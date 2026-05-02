"""
SILTA — Config manager
Reads and writes ~/.silta/config.json.
API keys are encrypted using Fernet (key derived from system user UID).
All other configuration is managed via the web UI, never directly through files.
"""

from __future__ import annotations
import json
import os
import base64
import hashlib
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────

SILTA_DIR  = Path.home() / ".silta"
CONFIG_JSON = SILTA_DIR / "config.json"
CONFIG_CONF = SILTA_DIR / "config.conf"   # only PORT=7842

DEFAULT_PORT = 7842

DEFAULT_CONFIG: dict = {
    "active_provider": "ollama",
    "session_mode": "standard",   # "standard" | "persistent"
    "providers": {
        "ollama": {
            "provider": "ollama",
            "model": "gemma2:2b",
            "base_url": "http://localhost:11434/v1",
        }
    }
}

# ── API Key Encryption ─────────────────────────────────────────────────────────

def _derive_key() -> bytes:
    """
    Fernet key derived from the system user UID.
    Simple but sufficient for at-rest protection on a local machine.
    """
    uid = str(os.getuid()).encode()
    digest = hashlib.sha256(uid + b"silta-v1").digest()
    return base64.urlsafe_b64encode(digest)

def _fernet():
    try:
        from cryptography.fernet import Fernet
        return Fernet(_derive_key())
    except ImportError:
        # Fernet not available -> plaintext fallback with warning
        return None 

def encrypt_key(plaintext: str) -> str:
    f = _fernet()
    if f is None:
        return plaintext  # degraded mode
    return f.encrypt(plaintext.encode()).decode()

def decrypt_key(token: str) -> str:
    f = _fernet()
    if f is None:
        return token
    try:
        return f.decrypt(token.encode()).decode()
    except Exception:
        return token  # already plaintext (migration)


# ── Read / Write Operations ───────────────────────────────────────────────────

def _ensure_dir():
    SILTA_DIR.mkdir(parents=True, exist_ok=True)

def load_config() -> dict:
    _ensure_dir()
    if not CONFIG_JSON.exists():
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    try:
        return json.loads(CONFIG_JSON.read_text())
    except Exception:
        # Return defaults if config file is corrupt
        return DEFAULT_CONFIG.copy()

def save_config(cfg: dict) -> None:
    _ensure_dir()
    CONFIG_JSON.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))

def load_port() -> int:
    _ensure_dir()
    if CONFIG_CONF.exists():
        for line in CONFIG_CONF.read_text().splitlines():
            line = line.strip()
            if line.startswith("PORT="):
                try:
                    return int(line.split("=", 1)[1])
                except ValueError:
                    pass
    return DEFAULT_PORT

def save_port(port: int) -> None:
    _ensure_dir()
    CONFIG_CONF.write_text(f"PORT={port}\n")


# ── Public API used by the bridge ─────────────────────────────────────────────

def get_active_provider_config() -> dict:
    """Returns the config dictionary of the active provider (with decrypted API key)."""
    cfg = load_config()
    active = cfg.get("active_provider", "ollama")
    providers = cfg.get("providers", {})
    # Use default if active provider is somehow missing
    provider_cfg = providers.get(active, DEFAULT_CONFIG["providers"]["ollama"]).copy()

    # Decrypt API key if present
    if "api_key_enc" in provider_cfg:
        provider_cfg["api_key"] = decrypt_key(provider_cfg.pop("api_key_enc"))

    return provider_cfg

def set_active_provider(provider_id: str) -> None:
    """Sets the currently active AI provider."""
    cfg = load_config()
    cfg["active_provider"] = provider_id
    save_config(cfg)

def upsert_provider(provider_id: str, provider_cfg: dict) -> None:
    """
    Adds or updates a provider.
    The API key is encrypted before being saved if provided in plaintext.
    """
    cfg = load_config()
    providers = cfg.setdefault("providers", {})

    stored = provider_cfg.copy()
    if "api_key" in stored and stored["api_key"]:
        # Encrypt the key and replace it with the encrypted version
        stored["api_key_enc"] = encrypt_key(stored.pop("api_key"))
    elif "api_key" in stored:
        stored.pop("api_key")

    providers[provider_id] = stored
    save_config(cfg)

def remove_provider(provider_id: str) -> None:
    """Removes a configured AI provider."""
    cfg = load_config()
    cfg.get("providers", {}).pop(provider_id, None)
    if cfg.get("active_provider") == provider_id:
        remaining = list(cfg.get("providers", {}).keys())
        cfg["active_provider"] = remaining[0] if remaining else "ollama"
    save_config(cfg)

def get_all_providers() -> dict:
    """Returns all providers without showing the plaintext API key."""
    cfg = load_config()
    result = {}
    for pid, pcfg in cfg.get("providers", {}).items():
        # Exclude the encrypted key from public view
        safe = {k: v for k, v in pcfg.items() if k != "api_key_enc"}
        safe["has_key"] = "api_key_enc" in pcfg
        result[pid] = safe
    return result


def get_session_mode() -> str:
    """Returns the command execution mode: 'standard' | 'persistent'."""
    cfg = load_config()
    return cfg.get("session_mode", "standard")


def set_session_mode(mode: str) -> None:
    """Saves the command execution mode."""
    if mode not in ("standard", "persistent"):
        raise ValueError(f"Invalid session_mode: {mode}")
    cfg = load_config()
    cfg["session_mode"] = mode
    save_config(cfg)
