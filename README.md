# Jane — AI-Powered Smart Home Voice Assistant

A private, Hebrew-speaking voice assistant for smart home control. Built on Gemini 2.5 Pro + Flash with Home Assistant integration, running on a Raspberry Pi 5.

## What Jane Does

- **Natural Hebrew conversation** — warm, curious personality. Not a robot — part of the family.
- **14 autonomous tools** — device control, discovery, notifications, timers, lists, history, and more
- **Creates automations** — "turn on heating every morning at 7" → Jane builds the automation herself
- **Persistent memory** — learns preferences, remembers family, detects patterns
- **Firebase backup** — memory backed up to Firestore, restored on SD failure
- **Custom wake word** — "Hey Jane" trained microWakeWord model
- **Night mode** — quieter, shorter responses between 23:00–07:00
- **Whisper hallucination filter** — catches phantom phrases from silence

## Architecture

```
"Hey Jane" / Assist button
        │
        ▼
HA Voice Pipeline
        │
  ┌─────┴─────┐
  │  Whisper   │  ← STT (gpt-4o-mini-transcribe + Hebrew hints)
  │   Cloud    │
  └─────┬──────┘
        │ text
        ▼
  ┌──────────────────────┐
  │        Jane           │  ← Conversation Agent (custom_component)
  │      brain.py         │
  │                        │──→ Gemini 2.5 Pro/Flash (function calling)
  │  38 tools:             │      │
  │  ├─ get_entity_state   │      ├→ Device control
  │  ├─ call_ha_service    │      ├→ Entity search & discovery
  │  ├─ search_entities    │      ├→ History & statistics
  │  ├─ set_automation     │      ├→ Notifications & timers
  │  ├─ set_script         │      ├→ Shopping lists & calendars
  │  ├─ set_scene          │      ├→ TTS announcements
  │  ├─ check_people       │      ├→ Config Store API (automations/scripts/scenes)
  │  ├─ set_timer          │      ├→ Smart Routines (cache & reuse)
  │  ├─ manage_list        │      └→ Web search (Google Search)
  │  └─ ... 29 more        │
  │                        │
  │  memory:               │──→ 7 markdown files (Gemini-managed)
  │                        │──→ Firebase backup (Firestore)
  └────────────┬───────────┘
               │ response text
               ▼
  ┌────────────┴──────┐
  │   Gemini TTS      │  ← voice: callirrhoe (ha-gemini-tts)
  └────────────┬──────┘
               │ audio
               ▼
        Speaker / Phone
```

## Tools

| Category | Tool | What it does |
|----------|------|-------------|
| **Control** | `get_entity_state` | Check any device status |
| | `call_ha_service` | Control devices, get forecasts |
| **Discovery** | `search_entities` | Find devices by name/room |
| | `list_areas` | List rooms and their devices |
| | `get_history` | State change history |
| | `get_statistics` | Sensor min/max/average |
| | `get_logbook` | Recent home events |
| **Family** | `check_people` | Who's home |
| | `send_notification` | Push to phones |
| | `set_timer` | Countdown with notification |
| | `manage_list` | Shopping/todo lists |
| | `tts_announce` | Broadcast via speaker |
| **Config** | `set_automation` / `remove_automation` | Create/update/delete automations |
| | `set_script` / `remove_script` | Create/update/delete scripts |
| | `set_scene` / `remove_scene` | Create/update/delete scenes |
| | `list_config` | List all automations/scripts/scenes |
| **External** | `search_web` | Web search (Google Search) |

See [docs/TOOL_CALLING_ARCHITECTURE.md](docs/TOOL_CALLING_ARCHITECTURE.md) for details.

## Memory System

7 LLM-managed markdown files + Firebase write-through backup.

| File | Purpose | Managed by |
|------|---------|-----------|
| `users/{name}.md` | Personal preferences, facts | GPT |
| `family.md` | Household rules, events | GPT |
| `habits.md` | Recurring patterns | GPT |
| `corrections.md` | Learned mistakes | GPT |
| `routines.md` | Command sequences | GPT |
| `actions.md` | Rolling 24h action log | Code |
| `home.md` | Device map by room | GPT (first run) / Manual |

