"""B2 — Weekly memory consolidation pass with diff reports (JANE-81).

Distinct from ``memory/consolidation.py`` (events→episodes, S1.4). This
module runs a memory-hygiene pass weekly (or threshold-triggered after
50+ new prefs):

1. Snapshot live + tombstoned counts.
2. Savepoint: purge tombstones older than ``TOMBSTONE_RETENTION_DAYS``,
   skipping rows still referenced by recent ``preference_merges``.
   Rollback resets all four purge counters to zero.
3. Trim expired entries from the ``jane:recently_removed_facts`` ZSET.
4. Re-run B1 dedup at its existing thresholds (per-person 1h debounce).
5. Reset threshold counter + last-run timestamp in Redis.
6. Emit a ``memory_health_samples`` row with the B5 metrics + an
   ``extra.consolidation = {...}`` JSONB block.

The ZSET is populated at ``forget_memory`` time (see ``memory_tools.py``);
this module only trims expired entries.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from . import preference_optimizer
from .health import collect_health_report, persist_health_report
from .structured import _normalize_pref_key

_LOGGER = logging.getLogger(__name__)

# ---- Redis keys ---------------------------------------------------------

RECENTLY_REMOVED_KEY = "jane:recently_removed_facts"  # Redis ZSET, score=unix_ts
PREFS_ADDED_COUNTER_KEY = "jane:prefs_added_since_consolidation"
LAST_CONSOLIDATION_KEY = "jane:last_consolidation_ts"

# ---- Knobs (kept module-level for easy override / introspection) --------

RECENTLY_REMOVED_TTL_SECONDS = 30 * 86400  # aligned with tombstone retention
RECENTLY_REMOVED_PROMPT_CAP = 30
THRESHOLD_NEW_PREFS = 50
THRESHOLD_DEBOUNCE_HOURS = 24
TOMBSTONE_RETENTION_DAYS = 30


@dataclass
class ConsolidationDiff:
    """Snapshot + transformations of one consolidation pass."""

    run_at: datetime
    trigger: str  # "weekly" | "threshold" | "manual"
    duration_ms: int = 0
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)
    tombstones_purged_prefs: int = 0
    tombstones_purged_entries: int = 0
    merges_auto: int = 0
    merges_arbitrated: int = 0
    removed_keys_sample: list[str] = field(default_factory=list)  # capped at 100
    removed_keys_total: int = 0  # honest total — sample may be truncated
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """One-line English summary for ``_LOGGER.info``."""
        return (
            f"trigger={self.trigger}, "
            f"purged_prefs={self.tombstones_purged_prefs}, "
            f"purged_entries={self.tombstones_purged_entries}, "
            f"merges={self.merges_auto + self.merges_arbitrated} "
            f"({self.merges_auto} auto, {self.merges_arbitrated} arbitrated), "
            f"duration={self.duration_ms}ms, errors={len(self.errors)}"
        )

    def to_extra_dict(self) -> dict:
        """JSON-serializable form for ``memory_health_samples.extra.consolidation``."""
        return {
            "run_at": self.run_at.isoformat(),
            "trigger": self.trigger,
            "duration_ms": self.duration_ms,
            "before": self.before,
            "after": self.after,
            "tombstones_purged_prefs": self.tombstones_purged_prefs,
            "tombstones_purged_entries": self.tombstones_purged_entries,
            "merges_auto": self.merges_auto,
            "merges_arbitrated": self.merges_arbitrated,
            "removed_keys_sample": self.removed_keys_sample,
            "removed_keys_total": self.removed_keys_total,
            "errors": self.errors,
        }


_SNAPSHOT_SQL = """
    SELECT
        (SELECT COUNT(*) FROM preferences WHERE deleted_at IS NULL)        AS prefs_live,
        (SELECT COUNT(*) FROM preferences WHERE deleted_at IS NOT NULL)    AS prefs_tombstoned,
        (SELECT COUNT(*) FROM memory_entries WHERE deleted_at IS NULL)     AS entries_live,
        (SELECT COUNT(*) FROM memory_entries WHERE deleted_at IS NOT NULL) AS entries_tombstoned
