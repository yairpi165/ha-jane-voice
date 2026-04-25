# Memory Architecture

> Source of truth for this document is in the Notion project workspace.
> This file is kept as a quick reference only.

## Overview

Jane uses a multi-layer memory system backed by PostgreSQL and Redis.

**Long-term memory** — PostgreSQL via `PostgresBackend` (PG-only since PR #39 / ADR-3; DualWrite removed).
**Working memory** — Redis (real-time household awareness, 1h TTL).
**Episodic memory** — PostgreSQL (state changes → episodes → daily summaries) + pgvector embeddings on episodes + daily summaries (S1.6).
**Read-time fallback** — MD files under `jane_memory/` are read by `manager.py` only when PG is unavailable. They are **not** written to anymore.
**Backup** — Firebase Firestore, disaster-recovery only.

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

### MD Files (legacy, read-time fallback only)

DualWrite was removed in PR #39 (ADR-3). MD files are no longer written. They remain on disk and are read via `manager.py` only when PG is unavailable. Treat them as a snapshot from the cutover, not a live mirror.

```
jane_memory/
├── users/{name}.md   # Personal preferences (frozen — live data in `preferences` PG table)
├── family.md         # Household members + rules (frozen — live data in `persons` + `relationships`)
├── habits.md         # Recurring patterns (frozen)
├── actions.md        # Rolling 24h action log (frozen — live data in `events`)
├── home.md           # Home layout (Gemini-generated; still read-only ref for engine)
├── corrections.md    # Legacy (new corrections → `events` table)
├── routines.md       # Smart Routines (frozen — live data in `routines` PG table)
└── history.log       # Conversation log (still appended for grep convenience)
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
extraction.py: process_memory()    (post-response, async, non-fatal)
    ├── ExtractionDebouncer (A1) batches multiple short turns into one call
    ├── Multi-exchange context (A2) — sees rolling N-turn window
    ├── Gemini Flash with response_schema=_OPS_RESPONSE_SCHEMA (JANE-84)
    │     → returns a list of MemoryOp objects (insert / update / delete / soft-delete)
    ├── ops_applier.py applies each op to PG
    │     ├── soft-delete (A4) → SET deleted_at = NOW()
    │     └── reads filter deleted_at IS NULL — double-delete is a no-op
    ├── PreferenceOptimizer (B1) — two-stage dedup pass after writes
    └── On parse failure → log + drop the batch; conversation already returned to user

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

- **State-change capture** — every state_change is recorded into both Redis (1h TTL, real-time context) and PG (`events` + `event_entities`, permanent). This is dual-target capture, not the removed `DualWriteBackend`.
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

## Memory Optimization (Sprint 5–7 detour, Sept 2025 — v3.27.x)

After Phase 1 sign-off, observability + memory hygiene work surfaced a backlog that grew into Sprints 5–7. This block of work is **not** in the Master Blueprint — it's a deliberate detour to harden Phase 1 before moving on to Phase 3 Intelligence. Sprint 6 closed 2026-04-23. Sprint 7 is Active.

### Phase A — extraction quality (Sprint 5–6, all Done)

- **A1 Extraction debouncing** — `ExtractionDebouncer` (memory/debouncer.py) batches multiple short turns into one extraction call. Self-cancel-safe (cancelling the timer task from inside its own coroutine no longer kills it). Codified in `feedback_debouncer_self_cancel.md`.
- **A2 Multi-exchange context** — extraction sees the rolling N-turn conversation, not just the last user/assistant pair. Catches preferences expressed across turns.
- **A3 Ops-based extraction** — extraction emits `MemoryOp` objects (insert / update / delete / soft-delete with table + key + payload) instead of free-form JSON. Defined in `memory/ops.py` + `memory/ops_applier.py`. Removes ambiguity, supports retries. Recorded as ADR-2.
- **A4 Soft-delete primitive** — `deleted_at` column on `persons` / `preferences` / `relationships`. All reads filter `deleted_at IS NULL`. Double-delete is a no-op. Lets the model "forget" something without dropping rows.
- **A5 forget_memory tool** — user-facing "תשכחי את X" → `forget_memory` handler in `tools/handlers/memory_tools.py` soft-deletes the matching row(s).

### Phase B — hygiene + observability (Sprint 6–7)

- **B1 Two-stage preference dedup** — `PreferenceOptimizer` (memory/preference_optimizer.py) collapses near-duplicate preferences in two passes: deterministic merge (preference_merge_helpers.py) for clear winners, LLM fallback for ambiguous pairs. Done in Sprint 6 (PR #47).
- **B2 Weekly memory consolidation + diff report** — JANE-81, Sprint 7. Periodic job that summarizes the week's memory deltas and surfaces them for review.
- **B3+B4 Staleness + corrections lifecycle** — JANE-83, Sprint 7. Detect stale preferences and route corrections through a known lifecycle (proposed → confirmed → active).
- **B5 Observability layer** — JANE-82, Sprint 7. Weekly memory health report (extraction success rate, dedup hits, soft-delete count, embedding coverage).
- **B6 Memory quotas / never-remember** — not yet ticketed. Per-user quotas + a "never remember this" marker.

### JANE-84 — Gemini JSON via response_schema

Hard-learned: `response_mime_type="application/json"` alone is **not** enough — Gemini Flash still returns prose like "Here is the JSON...". `extraction.py` now passes both `response_mime_type` AND `response_schema=_OPS_RESPONSE_SCHEMA`. This invariant is codified in `feedback_gemini_json_mode.md` and **must** be paired anywhere we want structured output.

### ADR-3 — DualWrite read-path removal (PR #39)

The active write + read path is PG-only via `PostgresBackend`. `DualWriteBackend` was removed from `storage.py`. MD files under `jane_memory/` are kept as a read-time fallback through `manager.py` for the case where PG is unavailable.

## Key Files

| File | Purpose |
|------|---------|
| `memory/storage.py` | `StorageBackend` ABC + `PostgresBackend` (PG-only since ADR-3) |
| `memory/manager.py` | MD-file fallback path, anti-repetition |
| `memory/structured.py` | `StructuredMemoryStore` (persons, relationships, preferences with `deleted_at`) |
| `memory/episodic.py` | `EpisodicStore` (events, episodes, daily_summaries) |
| `memory/consolidation.py` | `ConsolidationWorker` (clustering, Gemini summaries, embeddings) |
| `memory/embeddings.py` | pgvector backfill + similarity search (gemini-embedding-001, 768-dim) |
| `memory/extraction.py` | Ops-based extraction with `response_schema` (JANE-84) |
| `memory/ops.py` | `MemoryOp` / `OpResult` dataclasses (A3) |
| `memory/ops_applier.py` | Applies ops to PG (A3) |
| `memory/debouncer.py` | `ExtractionDebouncer` (A1, self-cancel safe) |
| `memory/preference_optimizer.py` | B1 two-stage pref dedup |
| `memory/preference_merge_helpers.py` | Helpers for deterministic pref merge |
| `memory/context_builder.py` | Formats memory + episodic for Gemini context |
| `memory/migrate_structured.py` | One-time MD → structured migration with sentinel |
| `memory/firebase.py` | Disaster-recovery backup (still active) |
| `memory/routine_store.py` | `RoutineStore` (structured routines, S1.5) |
| `memory/policy.py` | `PolicyStore` (per-user policies, S1.5) |
| `brain/working_memory.py` | Redis real-time awareness + PG event dual-target capture |
| `brain/context.py` | Working Memory context + routines fallback |
