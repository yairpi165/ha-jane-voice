# Jane — AI-Powered Smart Home Voice Assistant

A private, Hebrew-speaking voice assistant for smart home control. Built on Gemini 2.5 Pro + Flash with Home Assistant integration, running on a Raspberry Pi 5.

**Version:** 3.27.2 | **Tools:** 41 | **Tests:** 380

## What Jane Does

- **Natural Hebrew conversation** — warm, curious personality. Part of the family, not a robot.
- **41 autonomous tools** — device control, automations, scripts, scenes, discovery, notifications, timers, lists, calendar, memory, web search
- **Smart Routines** — caches multi-step commands as scripts/scenes for instant reuse
- **Structured memory** — PostgreSQL-backed persons + preferences with confidence scoring, decay, and soft-delete; ops-based extraction with `response_schema`
- **Episodic memory** — append-only events → 6-hourly episodes → daily summaries; pgvector semantic search (gemini-embedding-001, 768-dim)
- **Real-time awareness** — Redis Working Memory tracks who's home, active devices, recent changes
- **Dual model brain** — Flash for chat/commands, Pro for complex reasoning; classifier routes between them
- **Firebase backup** — disaster-recovery snapshot of memory

## Pipeline

```
Voice → HA Cloud STT (Nabu Casa) → Jane (Gemini 2.5) → Gemini TTS (callirrhoe)
```

## Architecture

```
jane_conversation/
├── brain/                       # Request loop
│   ├── engine.py                # Dual model think() loop + tool execution
│   ├── classifier.py            # Request type: chat/command/complex
│   ├── context.py               # Working Memory context builder
│   └── working_memory.py        # Redis-backed real-time awareness
├── memory/                      # 7-layer persistent memory
│   ├── storage.py               # StorageBackend ABC + PostgresBackend (PG-only since ADR-3)
│   ├── manager.py               # MD-file fallback path + anti-repetition
│   ├── structured.py            # S1.3: persons, relationships, preferences (with deleted_at)
│   ├── episodic.py              # S1.4: events, episodes, daily_summaries
│   ├── consolidation.py         # 6-hourly: events → episodes → summaries
│   ├── embeddings.py            # S1.6: gemini-embedding-001 (768-dim)
│   ├── extraction.py            # Ops-based extraction (A3) with response_schema (JANE-84)
│   ├── extraction_prompts.py
│   ├── ops.py + ops_applier.py  # MemoryOp dataclasses + applier (A3)
│   ├── debouncer.py             # ExtractionDebouncer (A1) — self-cancel safe
│   ├── preference_optimizer.py  # B1 two-stage pref dedup
│   ├── preference_merge_helpers.py
│   ├── routine_store.py         # S1.5: cached jane_ scripts
│   ├── policy.py                # S1.5: per-user permissions, quiet hours
│   ├── context_builder.py       # Formats episodic + memory context
│   ├── migrate_structured.py    # One-time MD → PG migration
│   └── firebase.py              # Disaster-recovery backup
├── tools/                       # 41 tool definitions + 8 handlers
│   ├── definitions.py           # Gemini function_declarations
│   ├── registry.py              # TOOL → handler dispatch
│   └── handlers/                # core, device, discovery, calendar, family, memory_tools, config, power
├── conversation.py              # HA ConversationEntity interface
├── const.py                     # SYSTEM_PROMPT, JaneData dataclass, WHISPER_HALLUCINATIONS
├── config_flow.py               # HA UI config + options flow
├── config_api.py                # Internal LLAT + REST helpers for config-store tools
└── strings.json                 # UI translations
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Brain | Gemini 2.5 Pro + Flash (dual model, function calling) |
| STT | Home Assistant Cloud (Nabu Casa) |
| TTS | Gemini TTS — callirrhoe voice ([ha-gemini-tts](https://github.com/yairpi165/ha-gemini-tts)) |
| Platform | Home Assistant OS on Raspberry Pi 5 (16GB) |
| Storage | PostgreSQL 16 + Redis 7 ([ha-jane-db](https://github.com/yairpi165/ha-jane-db)) |
| Memory | PostgreSQL-only via `PostgresBackend` (DualWrite removed in PR #39 / ADR-3); MD files = read-time fallback only |
| Working Memory | Redis — presence, active devices, recent changes |
| Embeddings | pgvector + `gemini-embedding-001` (768-dim) on episodes + daily summaries |
| Search | Google Search (built-in via Gemini) + Tavily for `search_web` tool |
| Backup | Firebase Firestore — disaster recovery only |

## Storage Architecture

```
PostgreSQL (ha-jane-db add-on)
├── memory_entries     # Legacy MD-equivalent (category, user_name, content)
├── persons            # Family members with deleted_at (S1.3 + A4 soft-delete)
├── relationships      # Family relationships (S1.3)
├── preferences        # Structured preferences with confidence/decay/deleted_at (S1.3 + A4)
├── routines           # Cached jane_ scripts (S1.5)
├── policies           # Per-user permissions, quiet hours (S1.5)
├── events             # Append-only state changes + conversations (S1.4)
├── event_entities     # FK link from events to HA entity_id (S1.4)
├── episodes           # Consolidated narratives + 768-dim embedding (S1.4 + S1.6)
├── daily_summaries    # One narrative per day + 768-dim embedding (S1.4 + S1.6)
└── response_tracking  # Anti-repetition cache (recent openings)

Redis (same add-on)
├── jane:presence          # Who is home/away
├── jane:presence:since    # Since when (unix timestamp)
├── jane:active            # Currently active devices
├── jane:changes           # Recent state changes (sorted set, 1h TTL)
├── jane:context_cache     # Pre-rendered context (30s TTL)
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