See [docs/MEMORY_ARCHITECTURE.md](docs/MEMORY_ARCHITECTURE.md) for details.

## Project Structure

```
jane/
├── custom_components/
│   └── jane_conversation/      # HA custom integration (v3.5.0)
│       ├── __init__.py         # Setup, Firebase init, restore
│       ├── manifest.json       # Integration metadata
│       ├── config_flow.py      # UI config (Gemini API key + Firebase)
│       ├── conversation.py     # ConversationEntity, sessions, hallucination filter
│       ├── brain.py            # Gemini 2.5 Pro/Flash with context injection + smart routines
│       ├── tools.py            # 14 tool definitions + execution handlers
│       ├── config_api.py       # Config Store API client (automations/scripts/scenes)
│       ├── web_search.py       # Google Search via Gemini
│       ├── memory.py           # 7 memory files, extraction, history log, home map
│       ├── firebase.py         # Firestore REST API backup
│       ├── const.py            # Constants + system prompt
│       └── strings.json        # UI translations
│
├── docs/
│   ├── MEMORY_ARCHITECTURE.md
│   ├── TOOL_CALLING_ARCHITECTURE.md
│   └── ROADMAP.md
│
├── tests/
│   ├── conftest.py              # Shared fixtures (mock hass, mock Gemini)
│   ├── test_brain.py            # Classification, context, text extraction
│   ├── test_tools.py            # YAML safety, tool format, routing
│   ├── test_ha_handlers.py      # All HA tool handlers
│   ├── test_memory.py           # Anti-repetition, file I/O, logs
│   ├── test_gemini_api.py       # History conversion, model selection, tool loop
│   ├── test_e2e.py              # Full conversation flows
│   └── test_conversation.py     # Hallucination filter
│
├── hacs.json
├── README.md
└── .gitignore
```

## Setup

### Prerequisites
- Raspberry Pi 5 running Home Assistant OS
- OpenAI API key
- Gemini TTS: Install [ha-gemini-tts](https://github.com/yairpi165/ha-gemini-tts) via HACS
- Firebase service account key (optional — enables memory backup)

### Installation via HACS
1. Add custom repository: `https://github.com/yairpi165/ha-jane-voice`
2. Install "Jane Voice Assistant"
3. Restart HA
4. Add integration: Settings → Integrations → Add → Jane Voice Assistant
5. Enter OpenAI API key
6. Optionally configure Firebase in Jane settings

### Voice Pipeline Setup
1. Install via HACS: "OpenAI Whisper STT API" + [ha-gemini-tts](https://github.com/yairpi165/ha-gemini-tts)
2. Create Voice Assistant: Settings → Voice Assistants → Add
   - Conversation Agent: **Jane**
   - STT: **OpenAI Whisper**
   - TTS: **Gemini TTS** (voice: callirrhoe)
   - Language: **Hebrew**

## Tech Stack

| Layer | Technology |
|-------|-----------|
| LLM | Gemini 2.5 Pro (complex) + 2.5 Flash (fast) — dual model, 38 tools |
| STT | gpt-4o-mini-transcribe (Hebrew prompt hints) |
| TTS | Gemini TTS, voice callirrhoe ([ha-gemini-tts](https://github.com/yairpi165/ha-gemini-tts)) |
| Smart Home | Home Assistant (native Python API) |
| Web Search | Google Search (built-in via Gemini) |
| Memory Backup | Firebase Firestore (optional) |
| Wake Word | microWakeWord "Hey Jane" + Voice Satellite Card |
| Server | Raspberry Pi 5 (HAOS) |

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md) for the full roadmap.

v3.5.0 highlights:
- **Smart Routines** — Jane caches multi-step commands as scripts/scenes (1 call instead of 6+)
- **Config Store API** — automation CRUD via HA REST API, no more YAML corruption
- **Dedicated config tools** — set/remove automation/script/scene (38 tools total)
- **Gemini TTS** — natural Hebrew voice via callirrhoe ([separate repo](https://github.com/yairpi165/ha-gemini-tts))
- **Local CLI** — `jane_cli.py` for testing against real HA without deploying

Next up:
- **Proactive behavior** — Jane speaks up when she notices something
- **Per-user personality** — different behavior per family member
