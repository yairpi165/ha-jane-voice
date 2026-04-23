"""B1 — Semantic preference dedup (Stage 2 sweep).

Daily per-person pass: backfill embeddings for live preferences, compute
pairwise cosine similarity via pgvector, auto-merge when sim>=0.95,
Gemini Flash arbitrates 0.85<=sim<0.95, skip below. Audit via
``preference_merges``.

Stage 1 (write-time key normalization) lives in ``structured.py``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from google.genai import types

from ..const import GEMINI_MODEL_FAST
from .embeddings import generate_embedding, store_preference_embedding

_LOGGER = logging.getLogger(__name__)

AUTO_MERGE_THRESHOLD = 0.95
ARBITRATE_THRESHOLD = 0.85
MAX_PREFS_PER_PERSON = 200
MIN_PREFS_TO_SWEEP = 4
RECENT_SWEEP_HOURS = 1
MAX_VALUE_LEN = 400

_ARBITRATE_PROMPT = """You decide if two remembered preferences for the same person are duplicates.

Preference A: {a_key} = {a_value}
Preference B: {b_key} = {b_value}
Cosine similarity: {sim:.3f}

Respond with JSON only: {{"merge": true|false, "reason": "one short sentence"}}.

Guidelines: merge only when they express the same fact. Do NOT merge if one
is a more specific refinement of the other (e.g., "coffee" vs "decaf coffee")."""


@dataclass
class MergeSweepResult:
    """Per-person outcome of one dedup sweep."""

    person_name: str
    before_count: int = 0
    after_count: int = 0
    auto_merges: int = 0
    arbitrated_merges: int = 0
    arbitrated_vetoed: int = 0
    aborted_mid_merge: int = 0
    skipped_few_prefs: bool = False
    skipped_too_many: bool = False
    skipped_recent: bool = False
    errors: list[str] = field(default_factory=list)


async def sweep_all(pool, client, hass, structured) -> dict[str, MergeSweepResult]:
    """Dedup sweep across every person with live preferences."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT DISTINCT person_name FROM preferences WHERE deleted_at IS NULL")
    results: dict[str, MergeSweepResult] = {}
    for r in rows:
        person = r["person_name"]
        try:
            results[person] = await sweep_person(pool, client, hass, structured, person)
        except Exception as e:
            _LOGGER.warning("Dedup sweep for %s failed: %s", person, e)
            results[person] = MergeSweepResult(person_name=person, errors=[str(e)])
    return results


async def sweep_person(pool, client, hass, structured, person_name: str) -> MergeSweepResult:
    """Dedup sweep for one person. Idempotent — repeat calls are no-ops when clean."""
    result = MergeSweepResult(person_name=person_name)

    async with pool.acquire() as conn:
        recent = await conn.fetchval(
            "SELECT 1 FROM preference_merges "
            "WHERE winner_key IS NOT NULL "
            "  AND merged_at > NOW() - ($1 * INTERVAL '1 hour') "
            "  AND (winner_id IN (SELECT id FROM preferences WHERE person_name = $2) "
            "       OR loser_id IN (SELECT id FROM preferences WHERE person_name = $2)) "
            "LIMIT 1",
            RECENT_SWEEP_HOURS,
            person_name,
        )
        if recent:
            result.skipped_recent = True
            return result

        live_rows = await conn.fetch(
            "SELECT id, key, value, confidence, last_reinforced, embedding IS NOT NULL AS has_emb "
            "FROM preferences WHERE person_name = $1 AND deleted_at IS NULL "
            "ORDER BY id",
            person_name,
        )

    result.before_count = len(live_rows)
    result.after_count = len(live_rows)

    if len(live_rows) < MIN_PREFS_TO_SWEEP:
        result.skipped_few_prefs = True
        return result
    if len(live_rows) > MAX_PREFS_PER_PERSON:
        _LOGGER.warning(
            "Dedup sweep: %s has %d prefs (>%d cap) — skipping",
            person_name,
            len(live_rows),
            MAX_PREFS_PER_PERSON,
        )
        result.skipped_too_many = True
        return result

    # Backfill missing embeddings.
    for row in live_rows:
        if row["has_emb"]:
            continue
        text = f"{row['key']}: {row['value']}"
        vec = await generate_embedding(hass, client, text)
        if vec is None:
            result.errors.append(f"embedding failed for id={row['id']}")
            continue
        await store_preference_embedding(pool, row["id"], vec)

    # Fetch candidate pairs.
    async with pool.acquire() as conn:
        pair_rows = await conn.fetch(
            """SELECT p1.id AS a_id, p2.id AS b_id,
                      1 - (p1.embedding <=> p2.embedding) AS sim
                 FROM preferences p1 JOIN preferences p2
                   ON p1.person_name = p2.person_name AND p1.id < p2.id
                WHERE p1.person_name = $1
                  AND p1.deleted_at IS NULL AND p2.deleted_at IS NULL
                  AND p1.embedding IS NOT NULL AND p2.embedding IS NOT NULL
                  AND 1 - (p1.embedding <=> p2.embedding) >= $2
                ORDER BY sim DESC""",
            person_name,
            ARBITRATE_THRESHOLD,
        )

    already_merged_ids: set[int] = set()
    for pair in pair_rows:
        a_id, b_id, sim = pair["a_id"], pair["b_id"], float(pair["sim"])
        if a_id in already_merged_ids or b_id in already_merged_ids:
            continue

        if sim >= AUTO_MERGE_THRESHOLD:
            merged = await _merge_pair(pool, structured, a_id, b_id, sim, reason="auto-high-similarity", result=result)
            if merged:
                result.auto_merges += 1
                already_merged_ids.update([a_id, b_id])
        else:
            decision = await _arbitrate(hass, client, pool, a_id, b_id, sim)
            if decision is True:
                merged = await _merge_pair(pool, structured, a_id, b_id, sim, reason="gemini-arbitrated", result=result)
                if merged:
                    result.arbitrated_merges += 1
                    already_merged_ids.update([a_id, b_id])
            elif decision is False:
                result.arbitrated_vetoed += 1

    result.after_count = result.before_count - result.auto_merges - result.arbitrated_merges
    return result


