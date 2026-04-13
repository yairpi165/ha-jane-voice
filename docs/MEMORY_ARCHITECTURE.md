# Memory Architecture

> Source of truth for this document is in the Notion project workspace.
> This file is kept as a quick reference only.

## Overview

Jane uses a multi-layer memory system backed by PostgreSQL and Redis.

**Long-term memory** — PostgreSQL (via DualWriteBackend: writes PG + MD files)
**Working memory** — Redis (real-time household awareness)
**Backup** — Firebase Firestore (write-through)

## Storage Layers

### PostgreSQL Tables

| Table | Purpose | Populated by |
|-------|---------|-------------|
| `memory_entries` | Legacy MD-equivalent (category/content blobs) | extraction.py, save_memory tool |
| `persons` | Family members: name, role, birth_date, metadata | S1.3 migration, extraction |
| `relationships` | Family relationships (spouse, parent_of) | S1.3 migration |
| `preferences` | Structured preferences with confidence + decay | extraction.py (Phase B) |
| `events` | Audit trail: actions, conversations, corrections | conversation.py |
| `response_tracking` | Anti-repetition (last 50 openings) | conversation.py |

### Redis Keys

| Key | Type | Purpose |
|-----|------|---------|
| `jane:presence` | Hash | Who is home/away |
| `jane:presence:since` | Hash | Since when (unix timestamp) |
| `jane:active` | Hash | Currently active devices |
| `jane:changes` | Sorted Set | Recent state changes (1h TTL) |
| `jane:context_cache` | String | Pre-rendered context (30s TTL) |
| `jane:last_interaction` | Hash | Last conversation |

### MD Files (legacy, still written via DualWrite)

```
jane_memory/
├── users/{name}.md   # Personal preferences
├── family.md         # Household members + rules
├── habits.md         # Recurring patterns
├── actions.md        # Rolling 24h action log
├── home.md           # Home layout (Gemini-generated)
├── corrections.md    # Legacy (new corrections → events table)
├── routines.md       # Smart Routines
└── history.log       # Conversation log
```

## Data Flow

```
Conversation
    ↓
extraction.py: process_memory()
    ├── save_*_memory() → memory_entries (DualWrite: PG + files)
    ├── schedule_pg_append("correction", ...) → events table
    └── _save_structured_preferences() → preferences table
    
Engine (each conversation)
    ├── build_context() → Working Memory (Redis) or live hass.states
    ├── build_memory_context() → persons + preferences from PG
    └── load_home() / load_routines() → memory_entries
```

## Preference System (S1.3)

- **Key taxonomy** in `const.py` — prevents free-form key duplication
- **Confidence scoring** — explicit = 1.0, inferred = 0.7
- **Decay** — inferred preferences: -0.05/day after 7-day grace period
- **Context injection** — preferences with confidence >= 0.5 injected into Gemini system_instruction

## Key Files

| File | Purpose |
|------|---------|
| `memory/manager.py` | Load/save functions, PG scheduling |
| `memory/storage.py` | StorageBackend abstraction (File/Postgres/DualWrite) |
| `memory/structured.py` | StructuredMemoryStore (persons, preferences) |
| `memory/context_builder.py` | Formats memory for Gemini context |
| `memory/extraction.py` | Gemini-based memory extraction |
| `memory/migrate_structured.py` | One-time MD → structured migration |
| `memory/firebase.py` | Firestore backup |
| `brain/working_memory.py` | Redis real-time awareness |
| `brain/context.py` | Working Memory context + fallback |
