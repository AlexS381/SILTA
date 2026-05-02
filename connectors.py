"""
SILTA — Side X Connectors
Interface between the bridge and any AI (local or cloud).

AnthropicConnector   → Anthropic API (Claude) with native streaming
OpenAICompatibleConnector → OpenAI, Ollama, LM Studio, any OpenAI-compatible provider

Adding a new provider = adding one line in config. No new code required.
"""

from __future__ import annotations
import json
import asyncio
from abc import ABC, abstractmethod
from typing import AsyncGenerator


# ── Base Interface ──────────────────────────────────────────────────────────

class BaseConnector(ABC):
    """Contract that every Side X connector must respect."""

    @abstractmethod
    async def stream(
        self,
        messages: list[dict],
        system: str = "",
        tools: list[dict] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """
        Generates streaming response chunks.
        Each chunk is a dict:
          {"type": "text",       "delta": "..."}
          {"type": "tool_use",   "name": "...", "input": {...}}
          {"type": "tool_result","content": "..."}
          {"type": "done",       "stop_reason": "..."}
          {"type": "error",      "message": "..."}
        """
        ...

    @abstractmethod
    async def ping(self) -> bool:
        """Verifies that the provider is reachable."""
        ...

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """String identifier of the provider (e.g., 'anthropic', 'ollama')."""
        ...

    @property
    @abstractmethod
    def is_local(self) -> bool:
        """True if the model runs locally (no data leaves the PC)."""
        ...


# ── Anthropic Connector ────────────────────────────────────────────────────────

class AnthropicConnector(BaseConnector):
    """
    Uses Anthropic Python SDK with native streaming.
    Supports tool calling (tool_use / tool_result).
    """

    DEFAULT_MODEL = "claude-opus-4-5"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        self._api_key = api_key
        self.model = model
        self._client = None  # initialized lazily

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
            except ImportError:
                raise RuntimeError("anthropic package not installed. Run: pip install anthropic")
        return self._client

    @property
    def provider_id(self) -> str:
        return "anthropic"

    @property
    def is_local(self) -> bool:
        return False

    async def ping(self) -> bool:
        try:
            client = self._get_client()
            # Minimal message to check connectivity and API key
            await client.messages.create(
                model=self.model,
                max_tokens=1,
                messages=[{"role": "user", "content": "ping"}],
            )
            return True
        except Exception:
            return False

    async def stream(
        self,
        messages: list[dict],
        system: str = "",
        tools: list[dict] | None = None,
    ) -> AsyncGenerator[dict, None]:
        client = self._get_client()

        kwargs: dict = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        try:
            async with client.messages.stream(**kwargs) as stream_ctx:
                async for event in stream_ctx:
                    etype = type(event).__name__

                    # Text streaming
                    if etype == "RawContentBlockDeltaEvent":
                        delta = event.delta
                        if hasattr(delta, "text"):
                            yield {"type": "text", "delta": delta.text}

                    # Tool use start
                    elif etype == "RawContentBlockStartEvent":
                        block = event.content_block
                        if hasattr(block, "type") and block.type == "tool_use":
                            yield {
                                "type": "tool_use_start",
                                "id": block.id,
                                "name": block.name,
                            }

                    # Tool use input JSON delta accumulation
                    elif etype == "RawContentBlockDeltaEvent":
                        delta = event.delta
                        if hasattr(delta, "partial_json"):
                            yield {"type": "tool_input_delta", "delta": delta.partial_json}

                    # Message end
                    elif etype == "RawMessageStopEvent":
                        final = await stream_ctx.get_final_message()
                        yield {
                            "type": "done",
                            "stop_reason": final.stop_reason,
                            "usage": {
                                "input_tokens": final.usage.input_tokens,
                                "output_tokens": final.usage.output_tokens,
                            },
                        }

        except Exception as e:
            yield {"type": "error", "message": str(e)}


# ── OpenAI-Compatible Connector ───────────────────────────────────────────────

class OpenAICompatibleConnector(BaseConnector):
    """
    Covers: OpenAI (cloud), Ollama (local), LM Studio (local),
    any provider with OpenAI compatible API.

    Adding a new provider = adding one line in config. No new code required.
    """

    KNOWN_ENDPOINTS = {
        "openai":    "https://api.openai.com/v1",
        "ollama":    "http://localhost:11434/v1",
        "lmstudio":  "http://localhost:1234/v1",
    }

    def __init__(
        self,
        provider: str = "ollama",
        model: str = "gemma2:2b",
        api_key: str = "ollama",          # Ollama/LM Studio ignore the key
        base_url: str | None = None,
    ):
        self._provider = provider
        self.model = model
        self._api_key = api_key
        self._base_url = base_url or self.KNOWN_ENDPOINTS.get(provider, "http://localhost:11434/v1")
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI
                self._client = AsyncOpenAI(
                    api_key=self._api_key,
                    base_url=self._base_url,
                )
            except ImportError:
                raise RuntimeError("openai package not installed. Run: pip install openai")
        return self._client

    @property
    def provider_id(self) -> str:
        return self._provider

    @property
    def is_local(self) -> bool:
        return "localhost" in self._base_url or "127.0.0.1" in self._base_url

    async def ping(self) -> bool:
        import aiohttp
        try:
            # For Ollama we use the native API; for others /models
            if self._provider == "ollama":
                url = self._base_url.replace("/v1", "") + "/api/tags"
            else:
                url = self._base_url + "/models"

            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    return resp.status < 400
        except Exception:
            return False

    async def stream(
        self,
        messages: list[dict],
        system: str = "",
        tools: list[dict] | None = None,
    ) -> AsyncGenerator[dict, None]:
        client = self._get_client()

        # OpenAI uses "system" as the first message with role=system
        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        kwargs: dict = {
            "model": self.model,
            "messages": full_messages,
            "stream": True,
            "max_tokens": 4096,
        }
        if tools:
            kwargs["tools"] = tools

        try:
            async with await client.chat.completions.create(**kwargs) as stream_ctx:
                async for chunk in stream_ctx:
                    choice = chunk.choices[0] if chunk.choices else None
                    if not choice:
                        continue

                    delta = choice.delta

                    # Text content
                    if delta.content:
                        yield {"type": "text", "delta": delta.content}

                    # Tool call (OpenAI function calling)
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            if tc.function.name:
                                yield {
                                    "type": "tool_use_start",
                                    "id": tc.id or "",
                                    "name": tc.function.name,
                                }
                            if tc.function.arguments:
                                yield {
                                    "type": "tool_input_delta",
                                    "delta": tc.function.arguments,
                                }

                    # End of message
                    if choice.finish_reason:
                        yield {"type": "done", "stop_reason": choice.finish_reason}

        except Exception as e:
            yield {"type": "error", "message": str(e)}


# ── Factory ───────────────────────────────────────────────────────────────────

def connector_from_config(cfg: dict) -> BaseConnector:
    """
    Creates the correct connector from the configuration dictionary.

    Config examples:
      {"provider": "anthropic", "api_key": "sk-ant-...", "model": "claude-opus-4-5"}
      {"provider": "ollama",    "model": "gemma2:2b"}
      {"provider": "lmstudio",  "model": "local-model"}
      {"provider": "openai",    "api_key": "sk-...", "model": "gpt-4o"}
    """
    provider = cfg.get("provider", "ollama")

    if provider == "anthropic":
        return AnthropicConnector(
            api_key=cfg["api_key"],
            model=cfg.get("model", AnthropicConnector.DEFAULT_MODEL),
        )

    return OpenAICompatibleConnector(
        provider=provider,
        model=cfg.get("model", "gemma2:2b"),
        api_key=cfg.get("api_key", "ollama"),
        base_url=cfg.get("base_url"),
    )
