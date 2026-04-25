# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Jane is a Hebrew-speaking AI voice assistant for Home Assistant, running on a Raspberry Pi 5 with HAOS. It's a custom HA conversation agent (`jane_conversation`) using Gemini 2.5 Pro + Flash with function calling, PostgreSQL 16 + Redis 7 for memory, and Gemini TTS for voice output. Distributed via HACS.

## How a Claude session works on Jane

Claude sessions on this project are expected to behave like a thoughtful contributor, not an autocomplete. Read this section before touching code.

### Session start ritual

Before any non-trivial task:

1. **Read this file (CLAUDE.md)** — repo conventions, hard rules, layer map. Don't skip it because the task looks small.
2. **Read your auto-memory** — at `~/.claude/projects/<jane-project-slug>/memory/MEMORY.md` (slug looks like `-Users-<username>-dev-jane`). It indexes everything past sessions remembered about the project owner, the project, and feedback they've given you. Pull entries that match the task at hand.
3. **Check Notion if the task is non-obvious** — the project workspace at the top page "Jane — Home Intelligence" has a callout pointing to **Operations / Workflow Guide**, which is the end-to-end Epic → Sprint → Task → Branch → PR → Release procedure.
4. **Plan, then implement** — for any change beyond a one-line fix, propose a plan and get approval. Never skip the planning step.

### Where to find information

