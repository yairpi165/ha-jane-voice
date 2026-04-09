# Jane — AI-Powered Smart Home Voice Assistant

A private, Hebrew-speaking voice assistant for smart home control. Built on GPT-5.4 Mini with Home Assistant integration, running on a Raspberry Pi 5.

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
  │                        │──→ GPT-5.4 Mini (function calling)
  │  14 tools:             │      │
  │  ├─ get_entity_state   │      ├→ Device control
  │  ├─ call_ha_service    │      ├→ Entity search & discovery
  │  ├─ search_entities    │      ├→ History & statistics
  │  ├─ get_history        │      ├→ Notifications & timers
  │  ├─ list_areas         │      ├→ Shopping lists
  │  ├─ send_notification  │      ├→ TTS announcements
  │  ├─ check_people       │      ├→ Automations/scenes/scripts
  │  ├─ set_timer          │      └→ Web search (Tavily)
  │  ├─ manage_list        │
  │  ├─ get_statistics     │
  │  ├─ get_logbook        │
  │  ├─ tts_announce       │
  │  ├─ ha_config_api      │
  │  └─ search_web         │
  │                        │
  │  memory:               │──→ 7 markdown files (GPT-managed)
  │                        │──→ Firebase backup (Firestore)
  └────────────┬───────────┘
               │ response text
               ▼
  ┌────────────┴──────┐
  │    OpenAI TTS     │  ← voice: nova
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
| **Create** | `ha_config_api` | Automations, scenes, scripts |
| **External** | `search_web` | Web search (Tavily) |

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
│   └── jane_conversation/      # HA custom integration (v3.2.0)
│       ├── __init__.py         # Setup, Firebase init, restore
│       ├── manifest.json       # Integration metadata
│       ├── config_flow.py      # UI config (OpenAI + Tavily + Firebase keys)
│       ├── conversation.py     # ConversationEntity, sessions, hallucination filter
│       ├── brain.py            # GPT-5.4 Mini with context injection + dynamic temperature
│       ├── tools.py            # 14 tool definitions + execution handlers
│       ├── web_search.py       # Tavily REST wrapper
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
├── hacs.json
├── README.md
└── .gitignore
```

## Setup

### Prerequisites
- Raspberry Pi 5 running Home Assistant OS
- OpenAI API key
- Tavily API key (optional — enables web search)
- Firebase service account key (optional — enables memory backup)

### Installation via HACS
1. Add custom repository: `https://github.com/yairpi165/ha-jane-voice`
2. Install "Jane Voice Assistant"
3. Restart HA
4. Add integration: Settings → Integrations → Add → Jane Voice Assistant
5. Enter OpenAI API key
6. Optionally configure Tavily and Firebase in Jane settings

### Voice Pipeline Setup
1. Install via HACS: "OpenAI Whisper STT API" + "OpenAI TTS"
2. Create Voice Assistant: Settings → Voice Assistants → Add
   - Conversation Agent: **Jane**
   - STT: **OpenAI Whisper**
   - TTS: **OpenAI TTS** (voice: nova)
   - Language: **Hebrew**

## Tech Stack

| Layer | Technology |
|-------|-----------|
| LLM | Claude Sonnet 4 (complex) + Haiku 4.5 (fast) — dual model, 33 tools |
| STT | gpt-4o-mini-transcribe (Hebrew prompt hints) |
| TTS | OpenAI TTS, voice nova (via HACS) |
| Smart Home | Home Assistant (native Python API) |
| Web Search | Tavily API (optional) |
| Memory Backup | Firebase Firestore (optional) |
| Wake Word | microWakeWord "Hey Jane" + Voice Satellite Card |
| Server | Raspberry Pi 5 (HAOS) |

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md) for the full roadmap.

v3.2.0 highlights:
- **Claude Sonnet 4** — replaced GPT-5.4 Mini, reliable tool calling
- **Dual model** — Haiku 4.5 for fast commands, Sonnet 4 for complex tasks
- **33 tools** — discovery, calendar, memory, device management, config reading
- **Smart memory** — only home.md loaded by default, read_memory tool for on-demand access
- **Prompt caching** — system prompt cached 5 min, chat gets 2 tools instead of 33
- **Config safety** — backup before YAML write, refuses to write if read fails

Next up:
- **ha_config_api → HA Config Store API** — safer automation creation (no more YAML)
- **Proactive behavior** — Jane speaks up when she notices something
- **Per-user personality** — different behavior per family member
