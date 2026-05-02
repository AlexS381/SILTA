"""
SILTA — Bridge core (Chat 3)
Extends the Chat 2 skeleton with:
  - Routing to Side X connectors (Anthropic / OpenAI-compatible)
  - Conversation session management (in-memory history)
  - Tool dispatch: execute_command, read_file, write_file, get_system_info, upload_file
  - End-to-end streaming: user message → AI → output in browser

The bridge is STUPID: it transports and dispatches. The AI decides everything.
The only exception: logging, rollback, and compression happen here because
they must work even without AI.
"""

import json
import asyncio
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse

from bridge import Bridge 
from system_info import get_system_info
from config_manager import (
    load_config, save_config,
    get_active_provider_config, get_all_providers,
    set_active_provider, upsert_provider, remove_provider,
    get_session_mode, set_session_mode,
)
import ollama_manager as ollama

app = FastAPI(title="SILTA Bridge", version="0.3.1-step3") # Incremented version
bridge = Bridge()

# ── Frontend ──────────────────────────────────────────────────────────────────

FRONTEND = Path(__file__).parent / "index.html"

@app.get("/", response_class=HTMLResponse)
async def root():
    return HTMLResponse(FRONTEND.read_text())


# ── WebSocket (Step 1 compatible) ────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    await ws.send_text(json.dumps({
        "type": "connected",
        "msg": "SILTA bridge connected. Use the chat to talk to the AI."
    }))
    try:
        while True:
            data = await ws.receive_text()
            result = await bridge.handle_message(data)
            await ws.send_text(json.dumps(result, ensure_ascii=False))
    except WebSocketDisconnect:
        pass


# ── SSE Step 1 compatible ──────────────────────────────────────────────────────

@app.get("/sse")
async def sse_endpoint(msg: str = ""):
    async def event_stream():
        async for chunk in bridge.stream_sse(msg):
            yield chunk
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Chat AI (SSE streaming) ───────────────────────────────────────────────────

@app.post("/api/chat")
async def chat_endpoint(request: Request):
    """
    Receives {"message": "..."} and responds in SSE streaming.
    The frontend consumes the events and updates the UI in real-time.
    """
    body = await request.json()
    user_message = body.get("message", "").strip()

    if not user_message:
        return JSONResponse({"error": "empty message"}, status_code=400)

    async def event_stream():
        async for chunk in bridge.stream_ai_sse(user_message):
            yield chunk

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── History ───────────────────────────────────────────────────────────────────

@app.delete("/api/history")
async def clear_history():
    bridge.clear_history()
    return {"status": "ok", "message": "History cleared"}

# ── NEW CONSOLE LOGGING ROUTE ───────────────────────────────────────────────

@app.get("/api/console_log")
async def get_console_log():
    """Endpoint to read the raw output of executed commands (Console Tab)."""
    return JSONResponse({
        "logs": bridge.get_console_log(),
    })

# ── System info (Step 1 compatible) ─────────────────────────────────────────

@app.get("/api/sysinfo")
async def api_sysinfo():
    return JSONResponse(get_system_info())


# ── Provider config ────────────────────────────────────────────────────────────

@app.get("/api/providers")
async def list_providers():
    cfg = load_config()
    return JSONResponse({
        "active": cfg.get("active_provider", "ollama"),
        "session_mode": cfg.get("session_mode", "standard"),
        "providers": get_all_providers(),
    })

@app.post("/api/providers")
async def add_provider(request: Request):
    body = await request.json()
    provider_id = body.get("id") or body.get("provider")
    if not provider_id:
        return JSONResponse({"error": "missing provider id"}, status_code=400)
    upsert_provider(provider_id, body)
    bridge.reload_connector()
    return JSONResponse({"status": "ok", "id": provider_id})

@app.put("/api/providers/active")
async def set_active(request: Request):
    body = await request.json()
    provider_id = body.get("provider")
    if not provider_id:
        return JSONResponse({"error": "missing provider"}, status_code=400)
    set_active_provider(provider_id)
    bridge.reload_connector()
    return JSONResponse({"status": "ok", "active": provider_id})

@app.delete("/api/providers/{provider_id}")
async def delete_provider(provider_id: str):
    remove_provider(provider_id)
    bridge.reload_connector()
    return JSONResponse({"status": "ok"})


# ── Session mode ───────────────────────────────────────────────────────────────

@app.put("/api/config/session_mode")
async def update_session_mode(request: Request):
    """
    Saves the command execution mode chosen from the UI.
    body: {"session_mode": "standard" | "persistent"}
    """
    body = await request.json()
    mode = body.get("session_mode", "standard")
    try:
        set_session_mode(mode)
        bridge.reload_session_mode()
        return JSONResponse({"status": "ok", "session_mode": mode})
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# ── Auto-discovery ─────────────────────────────────────────────────────────────

@app.get("/api/discovery")
async def discovery():
    """
    Automatic probe on localhost.
    Returns which local providers are reachable.
    """
    import aiohttp

    async def probe(url: str, timeout: int = 2) -> bool:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                    return r.status < 400
        except Exception:
            return False

    ollama_ok, lmstudio_ok = await asyncio.gather(
        probe("http://localhost:11434/api/tags"),
        probe("http://localhost:1234/v1/models"),
    )

    return JSONResponse({
        "ollama":   {"found": ollama_ok,   "url": "http://localhost:11434"},
        "lmstudio": {"found": lmstudio_ok, "url": "http://localhost:1234"},
    })


# ── Ollama model management ────────────────────────────────────────────────────

@app.get("/api/ollama/models")
async def ollama_models():
    if not await ollama.is_running():
        return JSONResponse({"error": "Ollama unreachable"}, status_code=503)
    models = await ollama.list_models()
    return JSONResponse({"models": models, "suggested": ollama.SUGGESTED_MODELS})

@app.get("/api/ollama/running")
async def ollama_running():
    running = await ollama.list_running()
    return JSONResponse({"running": running})

@app.post("/api/ollama/pull")
async def ollama_pull(request: Request):
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "missing model name"}, status_code=400)

    async def event_stream():
        async for chunk in ollama.pull_model(name):
            yield f"data: {json.dumps(chunk)}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

@app.delete("/api/ollama/models/{model_name:path}")
async def ollama_delete(model_name: str):
    name = unquote(model_name)
    ok = await ollama.delete_model(name)
    if ok:
        return JSONResponse({"status": "ok", "deleted": name})
    return JSONResponse({"error": f"Could not remove {name}"}, status_code=500)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    cfg = load_config()
    return {
        "status": "ok",
        "step": 3,
        "active_provider": cfg.get("active_provider", "ollama"),
    }

# ── Server start ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=7842)