| You need | Look here (in this order) |
|----------|---------------------------|
| Repo conventions, lint, file-size, async gotchas | This file (CLAUDE.md) |
| Past decisions about the project owner's preferences / way of working | auto-memory `feedback_*.md` and `user_*.md` files |
| Workflow procedure (open task, branch, PR, merge, release) | Notion → Operations / Workflow Guide |
| Architecture deep dive (memory layers, tool dispatch, brain loop) | Notion → Architecture/* + `docs/MEMORY_ARCHITECTURE.md` + `docs/TOOL_CALLING_ARCHITECTURE.md` |
| Current sprint state, what's Active vs Backlog | Notion → Sprints DB + auto-memory `project_sprint_plan.md` |
| Past architectural decisions and their reasons | Notion → ADRs DB |
| Standing constraints (limits, workarounds, traps) | Notion → Architecture / Known Limitations |
| Open product/research questions | Notion → Architecture / Open Questions (12 OQs) |
| Hard-learned bugs / gotchas (incl. JANE-84, debouncer self-cancel) | Notion → Operations / Lessons Learned + auto-memory `feedback_*` |
| Real names, home layout, family-specific facts | Notion → Private / Household details (NEVER hard-code in repo) |
| Reviewer briefing for an outside reviewer Claude | Notion → For Reviewers / Project Overview |
| Current versions of related repos | Notion → Operations / Version Matrix |

### Source-of-truth precedence

When two places disagree, this is the order:

1. **Code** — for "what does the system actually do right now". Notion / docs can lag a sprint; the running code can't.
2. **Notion** — for product, process, sprint state, decisions, design intent.
3. **Auto-memory** (`~/.claude/projects/.../memory/`) — for the project owner's preferences and feedback patterns.
4. **CLAUDE.md** — for repo conventions specifically.
5. **README.md and docs/** — quick references; treat as cached views, not authoritative.

If you find a contradiction, fix the stale source in the same PR (or open a task for it). Don't act on stale info silently.

### Memory across sessions

Auto-memory persists between sessions on the project owner's machine, indexed at `~/.claude/projects/<jane-project-slug>/memory/MEMORY.md`. Save user/feedback/project/reference memories there, not in CLAUDE.md. CLAUDE.md is for things that apply to anyone touching this repo; auto-memory is for things that apply specifically to this user's preferences.

### Hard rules — never skip

- **No real personal names** anywhere in code or tests. Use placeholders Alice / Bob / Charlie / Daisy. Real names live only in the runtime `persons` table and in Notion / Private / Household details.
- **`response_schema` is mandatory** for any Gemini structured-output call. `response_mime_type="application/json"` alone returns prose like "Here is the JSON..." (JANE-84).
- **Never bump `manifest.json` manually** — CI owns the version.
- **Never push to `main` directly** — all changes via PR; the 4 CI checks must run.
- **Never skip CI hooks** — no `--no-verify`, no `--no-gpg-sign`. If a hook fails, fix the underlying issue.
- **All GitHub PR / review comments in English** — never Hebrew on GitHub. Hebrew stays in the local conversation with the project owner.
- **Always use `mcp__github__*` tools** for PR / issue / comment / merge operations. Never the `gh` CLI for these.
- **Resolve PR review conversations** on GitHub after fixing each comment.
- **No `${{ github.event.pull_request.body }}` inline in `bash run:`** — backticks in PR bodies execute as commands. Always route through `env:` vars.

### Notion API limits (when editing the workspace via MCP)

- Cannot create `linked_database_view` blocks — UI only.
- Cannot reorder existing blocks via API — only new blocks via `after:<block_id>` on create.
- Moving databases via `move-page` is unreliable — the project owner moves them manually in the UI when needed.
- Creating a new database needs `Notion-Version: 2025-09-03` and the dedicated endpoint.

## Commands

```bash
# Tests (excludes live integration test)
pytest tests/ -v --tb=short --ignore=tests/test_config_api_live.py

# Single test file / specific test
pytest tests/test_brain.py -v
pytest tests/test_brain.py::TestClassifier::test_chat -v

# Lint + format (fix)
ruff check custom_components/ tests/ --fix
ruff format custom_components/ tests/

# Quick local lint (runs both above)
bash scripts/lint.sh

# Deploy to dev VM (rsync)
jdev-push    # alias — rsync custom_components/ to VM
jdev-restart # alias — restart HA on VM
jdev-logs    # alias — tail Jane logs on VM
```

## CI Requirements (4 checks, all must pass for PR merge)

1. **pytest** — `pytest tests/ -v --tb=short --ignore=tests/test_config_api_live.py`
2. **ruff check** — `ruff check custom_components/ tests/`
3. **ruff format** — `ruff format --check custom_components/ tests/`
4. **File size** — max 300 lines per `.py` file in `custom_components/` (excluding `__init__.py`). Exempt files: `definitions.py`, `discovery.py`, `storage.py`, `extraction.py`, `consolidation.py`

## Version Bumping

Handled automatically by CI on PR merge. PR title prefix determines bump type:
- `feat(...):` → minor bump
- `fix(...):` → patch bump
- Anything else → no bump

Version lives in `custom_components/jane_conversation/manifest.json`. Never bump manually.

## Planning First

Always create a plan and get user approval BEFORE implementing changes. Never skip the planning step. List what files will be changed and why before writing any code. Check the Notion feature spec if one exists.

## Verification

Before claiming work is done:
1. Run `ruff check custom_components/ tests/` — show zero errors
2. Run `ruff format --check custom_components/ tests/` — show zero errors
3. Run `pytest tests/ -v --tb=short --ignore=tests/test_config_api_live.py` — show passing
4. Check file sizes: any new/modified `.py` file in `custom_components/` must be under 300 lines (unless exempt)

Never claim "all clean" without actually running these commands and showing the output. If a test fails, report it honestly.

## Scope Discipline

Do not make changes beyond what was requested. When fixing a review comment, change only what the reviewer asked for. When refactoring, keep changes minimal and targeted. Do not add lint ignores, extra error handling, or "while I'm here" improvements.

## Architecture

### Request Flow
```
Voice/Text → HA Assist Pipeline → conversation.py (ConversationEntity)
  → brain/classifier.py (chat|command|complex → model selection)
  → brain/engine.py think() loop (system prompt + context + tools, up to 10 iterations)
  → Tool execution → memory extraction (async, post-response)
```

### brain/ — LLM Engine
- **engine.py** — `think()` main loop. Builds system instruction from: SYSTEM_PROMPT + time + home context + home layout + routines + memory + episodic + policy + anti-repetition. Dual model: Flash for chat/commands, Pro for complex.
- **classifier.py** — Classifies Hebrew input into `chat`, `command`, or `complex`.
- **context.py** — Builds working memory context (presence, active devices, recent changes).
- **working_memory.py** — Redis-backed real-time state (presence, active devices, changes with TTL).

### memory/ — 7-Layer Memory System
All memory subsystems are initialized in `__init__.py` (`async_setup_entry`) and stored on the `JaneData` dataclass in `const.py`.

- **storage.py** — `StorageBackend` ABC + `PostgresBackend`. **DualWrite was removed** in PR #39 (ADR-3) — the active write + read path is PG-only. MD files are kept as a read-time fallback through `manager.py` if PG is unavailable.
- **manager.py** — MD-file read fallback path, anti-repetition tracking.
- **structured.py** — `StructuredMemoryStore` — PG tables `persons`, `relationships`, `preferences` with confidence scoring + decay + soft-delete (`deleted_at`).
- **episodic.py** — `EpisodicStore` — append-only `events` + `episodes` + `daily_summaries`.
- **consolidation.py** — `ConsolidationWorker` — 6-hourly: raw events → episodes → daily summaries. Uses Gemini Flash for narrative generation. Generates embeddings after consolidation.
- **embeddings.py** — pgvector integration. `gemini-embedding-001` (768 dims, reduced from 3072). Backfill on startup. Non-fatal failures.
- **extraction.py** — Post-conversation memory extraction via Gemini Flash. **Ops-based** (A3): emits a list of `MemoryOp` objects parsed by `ops.py` and applied by `ops_applier.py`. Always uses `response_schema` (JANE-84 — `response_mime_type` alone is insufficient).
- **ops.py** + **ops_applier.py** — `MemoryOp` / `OpResult` dataclasses + the applier that writes ops to PG (insert / update / delete / soft-delete).
- **debouncer.py** — `ExtractionDebouncer` (A1). Batches multiple short turns into one extraction call. **Self-cancel-safe** — cancelling the timer task from inside its own coroutine no longer kills it.
- **preference_optimizer.py** + **preference_merge_helpers.py** — B1 two-stage preference dedup: deterministic merge for clear winners, LLM fallback for ambiguous pairs.
- **policy.py** — `PolicyStore` — permission rules, confirmation thresholds, quiet hours per user.
- **routine_store.py** — `RoutineStore` — Smart Routines cached in PG with confidence scores.
- **context_builder.py** — Formats episodic + memory context for system prompt injection.
- **migrate_structured.py** — One-time MD → PG migration with sentinel pattern (`category='_migration'`).
- **firebase.py** — Disaster-recovery backup (still active).

### tools/ — 41 Tool Definitions
- **definitions.py** — Gemini `function_declarations` (JSON schemas for all tools). Verify count with `grep -c "^TOOL_[A-Z_]* = " tools/definitions.py` (currently 41).
- **registry.py** — Maps tool names → handler functions. `execute_tool()` dispatcher. Errors are caught and fed back to Gemini as the result string — never raised. Jane never crashes.
- **handlers/** — Grouped by domain: `core.py`, `device.py`, `discovery.py`, `calendar.py`, `family.py`, `memory_tools.py`, `config.py`, `power.py`.
- **`forget_memory` tool** (A5) — soft-deletes a row by routing the user's "תשכחי את X" through `memory_tools.py` → `structured.py` (`deleted_at = NOW()`).

### Key Patterns
- **JaneData dataclass** (`const.py`) — typed container for all runtime state. Access via `hass.data[DOMAIN]`. Fields: `entry`, `pg_pool`, `redis`, `working_memory`, `gemini_client`, `structured`, `episodic`, `consolidation`, `routines`, `policies`, `extraction_debouncer`, `_unsubs`.
- **PG-only read path (ADR-3)** — all live reads hit PG via `PostgresBackend`. MD files are a fallback only when PG is down.
- **Soft-delete (A4)** — `persons` / `preferences` / `relationships` carry `deleted_at`. All reads filter `deleted_at IS NULL`. Double-delete is a no-op.
- **Ops-based extraction (A3)** — extraction emits structured `MemoryOp`s instead of free-form JSON. Defined in `memory/ops.py`.
- **`response_schema` invariant (JANE-84)** — any Gemini structured-output call MUST pair `response_mime_type="application/json"` with a real `response_schema`. The mime type alone returns prose like "Here is the JSON...".
- **Executor wrapping** — blocking I/O (file reads, sync API calls) must use `hass.async_add_executor_job()`. Does not accept kwargs — wrap with lambda.
- **Whisper hallucination filter** — `WHISPER_HALLUCINATIONS` in const.py filters known STT artifacts.
- **Personal names** — never appear in code or tests. Use placeholders Alice / Bob / Charlie / Daisy. Real names live only in the runtime `persons` table + the Notion Private/Household details page.

## Testing

Tests mock all of Home Assistant (`conftest.py` patches `sys.modules` with MagicMock for all `homeassistant.*` modules). `test_config_api_live.py` is the only live integration test (excluded from CI). The `hass_mock` fixture provides a mock HA instance with Hebrew entity names.

## Related Repositories

- **ha-jane-db** — PostgreSQL 16 + Redis 7 + pgvector HA add-on (Alpine Linux, builds pgvector from source for PG 16)
- **ha-gemini-tts** — Gemini TTS add-on (callirrhoe voice)

## Dev Workflow

1. **Design in Notion first** — feature specs required before implementation
2. **Plan mode** — create implementation plan, get approval
3. **Code** — test locally with `pytest` + `jane_cli.py`
4. **Deploy to dev VM** — `jdev-push` + `jdev-restart`, verify with `jdev-logs`
5. **PR** — conventional commits (`feat:`, `fix:`), all 4 CI checks must pass
6. **After fixing PR review comments** — always resolve the conversation thread on GitHub
7. **Merge** — CI auto-bumps version and creates GitHub release
8. **Production** — HACS update on production Pi

## Jane's System Prompt Rules

When editing SYSTEM_PROMPT in `const.py`, every rule must include WHY — not just WHAT. LLMs follow rules far better when they understand the reason.
- Bad: `"NEVER use emojis"`
- Good: `"NEVER use emojis. This is a voice assistant — emojis are read aloud by TTS and sound terrible."`

Jane is designed as an autonomous family assistant, not a voice remote. She should think, search, chain actions, and manage memory without being told. When adding tools or behaviors, prefer autonomy over explicit commands.

## HA Patterns

Always use native Home Assistant approaches:
- Dashboard UI → custom Lovelace cards (not iframes or separate HTML pages)
- Running code on HAOS → add-ons (not SSH scripts)
- Check what HA supports natively before proposing external solutions

## Async/Threading Gotchas

- `hass.async_add_executor_job()` does **not** support kwargs — wrap with lambda: `lambda: func(arg1, kwarg=val)`
- Blocking I/O (genai.Client, Firebase, file reads) → must run in executor, never on event loop
- `hass.async_create_task` from threads → use `asyncio.run_coroutine_threadsafe` instead
- `time.sleep()` is OK in executor threads, NEVER on event loop
- For PG scheduled tasks: pass a `lambda` that creates the coroutine, not a pre-created coroutine

## Language

Jane's UI, personality, and all user-facing text is in Hebrew. Entity names, friendly names, and person names are Hebrew. Never hardcode Hebrew strings in inline JavaScript — pass via YAML config or template variables.

## Notion

Notion is the source of truth for project management. After completing a plan or design phase, update Notion (sprint items, status, design decisions) before starting to code.
