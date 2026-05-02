"""
SILTA — Bridge core (Chat 5)
Fixes from Chat 4:
  1. System prompt interpolated with real data (no longer literal placeholders)
  2. Provider probe on the first message — clear error if KO
  3. execute_command uses asynchronous ptyprocess (no fixed timeout)
  4. output_mode for execute_command — AI chooses full/errors_only
  5. Fix OpenAI tool_calls history — context maintained across rounds

The bridge is STUPID: it transports and dispatches. The AI decides everything.
The only exception: logging, rollback, and compression happen here because
they must work even without AI.
"""

from __future__ import annotations
import json
import asyncio
import os
import threading
import time
from typing import AsyncGenerator

from system_info import get_system_info
from connectors import BaseConnector, connector_from_config
from config_manager import get_active_provider_config, get_session_mode

# ── Persistent Bash Session ─────────────────────────────────────────────────

class PersistentShell:
    """
    A single bash shell that remains open for the entire SILTA session.
    sudo authenticates once — PAM cache persists between commands.

    Protocol:
      - Each command is delimited by a unique sentinel printed to stdout.
      - We read output until the sentinel to know when the command is finished.
      - returncode is extracted immediately after the command using `echo $?`.
    """

    SENTINEL = "__SILTA_CMD_DONE__"

    def __init__(self):
        self._proc = None
        self._lock = asyncio.Lock()
        self._alive = False

    def _spawn(self):
        """Spawns the bash shell via ptyprocess (blocking — must be called in executor)."""
        import ptyprocess
        self._proc = ptyprocess.PtyProcess.spawn(
            ["/bin/bash", "--norc", "--noprofile"],
            env={**os.environ, "TERM": "dumb", "PS1": ""},
        )
        # Wait for the shell to be ready (small pause)
        time.sleep(0.2)
        self._alive = True

    async def start(self):
        """Starts the persistent shell in background."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._spawn)

    def _write(self, text: str):
        if self._proc and self._proc.isalive():
            self._proc.write(text.encode())

    def _read_until_sentinel(self, sentinel: str, timeout: float = 300.0) -> str:
        """
        Reads output from the pty until the sentinel is found.
        Safety timeout (default 300s) — never blocking indefinitely.
        """
        buf = ""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._proc.isalive():
                self._alive = False
                break
            try:
                chunk = self._proc.read(4096)
                if chunk:
                    buf += chunk.decode(errors="replace")
                    if sentinel in buf:
                        break
            except EOFError:
                self._alive = False
                break
            except Exception:
                time.sleep(0.05)
        # Remove the sentinel and everything that follows
        if sentinel in buf:
            buf = buf[:buf.index(sentinel)]
        return buf

    async def run(self, cmd: str) -> tuple[int, str]:
        """
        Executes a command in the persistent shell.
        Returns (returncode, output).
        Falls back to _run_pty standard mode if the shell is dead.
        """
        if not self._alive or self._proc is None:
            # Shell died — fallback to standard mode
            return await _run_pty(cmd)

        async with self._lock:
            loop = asyncio.get_event_loop()

            def _blocking():
                # Send command + sentinel + returncode request
                full_cmd = (
                    f"{cmd}\n"
                    f"echo {self.SENTINEL}_RC_$?\n"
                )
                self._write(full_cmd)

                # Read until the end-of-command sentinel
                sentinel_full = f"{self.SENTINEL}_RC_"
                output = self._read_until_sentinel(sentinel_full)

                # Read the returncode (number on the sentinel line)
                rc_buf = ""
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    try:
                        chunk = self._proc.read(256)
                        if chunk:
                            rc_buf += chunk.decode(errors="replace")
                            if "\n" in rc_buf:
                                break
                    except Exception:
                        break

                try:
                    # Extract the return code
                    rc = int(rc_buf.strip().split("\n")[0].strip())
                except (ValueError, IndexError):
                    rc = 0

                # Remove command echo from the start of the output
                lines = output.splitlines()
                if lines and cmd.strip() in lines[0]:
                    lines = lines[1:]
                return rc, "\n".join(lines)

            try:
                return await loop.run_in_executor(None, _blocking)
            except Exception as e:
                # Safe fallback
                self._alive = False
                return 1, f"[Persistent shell KO, falling back to standard] {e}"

    async def close(self):
        """Closes the persistent shell."""
        self._alive = False
        if self._proc and self._proc.isalive():
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: self._proc.terminate(force=True))
        self._proc = None

    async def restart(self):
        """Closes and reopens the shell — used by clear_history()."""
        await self.close()
        await self.start()


# ── Base System Prompt ────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are SILTA, a Linux system management assistant.
You have tools (tool calls) to execute commands on the local system.
When executing commands, always read the output and provide the user with a concise and clear summary of what happened.
Before running long-running commands that might take time, notify the user.
Be concise and practical — show results, not descriptions.
Use tools when necessary — never simulate output.
If a command fails, analyze the error and propose alternatives.

User Profile: {user_profile}
System Info:
{system_info}"""


