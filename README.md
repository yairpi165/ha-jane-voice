# Jane — AI-Powered Smart Home Voice Assistant

A private, Hebrew-speaking voice assistant for smart home control. Built on Gemini 2.5 Pro + Flash with Home Assistant integration, running on a Raspberry Pi 5.

**Version:** 3.14.0 | **Tools:** 38 | **Tests:** 112

## What Jane Does

- **Natural Hebrew conversation** — warm, curious personality. Part of the family, not a robot.
- **38 autonomous tools** — device control, automations, scripts, scenes, discovery, notifications, timers, lists, calendar, memory, web search
- **Smart Routines** — caches multi-step commands as scripts/scenes for instant reuse
- **Structured memory** — PostgreSQL-backed persons, preferences with confidence scoring and decay
- **Real-time awareness** — Redis Working Memory tracks who's home, active devices, recent changes
- **Dual model brain** — Flash for speed, Pro for complex reasoning
- **Firebase backup** — memory backed up to Firestore

## Pipeline

```
Voice → HA Cloud STT (Nabu Casa) → Jane (Gemini 2.5) → Gemini TTS (callirrhoe)
```

## Architecture

```
jane_conversation/
├── brain/              # Engine, classifier, context, working memory
│   ├── engine.py       # Dual model think() loop + tool execution
│   ├── classifier.py   # Request type: chat/command/complex
│   ├── context.py      # Working Memory context builder
│   └── working_memory.py  # Redis-backed real-time awareness
├── memory/             # Persistent memory system
│   ├── manager.py      # Load/save functions, PG scheduling
│   ├── storage.py      # StorageBackend: File/Postgres/DualWrite
│   ├── structured.py   # S1.3: persons, preferences tables
│   ├── context_builder.py  # Formats memory for Gemini context
│   ├── extraction.py   # Gemini-based memory extraction
│   ├── migrate_structured.py  # MD → structured table migration
│   └── firebase.py     # Firestore backup
├── tools/              # 38 tool definitions + handlers
│   ├── registry.py     # Tool declarations + dispatcher
│   ├── definitions.py  # Gemini function_declarations
│   └── handlers/       # 8 handler modules by domain
├── config/             # HA config Store API
├── conversation.py     # HA ConversationEntity interface
├── const.py            # System prompt, taxonomy, constants
├── config_flow.py      # HA UI config + options flow
└── strings.json        # UI translations
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Brain | Gemini 2.5 Pro + Flash (dual model, function calling) |
| STT | Home Assistant Cloud (Nabu Casa) |
| TTS | Gemini TTS — callirrhoe voice ([ha-gemini-tts](https://github.com/yairpi165/ha-gemini-tts)) |
| Platform | Home Assistant OS on Raspberry Pi 5 (16GB) |
| Storage | PostgreSQL 16 + Redis 7 ([ha-jane-db](https://github.com/yairpi165/ha-jane-db)) |
| Memory | Structured PG tables (persons, preferences) + MD files (DualWrite) |
| Working Memory | Redis — presence, active devices, recent changes |
| Search | Google Search (built-in via Gemini) |
| Backup | Firebase Firestore |

## Storage Architecture

```
PostgreSQL (ha-jane-db add-on)
├── memory_entries    # Legacy MD-equivalent (DualWrite)
├── persons           # Family members (S1.3)
├── relationships     # Family relationships (S1.3)
├── preferences       # Structured preferences with confidence/decay (S1.3)
├── events            # Audit trail: actions, conversations, corrections
└── response_tracking # Anti-repetition cache

Redis (same add-on)
├── jane:presence       # Who is home/away + since when
├── jane:active         # Currently active devices
├── jane:changes        # Recent state changes (1h TTL)
├── jane:context_cache  # Pre-rendered context (30s TTL)
└── jane:last_interaction  # Last conversation
```

## Setup

### Prerequisites
- Raspberry Pi 5 running Home Assistant OS
- Gemini API key
- Gemini TTS: [ha-gemini-tts](https://github.com/yairpi165/ha-gemini-tts)
- Jane DB add-on: [ha-jane-db](https://github.com/yairpi165/ha-jane-db) (PostgreSQL + Redis)
- Firebase service account key (optional — memory backup)

### Installation via HACS
1. Add custom repository: `https://github.com/yairpi165/ha-jane-voice`
2. Install "Jane Voice Assistant"
3. Restart HA
4. Settings → Integrations → Add → Jane Voice Assistant
5. Enter Gemini API key
6. Configure PostgreSQL host (Jane DB add-on hostname)

### Voice Pipeline
1. Install [ha-gemini-tts](https://github.com/yairpi165/ha-gemini-tts) via HACS
2. Create Voice Assistant: Settings → Voice Assistants → Add
   - **Conversation Agent:** Jane
   - **STT:** Home Assistant Cloud
   - **TTS:** Gemini TTS (voice: callirrhoe)
   - **Language:** Hebrew

## License

Private project. Not open source.
