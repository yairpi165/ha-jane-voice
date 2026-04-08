# Jane — AI-Powered Smart Home Voice Assistant

A private, Hebrew-speaking voice assistant for smart home control. Built on GPT-4o Mini with Home Assistant integration, running on a Raspberry Pi 5.

## What Jane Does

- **Natural Hebrew conversation** — no fixed commands, just talk
- **Smart home control** — lights, AC, heater, shutters, TV, robot vacuum
- **Autonomous decision making** — GPT decides what tools to use (device control, weather, web search)
- **Persistent memory** — learns preferences, remembers facts, detects patterns
- **Corrections learning** — make a mistake once, never again
- **Multi-turn conversations** — understands context ("turn it off" after "turn on the light")
- **Custom wake word** — "Hey Jane" trained microWakeWord model
- **Works everywhere** — Companion App, Safari, Chrome, tablets, satellites

## Architecture

Jane is a **custom HA conversation agent** with autonomous tool calling:

```
"Hey Jane" wake word / Assist button
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
  ┌─────────────────┐
  │     Jane         │  ← Conversation Agent (custom_component)
  │   brain.py       │
  │                   │──→ GPT-4o Mini (function calling)
  │   tools:          │      │
  │   - get_entity    │      ├→ get_entity_state (check devices)
  │   - call_service  │      ├→ call_ha_service (control + forecasts)
  │   - search_web    │      └→ search_web (Tavily, when needed)
  │                   │
  │   memory:         │──→ 7 markdown files (GPT-managed)
  └─────────┬─────────┘
            │ response text
            ▼
  ┌─────────┴─────┐
  │  OpenAI TTS   │  ← voice: nova
  └─────────┬─────┘
            │ audio
            ▼
      Speaker / Phone
```

## Project Structure

```
jane/
├── custom_components/
│   └── jane_conversation/      # HA custom integration (v2.0.0)
│       ├── __init__.py         # Setup + agent registration
│       ├── manifest.json       # Integration metadata
│       ├── config_flow.py      # UI config (OpenAI + Tavily keys)
│       ├── conversation.py     # ConversationEntity + session history
│       ├── brain.py            # GPT function calling loop
│       ├── tools.py            # Tool definitions + execution handlers
│       ├── web_search.py       # Tavily REST wrapper
│       ├── memory.py           # 7 memory files, extraction, action log
│       ├── const.py            # Constants + system prompt
│       └── strings.json        # UI translations
│
├── docs/
│   ├── MEMORY_ARCHITECTURE.md  # Memory system design
│   ├── TOOL_CALLING_ARCHITECTURE.md  # Tool calling + home manager vision
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
- Tavily API key (optional — enables web search)
- Samba share add-on on HA

### Installation
1. **Copy** `custom_components/jane_conversation/` to Pi via Samba: `config/custom_components/jane_conversation/`
2. **Install via HACS:**
   - "OpenAI Whisper STT API" (Speech-to-Text)
   - "OpenAI TTS" (Text-to-Speech)
   - "Voice Satellite Card" (optional — wake word support)
3. **Restart HA**
4. **Add integrations** (Settings → Integrations → Add):
   - OpenAI Whisper STT → API key, whisper-1
   - OpenAI TTS → API key, create profile "jane" with voice nova
   - Jane Voice Assistant → OpenAI API key + optional Tavily key
5. **Create Voice Assistant** (Settings → Voice Assistants → Add):
   - Conversation Agent: **Jane**
   - STT: **OpenAI Whisper**
   - TTS: **OpenAI TTS (jane)**
   - Language: **Hebrew**
6. Press **Assist** button → talk to Jane

### Optional: Wake Word
Custom "Hey Jane" wake word model trained with [microWakeWord Trainer](https://github.com/TaterTotterson/microWakeWord-Trainer-AppleSilicon). Copy `hey_jane.tflite` + `hey_jane.json` to Voice Satellite Card's models directory.

## Tool Calling

Jane uses OpenAI function calling — GPT decides autonomously what tools to use:

| Tool | What it does | Example |
|------|-------------|---------|
| `get_entity_state` | Read any HA entity | "כמה מעלות?" → checks weather entity |
| `call_ha_service` | Control devices + get data | "תדליקי אור" / "מה מזג האוויר מחר?" |
| `search_web` | Tavily web search | "מה שער הדולר?" (only when HA doesn't have the info) |

See [docs/TOOL_CALLING_ARCHITECTURE.md](docs/TOOL_CALLING_ARCHITECTURE.md) for full design.

## Memory System

7 LLM-managed markdown files. GPT reads, consolidates, and rewrites them.

| File | Purpose | Managed by |
|------|---------|-----------|
| `users/{name}.md` | Personal preferences, facts | GPT |
| `family.md` | Household rules, events | GPT |
| `habits.md` | Recurring patterns | GPT |
| `corrections.md` | Learned mistakes | GPT |
| `routines.md` | Command sequences | GPT |
| `actions.md` | Rolling 24h action log | Code |
| `home.md` | Device map by room | GPT (first run) |

See [docs/MEMORY_ARCHITECTURE.md](docs/MEMORY_ARCHITECTURE.md) for full design.

## Context Layers

| Layer | Storage | Lifetime | Example |
|-------|---------|----------|---------|
| Session history | RAM | Until session ends | "turn **it** off" → knows what "it" is |
| Action log | `actions.md` | 24 hours | "You turned on the light 5 min ago" |
| Personal memory | `users/*.md` | Permanent | "Prefers dim lights in the evening" |

## Tech Stack

| Layer | Technology |
|-------|-----------|
| LLM | OpenAI GPT-4o Mini (function calling) |
| STT | OpenAI Whisper (via HACS) |
| TTS | OpenAI TTS, voice nova (via HACS) |
| Smart Home | Home Assistant (native `hass.services`) |
| Web Search | Tavily API (optional) |
| Integration | Custom conversation agent (`custom_component`) |
| Wake Word | microWakeWord "Hey Jane" + Voice Satellite Card |
| Server | Raspberry Pi 5 (HAOS) |

## Vision

Jane evolves from a voice remote control to an intelligent home manager:

```
Today:     "turn on the light"           → executes command
           "what's the weather?"         → fetches from HA autonomously
           "what's the dollar rate?"     → searches web autonomously
Next:      "create an automation for..." → creates HA automations
Future:    "I noticed you dim lights     → suggests automation proactively
            every evening — want me
            to create an automation?"
```

See [docs/ROADMAP.md](docs/ROADMAP.md) for the full roadmap.
