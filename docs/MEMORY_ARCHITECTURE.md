# Memory Architecture

> Source of truth for this document is in the Notion project workspace.
> This file is kept as a quick reference only.

## Overview

Jane uses a multi-layer memory system backed by PostgreSQL and Redis.

**Long-term memory** — PostgreSQL (via DualWriteBackend: writes PG + MD files)
**Working memory** — Redis (real-time household awareness, 1h TTL)
**Episodic memory** — PostgreSQL (state changes → episodes → daily summaries)
**Backup** — Firebase Firestore (write-through)

## Storage Layers

### PostgreSQL Tables

| Table | Purpose | Populated by |
|-------|---------|-------------|
| `memory_entries` | Legacy MD-equivalent (category/content blobs) | extraction.py, save_memory tool |
| `persons` | Family members: name, role, birth_date, metadata | S1.3 migration, extraction |
| `relationships` | Family relationships (spouse, parent_of) | S1.3 migration |
| `preferences` | Structured preferences with confidence + decay | extraction.py |
| `routines` | Structured Smart Routines (trigger, steps, occurrences) | routine_store.py |
| `policies` | Per-user access control (role, quiet hours) | policy.py, auto-seed |
| `events` | Audit trail: actions, conversations, state_changes | conversation.py, working_memory.py |
| `event_entities` | Links events to HA entities (entity_id) | working_memory.py dual-write |
| `episodes` | Consolidated event groups (title, summary, type) | consolidation.py (every 6h) |
| `daily_summaries` | One daily narrative per day | consolidation.py (daily) |
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
HA state_changed events
    ↓
working_memory.py: _on_state_changed()
    ├── Redis jane:changes (1h TTL, real-time context)
    └── PG events + event_entities (dual-write, persistent)

Every 6 hours: ConsolidationWorker
    ├── Load raw events from PG (last 6h window)
    ├── Group by temporal proximity (>10 min gap = new cluster, 90 min hard cap)
    ├── Simple clusters → template summary (no LLM)
    ├── Complex clusters → Gemini Flash summary (max 3 calls per window)
    └── Save to episodes table

Daily: ConsolidationWorker
    ├── Load yesterday's episodes
    ├── Gemini Flash → daily narrative summary
    └── Save to daily_summaries table

Conversation
    ↓
extraction.py: process_memory()
    ├── save_*_memory() → memory_entries (DualWrite: PG + files)
    ├── schedule_pg_append("correction", ...) → events table
    └── _save_structured_preferences() → preferences table

Engine (each conversation)
    ├── build_context() → Working Memory (Redis) or live hass.states
    ├── build_memory_context() → persons + preferences from PG
    ├── build_episodic_context() → recent episodes + yesterday's summary
    ├── load_home() → memory_entries
    ├── load_routines_index() → RoutineStore (PG) or routines.md fallback
    └── build_policy_context() → policies from PG
```

## Preference System (S1.3)

- **Key taxonomy** in `const.py` — prevents free-form key duplication
- **Confidence scoring** — explicit = 1.0, inferred = 0.7
- **Decay** — inferred preferences: -0.05/day after 7-day grace period
- **Context injection** — preferences with confidence >= 0.5 injected into Gemini system_instruction

## Episodic Memory (S1.4)

- **Dual-write** — every state_change persists to both Redis (1h TTL) and PG (permanent)
- **Consolidation** — every 6h, groups raw events into episodes via temporal clustering
- **Clustering** — 10 min gap splits clusters, 90 min hard cap prevents giant episodes
- **Templates** — 80% of clusters summarized without LLM (template-based)
- **LLM budget** — max 3 Gemini Flash calls per 6h window, excess falls back to template
- **Daily summaries** — 1 Flash call/day for narrative summary
- **Context injection** — episodes + daily summary injected into Gemini system_instruction (max 800 chars)
- **query_history tool** — enables "what happened last Thursday?" (up to 7 days)
- **Idempotency** — consolidation records last-processed window in memory_entries sentinel
- **Retention** — 10 days events, 90 days episodes, 365 days daily summaries

## Routine Memory (S1.5)

- **RoutineStore** — structured Smart Routines in PG `routines` table
- **Trigger matching** — substring containment, case-insensitive (ILIKE)
- **Occurrence tracking** — `increment_occurrence()` bumps count + last_used
- **Context injection** — top 10 routines by usage injected into Gemini context
- **Fallback** — if PG unavailable, reads from routines.md (backward compat)
- **Migration** — start fresh, new routines accumulate from conversations

## Policy Memory (S1.5)

- **PolicyStore** — per-user policies in PG `policies` table
- **Keys** — role (admin/child/guest), confirmation_threshold, quiet_hours, tts_enabled
- **check_permission()** — returns `str | None` (reason or allowed)
- **Quiet hours** — supports same-day (14:00–16:00) and overnight (23:00–07:00) ranges
- **Sensitive actions** — child role requires confirmation for set/remove automation/script, bulk_control
- **Context injection** — policies injected into Gemini system_instruction per user
- **Scope** — S1.5 stores + injects, Gemini decides. Hard enforcement in Phase 3
- **Auto-seed** — default policies (admin/child) seeded from persons table on startup

## Key Files

| File | Purpose |
|------|---------|
| `memory/manager.py` | Load/save functions, PG scheduling |
| `memory/storage.py` | StorageBackend abstraction (File/Postgres/DualWrite) |
| `memory/structured.py` | StructuredMemoryStore (persons, preferences) |
| `memory/episodic.py` | EpisodicStore (events, episodes, summaries) |
| `memory/consolidation.py` | ConsolidationWorker (clustering, LLM summaries) |
| `memory/context_builder.py` | Formats memory + episodic for Gemini context |
| `memory/extraction.py` | Gemini-based memory extraction |
| `memory/migrate_structured.py` | One-time MD → structured migration |
| `memory/firebase.py` | Firestore backup |
| `brain/working_memory.py` | Redis real-time awareness + PG dual-write |
| `memory/routine_store.py` | RoutineStore (structured routines) |
| `memory/policy.py` | PolicyStore (per-user policies) |
| `brain/context.py` | Working Memory context + routines fallback |
