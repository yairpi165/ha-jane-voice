# Jane — AI-Powered Smart Home Voice Assistant

A private, Hebrew-speaking voice assistant for smart home control. Built on GPT-4o Mini with Home Assistant integration, running on a Raspberry Pi 5.

## What Jane Does

- **Natural Hebrew conversation** — no fixed commands, just talk
- **Smart home control** — lights, AC, heater, shutters, TV, robot vacuum
- **Persistent memory** — learns preferences, remembers facts, detects patterns
- **Corrections learning** — make a mistake once, never again
- **Custom routines** — "goodnight" triggers a full sequence
- **Multi-turn conversations** — understands context ("turn it off" after "turn on the light")

## Architecture

Jane is a **custom HA conversation agent** that integrates natively with the Assist pipeline:

```
Assist button / Satellite mic
        │
        ▼
HA Voice Pipeline
        │
  ┌─────┴─────┐
  │  Whisper   │  ← STT (cloud)
  │   STT      │
  └─────┬──────┘
        │ text
        ▼
  ┌────────────┐
  │   Jane     │  ← Conversation Agent (custom_component)
  │  brain.py  │──→ GPT-4o Mini ←→ Memory (7 MD files)
  │            │──→ hass.services (device control)
  └─────┬──────┘
        │ response text
        ▼
  ┌─────┴─────┐
  │  OpenAI   │  ← TTS
  │   TTS     │
  └─────┬─────┘
        │ audio
        ▼
  Speaker / Phone
```

Works everywhere: **Companion App (iPhone/Android), Safari, Chrome, and future Atom EchoS3R satellites.**

## Project Structure

```
jane/
├── custom_components/
│   └── jane_conversation/      # HA custom integration
│       ├── __init__.py         # Setup + agent registration
│       ├── manifest.json       # Integration metadata
│       ├── config_flow.py      # UI config (API key)
│       ├── conversation.py     # ConversationEntity + session history
│       ├── brain.py            # GPT think + HA service execution
│       ├── memory.py           # 7 memory files, extraction, action log
│       ├── const.py            # Constants + system prompt
│       └── strings.json        # UI translations
│
├── src/                        # Legacy CLI mode (local dev)
│   ├── brain.py
│   ├── voice.py
│   ├── jane.py
│   └── ...
│
├── docs/
│   ├── JANE_PRD.md
│   ├── MEMORY_ARCHITECTURE.md
│   └── ROADMAP.md
│
└── README.md
```

## Setup

### Prerequisites
- Raspberry Pi 5 running Home Assistant OS
- OpenAI API key
- Samba share add-on on HA

### Installation
1. **Copy** `custom_components/jane_conversation/` → Pi via Samba: `config/custom_components/jane_conversation/`
2. **Install STT** via HACS: "OpenAI Whisper STT API"
3. **Install TTS** via HACS: "OpenAI TTS"
4. **Restart HA**
5. **Add integrations** (Settings → Integrations → Add):
   - OpenAI Whisper STT → enter API key
   - OpenAI TTS → enter API key
   - Jane Voice Assistant → enter API key
6. **Create Voice Assistant** (Settings → Voice Assistants → Add):
   - Conversation Agent: **Jane**
   - STT: **OpenAI Whisper**
   - TTS: **OpenAI TTS** (or Home Assistant Cloud)
   - Language: **Hebrew**
7. Press **Assist** button → talk to Jane

### Local Development (CLI)
```bash
cp .env.example .env
pip install -r requirements.txt
cd src && python jane.py
```

## Memory System

Jane uses LLM-managed markdown files. GPT reads, consolidates, and rewrites them — no code-side dedup or scoring.

| File | Purpose | Managed by |
|------|---------|-----------|
| `users/{name}.md` | Personal preferences, facts | GPT |
| `family.md` | Household rules, events | GPT |
| `habits.md` | Recurring patterns | GPT |
| `corrections.md` | Learned mistakes | GPT |
| `routines.md` | Command sequences | GPT |
| `actions.md` | Rolling 24h action log | Code |
| `home.md` | Device map (GPT-organized by room) | GPT (on first run) |

Memory stored in English for LLM precision. Conversations remain in Hebrew.

See [docs/MEMORY_ARCHITECTURE.md](docs/MEMORY_ARCHITECTURE.md) for full details.

## Context Layers

| Layer | Storage | Lifetime | Example |
|-------|---------|----------|---------|
| Session history | RAM | Until session ends | "turn **it** off" → knows what "it" is |
| Action log | `actions.md` | 24 hours | "You turned on the light 5 min ago" |
| Personal memory | `users/*.md` | Permanent (GPT-managed) | "Prefers dim lights in the evening" |

## Tech Stack

| Layer | Technology |
|-------|-----------|
| LLM | OpenAI GPT-4o Mini |
| STT | OpenAI Whisper (via HACS integration) |
| TTS | OpenAI TTS / HA Cloud (via HACS integration) |
| Smart Home | Home Assistant (native `hass.services`) |
| Integration | Custom conversation agent (`custom_component`) |
| Server | Raspberry Pi 5 (HAOS) |
| Language | Python 3 |

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md) for the full prioritized list. Current status:

- [x] V1 — Voice pipeline + HA control
- [x] HA Integration — Custom conversation agent (Assist pipeline)
- [x] Memory system — 7 LLM-managed markdown files
- [x] Multi-turn conversations — session history
- [x] Auto user identification — from HA logged-in user
- [ ] Tavily web search — real-time info (weather, news, traffic)
- [ ] Concise responses — "done" for simple commands
- [ ] Night mode — quiet hours behavior
- [ ] Firebase backup — cloud memory persistence
- [ ] Atom EchoS3R satellite — wake word + Wyoming Protocol