# ── Tool Definitions for AI ─────────────────────────────────────────────────────

TOOLS_ANTHROPIC = [
    {
        "name": "execute_command",
        "description": (
            "Executes a shell command on the local Linux system. "
            "output_mode='full' for informational commands (date, lscpu, sensors, ps, ls…). "
            "output_mode='errors_only' for long operational commands (apt, pip, make, rsync…)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cmd": {
                    "type": "string",
                    "description": "The command to execute"
                },
                "output_mode": {
                    "type": "string",
                    "enum": ["full", "errors_only"],
                    "description": (
                        "full: sends all output to the AI. "
                        "errors_only: sends only errors and returncode to the AI; if successful, returns 'Completed successfully'."
                    )
                }
            },
            "required": ["cmd", "output_mode"],
        },
    },
    {
        "name": "read_file",
        "description": "Reads the content of a file from the filesystem.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or relative path to the file"}
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Writes or overwrites a file on the filesystem.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "File path"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "get_system_info",
        "description": "Gathers complete system information: distro, kernel, CPU, memory, disk, packages.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

# OpenAI-compatible version (same logic, different format)
TOOLS_OPENAI = [
    {
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t["description"],
            "parameters": t["input_schema"],
        }
    }
    for t in TOOLS_ANTHROPIC
]


# ── Command Execution via ptyprocess ─────────────────────────────────────────

async def _run_pty(cmd: str) -> tuple[int, str]:
    """
    Executes a command in a pty and returns (returncode, full_output).
    No fixed timeout — the process runs until it terminates.
    sudo and PAM (fingerprint, password) work natively.
    """
    try:
        import ptyprocess
    except ImportError:
        # Fallback to asyncio subprocess if ptyprocess is not available
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        return proc.returncode or 0, stdout.decode(errors="replace")

    loop = asyncio.get_event_loop()
    output_chunks: list[str] = []

    def _blocking_run() -> int:
        proc = ptyprocess.PtyProcess.spawn(
            ["/bin/bash", "-c", cmd],
            env={**os.environ, "TERM": "dumb"},
        )
        while proc.isalive():
            try:
                chunk = proc.read(4096)
                if chunk:
                    output_chunks.append(chunk.decode(errors="replace"))
            except EOFError:
                break
            except Exception:
                break
        proc.wait()
        return proc.exitstatus or 0

    returncode = await loop.run_in_executor(None, _blocking_run)
    return returncode, "".join(output_chunks)


# ── Bridge ────────────────────────────────────────────────────────────────────

