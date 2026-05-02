# SILTA — Changelog

## [0.7.0-alpha] — 2026-05-02

### Changed
- Full codebase translated to English (comments, internal messages, system prompt)
- Previous Italian version archived as 0.6
- Console tab now auto-scrolls as new output arrives

---

## [0.6.0-alpha] — 2026-05-01

### Added
- Persistent bash session mode — sudo authenticates once per session (opt-in from UI)
- Toggle in Config tab to switch between Standard and Persistent shell mode
- `PUT /api/config/session_mode` endpoint
- Automatic fallback to standard mode if persistent shell dies unexpectedly

---

## [0.5.0-alpha] — 2026-05-01

### Fixed (manual fixes + various patches)
- System prompt now interpolated with real system data — distro, kernel, CPU, RAM, package manager
- Provider probe on first message — clear error in chat if AI unreachable
- execute_command migrated to asynchronous ptyprocess — no fixed timeout, sudo/PAM works natively
- output_mode parameter added to execute_command — AI chooses full/errors_only per command
- OpenAI tool_calls history fixed — LM Studio and Ollama now maintain tool call context across rounds
- User text no longer disappears from chat after sending
- `python main.py` now starts the server correctly

---

## [0.4.0-alpha] — 2026-04-30

### Added
- Console tab — raw output of executed commands visible in real time
- start_test.sh — automated test environment with virtual environment and auto-cleanup on exit

### Fixed
- Various UI and streaming fixes applied manually

---

## [0.3.0-alpha] — 2026-04-30

### Added
- AnthropicConnector — Claude API with native streaming
- OpenAICompatibleConnector — covers OpenAI, Ollama, LM Studio, any OpenAI-compatible provider
- Auto-discovery of local providers (Ollama on :11434, LM Studio on :1234)
- Ollama model management via native REST API (list, pull, delete)
- Local/Cloud badge always visible in header
- Encrypted API key storage
- Provider configuration from UI — no config files to edit

---

## [0.2.0-alpha] — 2026-04-29

### Added
- FastAPI bridge skeleton with WebSocket and SSE
- `get_system_info()` — reads distro, kernel, CPU, RAM, disk, installed packages
- `/api/sysinfo` endpoint
- System info panel in UI
- Basic HTML/JS frontend served directly by FastAPI

---

## [0.1.0-alpha] — 2026-04-29

### Added
- Project concept and architecture defined
- Repository initialized
- Core principle established: the bridge is simple — it transports. The AI decides everything.
