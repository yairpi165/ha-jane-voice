"""B5 — Weekly memory health report (JANE-82).

Snapshots five metrics into ``memory_health_samples`` for use by JANE-81
(consolidation diff reports), JANE-83 (staleness/corrections), and the
eventual Self-Model layer (Blueprint §4.2).

Metrics, in order:

1. ``prefs_per_person`` — live preference count per person (stock, not delta).
2. ``extraction_calls`` — distinct extractor invocations in window.
   Counts distinct ``session_id`` (NOT ops emitted), excluding
   ``tool-forget-*`` sessions and NULL session_id.
3. ``consolidation_ops`` — episodes PRODUCED in window (uses
   ``episodes.created_at``, not ``start_ts`` which is event-time).
4. ``corrections`` — ``memory_ops`` rows with ``op = 'UPDATE'`` (per ADR-2,
   each UPDATE rewrites an existing fact, so it is a correction by
   definition).
5. ``forget_invocations`` — ``memory_ops`` rows with ``op = 'DELETE'`` from
   the ``forget_memory`` tool path (``session_id LIKE 'tool-forget-%'``).

Window: floating, ``period_end = NOW()``, ``period_start = NOW() - days``.
No unique index on ``(period_start, period_end)`` — every run inserts a
new row. Restart-induced double-rows are information, not noise.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

_LOGGER = logging.getLogger(__name__)

DEFAULT_WINDOW_DAYS = 7

_SQL_PREFS_PER_PERSON = """
    SELECT person_name, COUNT(*) AS cnt
    FROM preferences
    WHERE deleted_at IS NULL
    GROUP BY person_name
"""

_SQL_EXTRACTION_CALLS = """
    SELECT COUNT(DISTINCT session_id)
    FROM memory_ops
    WHERE op IN ('ADD','UPDATE','DELETE','NOOP')
      AND created_at > NOW() - ($1 * INTERVAL '1 day')
      AND session_id IS NOT NULL
      AND session_id NOT LIKE 'tool-forget-%'
"""

_SQL_CONSOLIDATION_OPS = """
    SELECT COUNT(*) FROM episodes
    WHERE created_at > NOW() - ($1 * INTERVAL '1 day')
"""

_SQL_CORRECTIONS = """
    SELECT COUNT(*) FROM memory_ops
    WHERE op = 'UPDATE'
      AND created_at > NOW() - ($1 * INTERVAL '1 day')
"""

_SQL_FORGET_INVOCATIONS = """
    SELECT COUNT(*) FROM memory_ops
    WHERE op = 'DELETE'
      AND session_id LIKE 'tool-forget-%'
      AND created_at > NOW() - ($1 * INTERVAL '1 day')
"""

_SQL_INSERT_SAMPLE = """
    INSERT INTO memory_health_samples
        (period_start, period_end, prefs_per_person, prefs_total,
         extraction_calls, consolidation_ops, corrections,
         forget_invocations, extra, schema_version)
    VALUES ($1, $2, $3::jsonb, $4, $5, $6, $7, $8, $9::jsonb, $10)
    RETURNING id
"""


@dataclass
class HealthReport:
    """One weekly snapshot of memory subsystem health."""

    period_start: datetime
    period_end: datetime
    prefs_per_person: dict[str, int] = field(default_factory=dict)
    prefs_total: int = 0
    extraction_calls: int = 0
    consolidation_ops: int = 0
    corrections: int = 0
    forget_invocations: int = 0
    # Forward-compat additive slot used by B2 (JANE-81) for the consolidation
    # diff (under the "consolidation" key) and reserved for future per-user
    # breakdowns, latency percentiles, etc. Default {} keeps the existing
    # B5 path identical to v3.28 behavior.
    extra: dict = field(default_factory=dict)
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    schema_version: int = 1


async def collect_health_report(pool, *, days: int = DEFAULT_WINDOW_DAYS) -> HealthReport:
    """Run all five metric queries against a 7-day floating window."""
    period_end = datetime.now(UTC)
    period_start = period_end - timedelta(days=days)

    async with pool.acquire() as conn:
        prefs_rows = await conn.fetch(_SQL_PREFS_PER_PERSON)
        extraction_calls = await conn.fetchval(_SQL_EXTRACTION_CALLS, days)
        consolidation_ops = await conn.fetchval(_SQL_CONSOLIDATION_OPS, days)
        corrections = await conn.fetchval(_SQL_CORRECTIONS, days)
        forget_invocations = await conn.fetchval(_SQL_FORGET_INVOCATIONS, days)

    prefs_per_person = {r["person_name"]: int(r["cnt"]) for r in prefs_rows}
    return HealthReport(
        period_start=period_start,
        period_end=period_end,
        prefs_per_person=prefs_per_person,
        prefs_total=sum(prefs_per_person.values()),
        extraction_calls=int(extraction_calls or 0),
        consolidation_ops=int(consolidation_ops or 0),
        corrections=int(corrections or 0),
        forget_invocations=int(forget_invocations or 0),
        generated_at=period_end,
    )


async def persist_health_report(pool, report: HealthReport) -> int:
    """Insert one row into ``memory_health_samples``. Returns the new id.

    No unique index — every call inserts. Per locked decision (a+B):
    floating window + insert duplicates. Restart-induced double-rows are
    information about the scheduler, not noise to dedup.
    """
    actual_sum = sum(report.prefs_per_person.values())
    if report.prefs_total != actual_sum:
        raise ValueError(f"prefs_total desync: stored={report.prefs_total}, sum={actual_sum}")

    async with pool.acquire() as conn:
        return await conn.fetchval(
            _SQL_INSERT_SAMPLE,
            report.period_start,
            report.period_end,
            json.dumps(report.prefs_per_person, ensure_ascii=False),
            report.prefs_total,
            report.extraction_calls,
            report.consolidation_ops,
            report.corrections,
            report.forget_invocations,
            json.dumps(report.extra, ensure_ascii=False),
            report.schema_version,
        )


# Log lines are English-only; Hebrew is reserved for user-facing TTS/UI.
# When prefs_per_person grows past ~10 households, switch to top-N truncation
# (e.g. "prefs={top 5}, +N others") — YAGNI today, but a known future shape.
def format_for_log(report: HealthReport) -> str:
    """One-line English summary for ``_LOGGER.info``."""
    prefs_pairs = ",".join(f"{k}:{v}" for k, v in sorted(report.prefs_per_person.items()))
    window = f"{report.period_start:%Y-%m-%d}..{report.period_end:%Y-%m-%d}"
    return (
        f"prefs={{{prefs_pairs}}} total={report.prefs_total}, "
        f"extractions={report.extraction_calls}, "
        f"consolidations={report.consolidation_ops}, "
        f"corrections={report.corrections}, "
        f"forgets={report.forget_invocations} (window={window})"
    )
