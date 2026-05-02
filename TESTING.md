# SILTA — Testing

## Test Environment

| Component | Details |
|---|---|
| **OS** | Linux |
| **SILTA version** | 0.7.0-alpha |
| **AI provider** | LM Studio 0.4.12 (local) |
| **Server port** | 1234 (default) |
| **Model** | openai/gpt-oss-20b |
| **Context window** | 20,000 tokens |

## Setup

LM Studio must be running with:
- Local server active on port `1234` (default)
- A model loaded and ready

SILTA auto-discovers LM Studio on startup — no manual configuration required.

## What has been tested

- [x] Bridge startup and WebSocket connection
- [x] LM Studio auto-discovery
- [x] System info injection into system prompt (distro, kernel, CPU, RAM, package manager)
- [x] Natural language to command translation
- [x] Command execution via pty (streaming output)
- [x] sudo authentication via PAM (fingerprint)
- [x] Persistent bash session mode (sudo authenticates once per session)
- [x] output_mode selection by AI (full / errors_only)
- [x] Tool call context maintained across rounds
- [x] Console tab with real-time output and auto-scroll
- [x] System update (`sudo apt update && sudo apt full-upgrade`)
- [x] Provider configuration from UI
- [x] New conversation reset (↺)

## What has NOT been tested yet

- [ ] Claude API (Anthropic) — cloud
- [ ] OpenAI API — cloud
- [ ] Ollama — local
- [ ] Multiple consecutive sessions
- [ ] Rollback functionality
- [ ] Software Update Manager (SUM)
- [ ] Voice input
- [ ] Remote SSH connection

## Known issues

- None at time of publishing (0.7.0-alpha)

## Notes

> This software is a working proof of concept.
> Testing has been performed on a single machine with a single local AI provider.
> Results may vary depending on the model, hardware, and Linux distribution.