class Bridge:
    """
    Routing between Side X (user + AI) and Side Y (Linux).
    Maintains the conversation history in memory for the session.
    """

    MAX_FULL_OUTPUT   = 4000   # Max chars sent to AI in full mode
    MAX_CONTEXT_LEN   = 1500   # Max chars for read_file towards AI

    def __init__(self):
        self._connector: BaseConnector | None = None
        self._history: list[dict] = []
        self._tool_input_buffers: dict[str, str] = {}
        self._console_log: list[str] = []
        self._user_profile: str = "Standard"
        self._provider_verified: bool = False   # reset on every clear_history
        self._persistent_shell: PersistentShell | None = None
        self._session_mode: str = get_session_mode()  # read from config

    # ── Persistent Session Management ─────────────────────────────────────────

    async def _ensure_persistent_shell(self):
        """Starts the persistent shell if it's not already active."""
        if self._persistent_shell is None or not self._persistent_shell._alive:
            self._persistent_shell = PersistentShell()
            await self._persistent_shell.start()
            self._console_log.append("[Persistent shell started]")

    def reload_session_mode(self):
        """Reloads session_mode from config (called by UI after PUT)."""
        self._session_mode = get_session_mode()

    # ── Connector Management ───────────────────────────────────────────────────

    def _get_connector(self) -> BaseConnector:
        if self._connector is None:
            cfg = get_active_provider_config()
            self._connector = connector_from_config(cfg)
        return self._connector

    def reload_connector(self):
        """Resets the current connector and verification status."""
        self._connector = None
        self._provider_verified = False

    def clear_history(self):
        """Clears conversation history, logs, and resets state."""
        self._history = []
        self._console_log = []
        self._provider_verified = False
        # Reset persistent shell if active
        if self._persistent_shell is not None:
            import asyncio as _a
            try:
                loop = _a.get_event_loop()
                if loop.is_running():
                    loop.create_task(self._persistent_shell.restart())
                else:
                    loop.run_until_complete(self._persistent_shell.restart())
            except Exception:
                self._persistent_shell = None
            self._console_log.append("[Persistent shell restarted with new conversation]")

    # ── System Prompt with Real Data ───────────────────────────────────────────

    def _build_system_prompt(self) -> str:
        """Constructs the system prompt using real system data (not placeholders)."""
        try:
            info = get_system_info()
            system_summary = (
                f"Distro: {info['distro']['name']} {info['distro']['version']}\n"
                f"Kernel: {info['kernel']['release']}\n"
                f"CPU: {info['cpu']['model']} ({info['cpu']['cores']} cores)\n"
                f"RAM: {info['memory']['total_mb']} MB total, "
                f"{info['memory']['available_mb']} MB available\n"
                f"Package manager: {', '.join(info['packages'].keys()) or 'none detected'}"
            )
        except Exception as e:
            system_summary = f"(unable to read system info: {e})"

        return SYSTEM_PROMPT.format(
            user_profile=self._user_profile,
            system_info=system_summary,
        )

    # ── Tool Dispatcher ───────────────────────────────────────────────────────

    async def _dispatch_tool(self, name: str, input_data: dict) -> str:
        """Executes the tool requested by the AI and returns the result as a string."""

        if name == "execute_command":
            cmd = input_data.get("cmd", "")
            output_mode = input_data.get("output_mode", "full")

            # Detect current session mode (can be changed by UI)
            current_mode = get_session_mode()

            # Console log — always full
            self._console_log.append(f"\n--- CMD [{current_mode}]: {cmd} [mode={output_mode}] ---")

            if current_mode == "persistent":
                await self._ensure_persistent_shell()
                returncode, raw_output = await self._persistent_shell.run(cmd)
            else:
                returncode, raw_output = await _run_pty(cmd)

            # Raw full log
            self._console_log.append(raw_output[:8000] + ("…" if len(raw_output) > 8000 else ""))

            # What is sent to the AI depends on output_mode
            if returncode != 0:
                # Error — always returns detail to AI
                err_snippet = raw_output[-2000:] if len(raw_output) > 2000 else raw_output
                result = f"[ERROR] Code {returncode}:\n{err_snippet}"
            elif output_mode == "errors_only":
                result = "Completed successfully."
            else:
                # full mode
                if len(raw_output) > self.MAX_FULL_OUTPUT:
                    result = raw_output[:self.MAX_FULL_OUTPUT] + "\n… [output truncated]"
                else:
                    result = raw_output.strip() or "(no output)"

            return result

        elif name == "read_file":
            from pathlib import Path
            path = input_data.get("path", "")
            try:
                content = Path(path).read_text(errors="replace")
                log_snip = content[:4096] + ("…" if len(content) > 4096 else "")
                self._console_log.append(f"\n--- READ: {path} ---\n{log_snip}")
                if len(content) > self.MAX_CONTEXT_LEN:
                    return content[:self.MAX_CONTEXT_LEN] + "\n… [file truncated]"
                return content
            except Exception as e:
                err = f"[error] {e}"
                self._console_log.append(f"\n--- READ ERROR: {path} ---\n{err}")
                return err

        elif name == "write_file":
            from pathlib import Path
            path = input_data.get("path", "")
            content = input_data.get("content", "")
            try:
                p = Path(path)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content)
                self._console_log.append(f"\n--- WRITE: {path} ({len(content)} chars) ---")
                return f"File written: {path} ({len(content)} characters)"
            except Exception as e:
                err = f"[error] {e}"
                self._console_log.append(f"\n--- WRITE ERROR: {path} ---\n{err}")
                return err

        elif name == "get_system_info":
            result = json.dumps(get_system_info(), ensure_ascii=False, indent=2)
            self._console_log.append("\n--- SYSINFO ---\n" + result[:2000])
            return result

        return f"[error] Unknown tool: {name}"

    # ── Handle WebSocket Messages (backward compat) ─────────────────────────

    async def handle_message(self, message: str) -> dict:
        msg = message.strip().lower()
        if msg in ("sysinfo", "system_info", "info", "get_system_info"):
            return {"type": "tool_result", "tool": "get_system_info", "data": get_system_info()}
        return {
            "type": "echo",
            "original": message,
            "hint": "Available commands: sysinfo | Use SSE channel for AI",
        }

    # ── End-to-End AI Streaming ───────────────────────────────────────────────

    async def stream_ai(self, user_message: str) -> AsyncGenerator[dict, None]:
        """
        Sends the user message to the AI and streams the response.
        Manages the internal tool_use → tool_result loop.

        Chunk yielded:
          {"type": "text",        "delta": str}
          {"type": "tool_start",  "name": str}
          {"type": "tool_result", "name": str, "output": str}
          {"type": "done",        "stop_reason": str}
          {"type": "error",       "message": str}
        """
        connector = self._get_connector()
        provider = connector.provider_id

        # ── Probe provider on first message ─────────────────────────────────
        if not self._provider_verified:
            ok = await connector.ping()
            if not ok:
                yield {
                    "type": "error",
                    "message": (
                        "AI unreachable. "
                        "Check the configuration in the Config tab."
                    ),
                }
                return
            self._provider_verified = True

        # Add user message to history
        self._history.append({"role": "user", "content": user_message})

        # System prompt built with real data on first message
        system_prompt = self._build_system_prompt()

        tools = TOOLS_ANTHROPIC if provider == "anthropic" else TOOLS_OPENAI

        MAX_TOOL_ROUNDS = 8

        for _round in range(MAX_TOOL_ROUNDS):
            self._tool_input_buffers = {}
            pending_tool_id: str | None = None
            pending_tool_name: str | None = None
            tool_calls_this_round: list[dict] = []
            round_text = ""

            async for chunk in connector.stream(
                messages=self._history,
                system=system_prompt,
                tools=tools,
            ):
                ctype = chunk.get("type")

                if ctype == "text":
                    round_text += chunk["delta"]
                    yield chunk

                elif ctype == "tool_use_start":
                    pending_tool_id = chunk["id"]
                    pending_tool_name = chunk["name"]
                    self._tool_input_buffers[pending_tool_id] = ""
                    yield {"type": "tool_start", "name": pending_tool_name}

                elif ctype == "tool_input_delta":
                    if pending_tool_id:
                        self._tool_input_buffers[pending_tool_id] += chunk["delta"]

                elif ctype == "done":
                    stop_reason = chunk.get("stop_reason", "")

                    # Assemble complete tool calls
                    for tid, raw_json in self._tool_input_buffers.items():
                        name = pending_tool_name or "unknown"
                        try:
                            input_data = json.loads(raw_json) if raw_json else {}
                        except json.JSONDecodeError:
                            input_data = {}
                        tool_calls_this_round.append({
                            "id": tid,
                            "name": name,
                            "input": input_data,
                        })

                    # ── Save AI response to history ─────────────────────────
                    if provider == "anthropic":
                        content_blocks: list[dict] = []
                        if round_text:
                            content_blocks.append({"type": "text", "text": round_text})
                        for tc in tool_calls_this_round:
                            content_blocks.append({
                                "type": "tool_use",
                                "id": tc["id"],
                                "name": tc["name"],
                                "input": tc["input"],
                            })
                        self._history.append({"role": "assistant", "content": content_blocks})
                    else:
                        # OpenAI format with structured tool_calls
                        assistant_msg: dict = {
                            "role": "assistant",
                            "content": round_text,
                        }
                        if tool_calls_this_round:
                            assistant_msg["tool_calls"] = [
                                {
                                    "id": tc["id"],
                                    "type": "function",
                                    "function": {
                                        "name": tc["name"],
                                        "arguments": json.dumps(tc["input"]),
                                    },
                                }
                                for tc in tool_calls_this_round
                            ]
                        self._history.append(assistant_msg)

                    # ── End without tools ────────────────────────────────────
                    if stop_reason in ("end_turn", "stop") and not tool_calls_this_round:
                        yield {"type": "done", "stop_reason": stop_reason}
                        return

                    # ── Execute tool and re-insert into history ───────────────
                    if stop_reason == "tool_use" or tool_calls_this_round:
                        if provider == "anthropic":
                            tool_results_content: list[dict] = []
                            for tc in tool_calls_this_round:
                                output = await self._dispatch_tool(tc["name"], tc["input"])
                                yield {"type": "tool_result", "name": tc["name"], "output": output}
                                tool_results_content.append({
                                    "type": "tool_result",
                                    "tool_use_id": tc["id"],
                                    "content": output,
                                })
                            self._history.append({
                                "role": "user",
                                "content": tool_results_content,
                            })
                        else:
                            # OpenAI format tool results
                            for tc in tool_calls_this_round:
                                output = await self._dispatch_tool(tc["name"], tc["input"])
                                yield {"type": "tool_result", "name": tc["name"], "output": output}
                                self._history.append({
                                    "role": "tool",
                                    "tool_call_id": tc["id"],
                                    "name": tc["name"],
                                    "content": output,
                                })
                        break  # next round

                    yield {"type": "done", "stop_reason": stop_reason}
                    return

                elif ctype == "error":
                    yield chunk
                    return

        yield {"type": "error", "message": "Too many consecutive tool call rounds (max 8)"}

    # ── SSE wrapper ───────────────────────────────────────────────────────────

    async def stream_sse(self, message: str):
        """SSE for simple messages (backward compatible Step 1)."""
        result = await self.handle_message(message)
        import asyncio as _a
        text = json.dumps(result, ensure_ascii=False, indent=2)
        chunk_size = 64
        for i in range(0, len(text), chunk_size):
            yield f"data: {json.dumps({'delta': text[i:i+chunk_size]})}\n\n"
            await _a.sleep(0.01)
        yield f"data: {json.dumps({'done': True})}\n\n"

    async def stream_ai_sse(self, user_message: str):
        """SSE for AI messages — end-to-end streaming."""
        async for chunk in self.stream_ai(user_message):
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'stream_end'})}\n\n"

    # ── Console log ───────────────────────────────────────────────────────────

    def get_console_log(self, limit: int = 50) -> list[str]:
        """Returns the last N messages recorded in the console log."""
        return self._console_log[-limit:]