async def _load_pref_by_id(pool, pref_id: int) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, person_name, key, value, confidence, last_reinforced "
            "FROM preferences WHERE id = $1 AND deleted_at IS NULL",
            pref_id,
        )
        return dict(row) if row else None


async def _arbitrate(hass, client, pool, a_id: int, b_id: int, sim: float) -> bool | None:
    """Ask Gemini Flash. Returns True/False/None (bad JSON = safe default None = skip)."""
    a = await _load_pref_by_id(pool, a_id)
    b = await _load_pref_by_id(pool, b_id)
    if not a or not b:
        return None
    prompt = _ARBITRATE_PROMPT.format(
        a_key=a["key"],
        a_value=a["value"],
        b_key=b["key"],
        b_value=b["value"],
        sim=sim,
    )
    try:
        response_schema = {
            "type": "object",
            "properties": {
                "merge": {"type": "boolean"},
                "reason": {"type": "string"},
            },
            "required": ["merge", "reason"],
        }
        response = await hass.async_add_executor_job(
            lambda: client.models.generate_content(
                model=GEMINI_MODEL_FAST,
                contents=prompt,
                config=types.GenerateContentConfig(
                    max_output_tokens=200,
                    temperature=0.1,
                    response_mime_type="application/json",
                    response_schema=response_schema,
                ),
            ),
        )
        raw = response.candidates[0].content.parts[0].text.strip()
        data = json.loads(raw)
        return bool(data.get("merge"))
    except (json.JSONDecodeError, KeyError, IndexError, AttributeError) as e:
        _LOGGER.info("Arbitration bad JSON for %d vs %d: %s", a_id, b_id, e)
        return None
    except Exception as e:
        _LOGGER.warning("Arbitration failed for %d vs %d: %s", a_id, b_id, e)
        return None


async def _merge_pair(
    pool, structured, a_id: int, b_id: int, sim: float, *, reason: str, result: MergeSweepResult
) -> bool:
    """Pick winner+loser, live-re-check, apply via A4 soft-delete + save_preference revive, audit."""
    a = await _load_pref_by_id(pool, a_id)
    b = await _load_pref_by_id(pool, b_id)
    if not a or not b:
        result.aborted_mid_merge += 1
        _LOGGER.info("Merge aborted: row tombstoned mid-sweep (a=%d, b=%d)", a_id, b_id)
        return False

    winner, loser = _pick_winner(a, b)
    merged_value = _merge_values(winner["value"], loser["value"])
    value_changed = merged_value != winner["value"]

    # Re-check winner is still live right before write (post-review §3.2).
    fresh = await _load_pref_by_id(pool, winner["id"])
    if fresh is None:
        result.aborted_mid_merge += 1
        _LOGGER.info("Merge aborted: winner %d forgotten mid-sweep", winner["id"])
        return False

    person_name = winner["person_name"]

    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO preference_merges
               (loser_id, winner_id, loser_key, loser_value,
                winner_key, winner_value_before, winner_value_after, similarity, reason)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
            loser["id"],
            winner["id"],
            loser["key"],
            loser["value"],
            winner["key"],
            winner["value"],
            merged_value,
            float(sim),
            reason,
        )

    # Soft-delete loser.
    await structured.delete_preference(person_name, loser["key"])

    # Update winner value + clear stale embedding (content may have changed).
    if value_changed:
        await structured.save_preference(
            person_name=person_name,
            key=winner["key"],
            value=merged_value,
            source="dedup_merge",
        )
        async with pool.acquire() as conn:
            await conn.execute("UPDATE preferences SET embedding = NULL WHERE id = $1", winner["id"])

    _LOGGER.info(
        "Merged pref id=%d → winner id=%d (sim=%.3f, reason=%s)",
        loser["id"],
        winner["id"],
        sim,
        reason,
    )
    return True


def _pick_winner(a: dict, b: dict) -> tuple[dict, dict]:
    """Winner: higher confidence; tie-break by later last_reinforced."""
    ac, bc = float(a.get("confidence") or 0), float(b.get("confidence") or 0)
    if ac != bc:
        return (a, b) if ac > bc else (b, a)
    a_ts, b_ts = a.get("last_reinforced"), b.get("last_reinforced")
    if a_ts and b_ts:
        return (a, b) if a_ts >= b_ts else (b, a)
    return (a, b)


def _merge_values(winner: str, loser: str) -> str:
    """Winner value + loser value concat (unless loser is already a substring)."""
    w = (winner or "").strip()
    lv = (loser or "").strip()
    if not lv or lv.lower() in w.lower():
        return w
    if not w:
        return lv
    candidate = f"{w}; {lv}"
    if len(candidate) > MAX_VALUE_LEN:
        return w  # keep winner unchanged; reason row notes truncation
    return candidate
