# Speaker Identification (S3.0 / JANE-71) — Design

> **Source of truth: Notion** — `Architecture / Phase 3 / Speaker Identification (S3.0)` (id `34475dc9-da6e-8110-b9f1-eccbb729ac27`).
> This file is a quick reference only. When in doubt, the Notion page is authoritative.

## What this is

JANE-71 implements the **layered `resolve_speaker()`** for Jane's voice path. Without speaker identification, every voice call from a shared device is treated as `user_name=default`, so `build_memory_context` returns empty and Phase-1 memory is invisible to voice. JANE-62 is the prod symptom of this gap.

JANE-71 closes both halves of JANE-62:
- **Half A** — `resolve_speaker()` returns a real user_name + confidence for voice paths.
- **Half B** — `build_memory_context` becomes confidence-aware: personal data only at confidence ≥ 0.7; family-level facts at 0.5–0.7; household-level minimum below 0.5.

## The ladder (one-line summary)

| Step | Source | Confidence |
|------|--------|-----------:|
| 0 | HA `context.user_id` (filtered against `"default"`) | 1.0 |
| 1 | `device_id` → device registry → area → sole resident | 0.85 |
| 2 | Exactly one person home (`jane:presence`) | 0.95 |
| 3 | Active `jane:session:{device_id}` (TTL 15m) | inherited × 0.95<sup>min</sup>, floor 0.5 |
| 4 | Pending-ask flow ("מי מדבר?" + re-execute) | 0.85 after known reply |
| 5 | Fallback to `primary_user` | 0.3 |

Full rationale, per-step notes, confidence-aware policy, JANE-62 absorption per-field tier table, Redis-down degraded mode, and PR sequence — all in the Notion page.

## Key locked decisions (D1–D12)

The full list lives in Notion. Highlights:

- **D1** — Step 1 = 0.85, Step 2 = 0.95 (exclusion > attribution).
- **D2** — Filter `user_id == "default"` at Step 0 (the JANE-62 fingerprint).
- **D5** — Redis key: `jane:session:{device_id}` → `{user_name, conversation_id, ts, confidence}`.
- **D7** — `_sessions` history dict stays at `conversation.py:50` (out of S3.0 scope).
- **D8** — `primary_user` lives in `persons.metadata.is_primary = true`. No schema migration.
- **D9** — Step 1 chains: `device_id → device_registry → area_id → resolve_sole_resident`.
- **D10** — `SENSITIVE_ACTIONS`: bedroom/bathroom devices, calendar writes, automation enable/disable, `forget_memory`.
- **D11** — `PERSONAL_DATA_ACTIONS`: `preferences`/`memory_entries` reads, presence-to-TTS, personal calendar reads, episodic context reads.
- **D12** — Routines treated as `shared` for v1 (the `routines.scope` column lands in S3.1).

## Verified facts (V1–V4)

Captured 2026-04-28 via dev-VM probe (temp `_LOGGER` line on `async_process` + Voice Satellite Card on Tablet Dev / סלון):

- **V1** — Voice path delivers `user_input.device_id` populated; matches the satellite's `device_registry` entry.
- **V2** — Service-call path delivers `context.user_id` (the HA user UUID); `device_id=None`.
- **V3** — Voice path returns `context.user_id=None` — Steps 1/2/3 are the only avenues for voice speaker identification.
- **V4** — `device_id → area_id` lookup chain via HA's device registry works.

These retire the original R1 risk ("Q1 fails — `device_id` doesn't map to area").

## Implementation roadmap

| PR | What ships |
|----|-----------|
| **#1** | This pointer + the Notion update. No code. **Gate.** |
| **#2** | `brain/speaker.py` (`resolve_speaker` Steps 0/1/3 + Redis session) + `conversation.py` glue + tests. |
| **#3** | `working_memory.is_exactly_one_home()` + Step 2 + Redis-down fallback + tests. |
| **#4** | `check_permission(confidence=)` + `context_builder` tiers + `SENSITIVE_ACTIONS`/`PERSONAL_DATA_ACTIONS` + tests. **Closes JANE-62.** |
| **#5** | Step 4 pending-ask state machine + tests. (Higher review scrutiny — new scope beyond v2.) |

## Files this work touches

**New:** `brain/speaker.py`, this file.

**Modified:** `conversation.py`, `memory/policy.py`, `memory/context_builder.py`, `brain/working_memory.py`, `memory/structured.py`, `const.py`.

No DB schema migration — `is_primary` lives in `persons.metadata` JSONB, `routines.scope` deferred to S3.1.

## Out of scope (this ticket)

- Voice ID / speaker embeddings → JANE-51 (Phase 5 / S5.3).
- Moving `_sessions` history to Redis.
- B6 memory quotas / never-remember list.
- JANE-90 (corrections wired via person/key anchor) — additive, separate epic.
- `routines.scope` column → S3.1 (Household Modes).
- `policies.sensitive_scene_ids` allowlist — future ticket if scene-name pattern matching proves insufficient.

---

*See the Notion page for the full design intent, locked decisions D1–D12, verified facts V1–V4, per-field tier table, and pending-ask state machine details.*
