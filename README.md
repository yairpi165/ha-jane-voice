# Jane — AI-Powered Smart Home Voice Assistant

A private, Hebrew-speaking voice assistant for smart home control. Built on GPT-4o Mini, Whisper STT, and OpenAI TTS, running on a Raspberry Pi 5 with Home Assistant.

## What Jane Does

- **Natural Hebrew conversation** — no fixed commands, just talk
- **Smart home control** — lights, AC, heater, shutters, TV, robot vacuum
- **Persistent memory** — learns preferences, remembers facts, detects patterns
- **Corrections learning** — make a mistake once, never again
- **Custom routines** — "goodnight" triggers a full sequence

## Architecture

```
Browser/Phone mic → HA Dashboard Card → Jane API (FastAPI)
                                            │
                              Whisper STT → GPT-4o Mini → TTS
                                    │              │
                              Memory (MD)    Home Assistant
                              7 files         device control
```

## Project Structure

```
jane/
├── src/                    # All Python source code
│   ├── brain.py            # LLM integration — think + execute
│   ├── ha_client.py        # Home Assistant REST API client
│   ├── memory.py           # LLM-managed markdown memory system
│   ├── voice.py            # Local mic recording + STT (CLI mode)
│   ├── jane.py             # CLI entry point
│   └── web_api.py          # FastAPI endpoint for dashboard
│
├── config/
│   ├── config_dev.py       # Dev config — loads .env
│   └── config_addon.py     # Addon config — SUPERVISOR_TOKEN
│
├── addon/                  # HAOS addon packaging (Docker)
├── dashboard/              # HA Lovelace custom card
├── docs/                   # PRD + architecture docs
├── deploy.sh               # Copies src → addon for deployment
└── requirements.txt
```

## Setup

### Prerequisites
- Raspberry Pi 5 running Home Assistant OS
- OpenAI API key
- Samba share addon on HA

### Local Development
```bash
cp .env.example .env       # Add your API keys
pip install -r requirements.txt
cd src && python jane.py   # CLI mode with local mic
```

### Dashboard Mode (HA Addon)
```bash
./deploy.sh                # Copy source to addon/
# Then via Samba: copy addon/ → addons/jane/ on the Pi
# HA → Settings → Add-ons → Jane → Install → Configure API key → Start
```

The dashboard card is registered as an inline JS resource in HA. Press the mic button on the dashboard to talk to Jane.

### Environment Variables
```
OPENAI_API_KEY=sk-...      # Required
HA_URL=http://homeassistant.local:8123
HA_TOKEN=...               # Long-lived access token (dev mode)
```

## Memory System

Jane uses LLM-managed markdown files for memory. GPT reads, consolidates, and rewrites them — no code-side dedup or scoring.

| File | Purpose | Managed by |
|------|---------|-----------|
| `users/{name}.md` | Personal preferences, facts | GPT |
| `family.md` | Household rules, events | GPT |
| `habits.md` | Recurring patterns | GPT |
| `corrections.md` | Learned mistakes | GPT |
| `routines.md` | Command sequences | GPT |
| `actions.md` | Rolling 24h action log | Code |
| `home.md` | Device map from HA | Code |

Memory content is stored in English for LLM precision. Conversations remain in Hebrew.

See [docs/MEMORY_ARCHITECTURE.md](docs/MEMORY_ARCHITECTURE.md) for full details.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| LLM | OpenAI GPT-4o Mini |
| STT | OpenAI Whisper |
| TTS | OpenAI TTS (nova voice) |
| Smart Home | Home Assistant (REST API) |
| Web API | FastAPI + Uvicorn |
| Server | Raspberry Pi 5 (HAOS) |
| Dashboard | Custom Lovelace card |
| Language | Python 3 |

## Roadmap

See [docs/JANE_PRD.md](docs/JANE_PRD.md) for the full roadmap. Current status:

- [x] V1 — Voice pipeline + HA control
- [x] Dashboard — Browser-based voice input via HA card
- [ ] V2 — Memory system (in progress)
- [ ] V2 — MCP Server integration
- [ ] V2 — Web search (Tavily)
- [ ] V3 — Atom EchoS3R satellite + wake word
