# Jane — AI-Powered Smart Home Voice Assistant

A private, Hebrew-speaking voice assistant for smart home control. Built on GPT-4o Mini with Home Assistant integration, running on a Raspberry Pi 5.

## What Jane Does

- **Natural Hebrew conversation** — no fixed commands, just talk
- **Smart home control** — lights, AC, heater, shutters, TV, robot vacuum
- **Persistent memory** — learns preferences, remembers facts, detects patterns
- **Corrections learning** — make a mistake once, never again
- **Custom routines** — "goodnight" triggers a full sequence
- **Multi-turn conversations** — understands context ("turn it off" after "turn on the light")
- **Works everywhere** — Companion App, Safari, Chrome, tablets, future satellites

## Architecture

Jane is a **custom HA conversation agent** that integrates natively with the Assist pipeline:

```
Assist button / Voice Satellite Card / Atom satellite
        │
        ▼
HA Voice Pipeline
        │
  ┌─────┴─────┐
  │  Whisper   │  ← STT (OpenAI cloud)
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
  │   TTS     │  ← OpenAI TTS / HA Cloud
  └─────┬─────┘
        │ audio
        ▼
  Speaker / Phone
```

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
├── docs/
│   ├── JANE_PRD.md             # Product requirements document
│   ├── MEMORY_ARCHITECTURE.md  # Memory system design
│   └── ROADMAP.md              # Prioritized feature list
│
├── README.md
├── .env.example
└── .gitignore
```

## Setup

### Prerequisites
- Raspberry Pi 5 running Home Assistant OS
- OpenAI API key
- Samba share add-on on HA

### Installation
1. **Copy** `custom_components/jane_conversation/` to Pi via Samba: `config/custom_components/jane_conversation/`
2. **Install via HACS:**
   - "OpenAI Whisper STT API" (Speech-to-Text)
   - "OpenAI TTS" (Text-to-Speech)
   - "Voice Satellite Card" (optional — browser-based voice satellite with wake word)
3. **Restart HA**
4. **Add integrations** (Settings → Integrations → Add):
   - OpenAI Whisper STT → enter API key, select whisper-1
   - OpenAI TTS → enter API key
   - Jane Voice Assistant → enter API key
5. **Create Voice Assistant** (Settings → Voice Assistants → Add):
   - Conversation Agent: **Jane**
   - STT: **OpenAI Whisper**
   - TTS: **OpenAI TTS** (or Home Assistant Cloud)
   - Language: **Hebrew**
6. Press **Assist** button → talk to Jane

### Optional: Voice Satellite Card
For hands-free wake word support on tablets/browsers, install [Voice Satellite Card](https://github.com/jxlarrea/voice-satellite-card-integration) via HACS. It turns any browser into an always-listening satellite that uses Jane's pipeline.

### Optional: Custom Wake Word
Train a custom "Hey Jane" wake word using [microWakeWord Trainer for Apple Silicon](https://github.com/TaterTotterson/microWakeWord-Trainer-AppleSilicon), then drop the `.tflite` model into Voice Satellite Card's models directory.

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
Memory files are stored in `config/jane_memory/` on the Pi.

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
| STT | OpenAI Whisper (via HACS) |
| TTS | OpenAI TTS / HA Cloud (via HACS) |
| Smart Home | Home Assistant (native `hass.services`) |
| Integration | Custom conversation agent (`custom_component`) |
| Voice Input | HA Assist button / Voice Satellite Card / Wyoming satellites |
| Server | Raspberry Pi 5 (HAOS) |
| Language | Python 3 |

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md) for the full prioritized list.

- [x] Voice pipeline + HA control
- [x] Custom conversation agent (Assist pipeline)
- [x] Memory system — 7 LLM-managed markdown files
- [x] Multi-turn conversations — session history
- [x] Auto user identification — from HA logged-in user
- [x] Voice Satellite Card integration — browser-based satellite
- [x] Custom wake word training — "Hey Jane" microWakeWord
- [ ] Tavily web search — real-time info (weather, news, traffic)
- [ ] Concise responses — "done" for simple commands
- [ ] Night mode — quiet hours behavior
- [ ] Firebase backup — cloud memory persistence
- [ ] Voice recognition — speaker ID without asking
- [ ] Face recognition — presence-based context via Frigate
- [ ] Atom EchoS3R satellite — Wyoming Protocol
