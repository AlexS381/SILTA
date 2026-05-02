# SILTA
**Shell Intelligence Linux Terminal Agent**

> *Silta* — bridge in Finnish. Like Linux, like Linus Torvalds.

SILTA is an intelligent bridge between you and your Linux system. Describe what you want to do in natural language — SILTA translates it into commands, executes them, and tells you what happened.

```
"install vlc"                → apt install vlc → ✓ installed
"my microphone doesn't work" → diagnose → fix → verify
"update the system"          → apt upgrade → concise report
```

> ⚠️ **Alpha concept — not for production use.**
> This is a working proof of concept under active development.
> Use it to explore and test. If something breaks, that's on you.

---

## How it works

SILTA is a **simple bridge**. It contains no application logic — it transports messages between who speaks (you) and who knows (the AI), and between who decides (the AI) and who executes (Linux). All intelligence lives in the AI.

```
[ You ]  ←→  [ SILTA bridge ]  ←→  [ AI ]
                    ↕
              [ Local Linux ]
```

The AI receives your system context (distro, kernel, hardware, packages) and autonomously decides how to solve the problem. The bridge executes the commands and reports the output.

---

## Requirements

Before you start, make sure you have the following installed on your Linux system:

**Python 3.10 or higher**
```bash
python3 --version
```
If not installed:
```bash
# Ubuntu / Debian / Mint
sudo apt install python3 python3-venv python3-pip

# Fedora
sudo dnf install python3 python3-pip

# Arch
sudo pacman -S python python-pip
```

**Git**
```bash
# Ubuntu / Debian / Mint
sudo apt install git

# Fedora
sudo dnf install git

# Arch
sudo pacman -S git
```

**An AI provider — at least one of:**
- [Claude API key](https://console.anthropic.com/) (Anthropic) — cloud
- [OpenAI API key](https://platform.openai.com/) — cloud
- [Ollama](https://ollama.com/) installed locally — local, free, private
- [LM Studio](https://lmstudio.ai/) installed locally — local, free, private
  *(tested with LM Studio 0.4.12 on Linux — local AI)*

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/AlexS381/SILTA.git
cd SILTA

# 2. Make the start script executable
chmod +x start_test.sh

# 3. Run SILTA
./start_test.sh
```

The script will automatically:
- Create a Python virtual environment (`venv/`)
- Install all required dependencies
- Start the SILTA bridge on `http://localhost:7842`
- Open your browser

**On subsequent runs:**
```bash
./start_test.sh
```
Just run the script again — it handles everything.

**To stop SILTA:** press `Ctrl+C` in the terminal. The virtual environment is automatically cleaned up on exit, ensuring a fresh start next time.

---

## First run

1. Open `http://localhost:7842` in your browser
2. Go to the **Config** tab
3. Add your AI provider (API key for cloud, or auto-detected if Ollama/LM Studio is running)
4. Start chatting

---

## Supported AI providers

| Provider | Type | Notes |
|---|---|---|
| **Claude** (Anthropic) | ☁️ Cloud | Requires API key |
| **OpenAI** / ChatGPT | ☁️ Cloud | Requires API key |
| **Ollama** | 💻 Local | Auto-detected if running |
| **LM Studio** | 💻 Local | Auto-detected if running |
| Any OpenAI-compatible | ☁️/💻 | Configure custom endpoint |
*(tested with LM Studio 0.4.12 on Linux — local AI)*

---

## Features

- **Natural language chat** — any language
- **Real command execution** — via pseudo-terminal, streaming output
- **Native sudo authentication** — fingerprint, PIN, password
- **Console tab** — see exactly what the AI is doing under the hood
- **Persistent shell mode** — sudo authenticates once per session (opt-in)
- **Security** — localhost-only by default, encrypted API keys, connection badge

---

## Security

- Server binds **exclusively to `127.0.0.1`** — not reachable from the network by default
- API keys are encrypted at rest
- Always-visible badge: **Local** / **Cloud** · **🔒 Encrypted** / **⚠️ Unencrypted**

---

## Roadmap

- [ ] Session logs with automatic compression
- [ ] Granular rollback of changes
- [ ] Software Update Manager (SUM)
- [ ] Automatic post-execution verification
- [ ] `install-localai.sh` — one-command installer with Ollama included
- [ ] SSH support for remote machines
- [ ] Voice input (Web Speech API)
- [ ] User profiles: Novice / Standard / Advanced

---

## Contributing

Issues and pull requests are welcome.
This is an open concept — if you have ideas, open a discussion.

---

## License

MIT — see [LICENSE](LICENSE) for details.

---

## Author

**AlexS381** — [github.com/AlexS381](https://github.com/AlexS381)