"""


async def _snapshot_counts(pool) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_SNAPSHOT_SQL)
    return {
        "prefs_live": int(row["prefs_live"] or 0),
        "prefs_tombstoned": int(row["prefs_tombstoned"] or 0),
        "entries_live": int(row["entries_live"] or 0),
        "entries_tombstoned": int(row["entries_tombstoned"] or 0),
    }


async def run_consolidation_pass(
    pool,
    redis,
    structured,
    hass,
    client,
    *,
    trigger: str,
) -> ConsolidationDiff:
    """Run the full B2 pass; returns a populated ``ConsolidationDiff``.

    Failures inside the savepoint roll back the purge counters to zero
    (consistent with PG semantics — both DELETEs roll back together).
    Failures elsewhere (trim, dedup) are captured in ``diff.errors`` but
    don't abort the pass.
    """
    started = datetime.now(UTC)
    diff = ConsolidationDiff(run_at=started, trigger=trigger)

    diff.before = await _snapshot_counts(pool)

    async with pool.acquire() as conn, conn.transaction():
        try:
            async with conn.transaction():  # asyncpg auto-promotes to SAVEPOINT
                # Single atomic fetch-with-RETURNING — no double-DELETE risk.
                purged_rows = await conn.fetch(
                    "DELETE FROM preferences "
                    "WHERE deleted_at IS NOT NULL "
                    "  AND deleted_at < NOW() - $1::interval "
                    "  AND id NOT IN (SELECT loser_id FROM preference_merges "
                    "                  WHERE merged_at > NOW() - INTERVAL '90 days') "
                    "RETURNING person_name, key",
                    timedelta(days=TOMBSTONE_RETENTION_DAYS),
                )
                diff.tombstones_purged_prefs = len(purged_rows)
                diff.removed_keys_total = len(purged_rows)
                diff.removed_keys_sample = [
                    f"{r['person_name']}:{_normalize_pref_key(r['key'])}" for r in purged_rows[:100]
                ]

                r = await conn.execute(
                    "DELETE FROM memory_entries WHERE deleted_at IS NOT NULL   AND deleted_at < NOW() - $1::interval",
                    timedelta(days=TOMBSTONE_RETENTION_DAYS),
                )
                diff.tombstones_purged_entries = int(r.split()[-1]) if r else 0
        except Exception as e:
            _LOGGER.warning("Tombstone purge savepoint rolled back: %s", e)
            diff.errors.append(f"purge: {e}")
            # Savepoint rollback restores BOTH preferences + memory_entries DELETEs,
            # so all four counters must reset to match the rolled-back state.
            diff.removed_keys_sample = []
            diff.removed_keys_total = 0
            diff.tombstones_purged_prefs = 0
            diff.tombstones_purged_entries = 0

    # Trim expired ZSET entries (housekeeping). Wrapped in try/except so a Redis
    # hiccup doesn't block sweep_all — consistent with the forget_memory write.
    try:
        cutoff = int(started.timestamp()) - RECENTLY_REMOVED_TTL_SECONDS
        await redis.zremrangebyscore(RECENTLY_REMOVED_KEY, 0, cutoff)
    except Exception as e:
        _LOGGER.debug("ZSET trim failed (non-fatal): %s", e)
        diff.errors.append(f"trim: {e}")

    # B1 dedup at existing thresholds. Has its own per-person 1h debounce
    # (preference_optimizer.py:80-92) so threshold-triggered runs don't storm.
    try:
        sweep_results = await preference_optimizer.sweep_all(pool, client, hass, structured)
        diff.merges_auto = sum(r.auto_merges for r in sweep_results.values())
        diff.merges_arbitrated = sum(r.arbitrated_merges for r in sweep_results.values())
    except Exception as e:
        _LOGGER.warning("B1 dedup sweep failed inside consolidation: %s", e)
        diff.errors.append(f"dedup: {e}")

    # Reset threshold state (best-effort).
    try:
        await redis.set(PREFS_ADDED_COUNTER_KEY, "0")
        await redis.set(LAST_CONSOLIDATION_KEY, started.isoformat())
    except Exception as e:
        _LOGGER.debug("Redis state reset failed (non-fatal): %s", e)
        diff.errors.append(f"reset: {e}")

    diff.after = await _snapshot_counts(pool)
    diff.duration_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)

    # Emit a fresh memory_health_samples row carrying the standard 5 metrics
    # plus the consolidation diff in extra. The B5 collector handles the metrics.
    try:
        report = await collect_health_report(pool, days=7)
        report.extra = {"consolidation": diff.to_extra_dict()}
        await persist_health_report(pool, report)
    except Exception as e:
        _LOGGER.warning("Failed to emit consolidation health row: %s", e)
        diff.errors.append(f"emit: {e}")

    return diff


async def should_trigger_threshold(redis) -> bool:
    """True iff counter ≥ THRESHOLD_NEW_PREFS AND last run > THRESHOLD_DEBOUNCE_HOURS ago."""
    try:
        raw = await redis.get(PREFS_ADDED_COUNTER_KEY)
    except Exception:
        return False
    counter = int(raw) if raw else 0
    if counter < THRESHOLD_NEW_PREFS:
        return False
    try:
        last_ts_raw = await redis.get(LAST_CONSOLIDATION_KEY)
    except Exception:
        last_ts_raw = None
    if last_ts_raw:
        try:
            last_ts = datetime.fromisoformat(last_ts_raw.decode() if isinstance(last_ts_raw, bytes) else last_ts_raw)
            if datetime.now(UTC) - last_ts < timedelta(hours=THRESHOLD_DEBOUNCE_HOURS):
                return False
        except (ValueError, TypeError):
            pass
    return True


async def fetch_recently_removed_for_prompt(redis, *, limit: int = RECENTLY_REMOVED_PROMPT_CAP) -> list[str]:
    """Top-N most-recently-removed keys (by score = unix_ts), capped at ``limit``."""
    try:
        rows = await redis.zrevrange(RECENTLY_REMOVED_KEY, 0, max(0, limit - 1))
    except Exception:
        return []
    return [r.decode() if isinstance(r, bytes) else r for r in rows]


async def is_recently_removed(redis, person: str, normalized_key: str) -> bool:
    """True iff ``{person}:{normalized_key}`` is in the ZSET. Used by OpApplier guard."""
    try:
        score = await redis.zscore(RECENTLY_REMOVED_KEY, f"{person}:{normalized_key}")
    except Exception:
        return False
    return score is not None


async def backfill_last_consolidation_ts(pool, redis) -> None:
    """On startup, rehydrate ``LAST_CONSOLIDATION_KEY`` from PG if Redis is empty.

    Looks at the most recent ``memory_health_samples`` row whose ``extra``
    contains a ``consolidation`` block, and uses its ``run_at`` as the
    last-run timestamp. Prevents the threshold trigger from over-firing
    in the hour after a Redis restart.
    """
    try:
        existing = await redis.get(LAST_CONSOLIDATION_KEY)
        if existing:
            return
    except Exception:
        return  # Redis down — nothing to do

    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT extra->'consolidation'->>'run_at' AS ts "
                "FROM memory_health_samples "
                "WHERE extra ? 'consolidation' "
                "ORDER BY generated_at DESC LIMIT 1"
            )
    except Exception as e:
        _LOGGER.debug("backfill_last_consolidation_ts: PG lookup failed: %s", e)
        return

    if not row or not row["ts"]:
        return
    try:
        await redis.set(LAST_CONSOLIDATION_KEY, row["ts"])
        _LOGGER.info("Rehydrated %s from latest consolidation row", LAST_CONSOLIDATION_KEY)
    except Exception:
        pass
