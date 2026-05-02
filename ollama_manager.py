"""
SILTA — Ollama manager
Lightweight proxy to native Ollama REST APIs.
No custom logic for model management: uses what Ollama already exposes.

Ollama Endpoints used:
  GET  /api/tags    → list installed models
  POST /api/pull    → download model with progress streaming
  DELETE /api/delete → remove model
  GET  /api/ps      → models loaded in RAM
"""

from __future__ import annotations
import asyncio
import json
from typing import AsyncGenerator

import aiohttp

OLLAMA_BASE = "http://localhost:11434"
PROBE_TIMEOUT = aiohttp.ClientTimeout(total=3)
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30)


async def is_running() -> bool:
    """Returns True if Ollama responds on localhost:11434."""
    try:
        async with aiohttp.ClientSession(timeout=PROBE_TIMEOUT) as s:
            async with s.get(f"{OLLAMA_BASE}/api/tags") as r:
                return r.status < 400
    except Exception:
        return False


async def list_models() -> list[dict]:
    """
    Returns a list of installed models.
    Each element: {"name": str, "size": int, "modified_at": str}
    """
    async with aiohttp.ClientSession(timeout=DEFAULT_TIMEOUT) as s:
        async with s.get(f"{OLLAMA_BASE}/api/tags") as r:
            data = await r.json()
            return data.get("models", [])


async def list_running() -> list[dict]:
    """Models currently loaded in RAM (GET /api/ps)."""
    try:
        async with aiohttp.ClientSession(timeout=PROBE_TIMEOUT) as s:
            async with s.get(f"{OLLAMA_BASE}/api/ps") as r:
                data = await r.json()
                return data.get("models", [])
    except Exception:
        return []


async def pull_model(name: str) -> AsyncGenerator[dict, None]:
    """
    Downloads a model with progress streaming.
    Each chunk: {"status": str, "completed": int, "total": int, "done": bool}
    """
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3600)) as s:
        async with s.post(
            f"{OLLAMA_BASE}/api/pull",
            json={"name": name, "stream": True},
        ) as r:
            async for line in r.content:
                line = line.strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    yield chunk
                    if chunk.get("status") == "success":
                        break
                except json.JSONDecodeError:
                    continue


async def delete_model(name: str) -> bool:
    """Removes a model. Returns True if successful."""
    try:
        async with aiohttp.ClientSession(timeout=DEFAULT_TIMEOUT) as s:
            async with s.delete(
                f"{OLLAMA_BASE}/api/delete",
                json={"name": name},
            ) as r:
                return r.status < 400
    except Exception:
        return False


# Suggested models from the LocalAI installer (table defined in Chat 3 briefing)
SUGGESTED_MODELS = [
    {
        "id": "gemma2:2b",
        "label": "Minimal (default)",
        "size": "1.7 GB",
        "note": "For any PC",
        "default": True,
    },
    {
        "id": "gemma2:9b",
        "label": "Standard",
        "size": "6 GB",
        "note": "Native tool calling",
        "default": False,
    },
    {
        "id": "qwen2.5:7b",
        "label": "Balanced",
        "size": "5 GB",
        "note": "Excellent multilingual support",
        "default": False,
    },
]
