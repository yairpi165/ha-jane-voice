"""B4 — Corrections lifecycle (JANE-83).

A daily sweep that ages ``events.event_type='correction'`` rows through a
time-based state machine: ``open → applied → resolved → DELETE``. No link to
``memory_ops.UPDATE`` (which is JANE-91 territory) — transitions fire purely on
elapsed time + same-user non-recurrence.

The state machine:

- **open → applied** at age 7 d if no other correction from the same user has
  been logged in the last 7 d (rolling absolute window).
- **applied → resolved** after another 30 d (so age ≥ 37 d total).
- **open → resolved (force-close)** at age 90 d as a safety cap; tagged
  ``metadata.auto_close = true`` so the diff still distinguishes "naturally
  resolved" from "we gave up waiting".
- **resolved DELETE** once ``resolved_at`` is older than 30 d.

Sweep concurrency is opportunistic: the four statements act on disjoint filtered
subsets, so no transaction is needed. A correction inserted mid-sweep just lands
in the next day's pass.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

_SQL_OPEN_TO_APPLIED = """
UPDATE events e SET status = 'applied'
 WHERE e.event_type = 'correction'
   AND e.status = 'open'
   AND e.timestamp < NOW() - INTERVAL '7 days'
   AND NOT EXISTS (
       SELECT 1 FROM events e2
        WHERE e2.event_type = 'correction'
          AND e2.user_name IS NOT DISTINCT FROM e.user_name
          AND e2.id != e.id
          AND e2.timestamp > e.timestamp
          AND e2.timestamp > NOW() - INTERVAL '7 days'
   )
"""

_SQL_APPLIED_TO_RESOLVED = """
UPDATE events SET status = 'resolved', resolved_at = NOW()
 WHERE event_type = 'correction'
   AND status = 'applied'
   AND timestamp < NOW() - INTERVAL '37 days'
"""

_SQL_FORCE_CLOSE = """
UPDATE events
   SET status = 'resolved',
       resolved_at = NOW(),
       metadata = COALESCE(metadata, '{}'::jsonb) || '{"auto_close": true}'::jsonb
 WHERE event_type = 'correction'
   AND status = 'open'
   AND timestamp < NOW() - INTERVAL '90 days'
"""

_SQL_DELETE_RESOLVED = """
DELETE FROM events
 WHERE event_type = 'correction'
   AND status = 'resolved'
   AND resolved_at < NOW() - INTERVAL '30 days'
"""

_SQL_STATUS_COUNTS = """
SELECT status, COUNT(*) AS cnt
  FROM events
 WHERE event_type = 'correction'
 GROUP BY status
"""


@dataclass
class LifecycleSummary:
    """One sweep's transition counts. All fields zero on a quiet day."""

    transitioned_to_applied: int = 0
    transitioned_to_resolved: int = 0
    force_closed: int = 0
    deleted: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "transitioned_to_applied": self.transitioned_to_applied,
            "transitioned_to_resolved": self.transitioned_to_resolved,
            "force_closed": self.force_closed,
            "deleted": self.deleted,
        }

    def any(self) -> bool:
        return bool(self.transitioned_to_applied or self.transitioned_to_resolved or self.force_closed or self.deleted)


def _count(result: str) -> int:
    """Parse asyncpg's ``"UPDATE N"`` / ``"DELETE N"`` status string."""
    return int(result.split()[-1]) if result else 0


async def sweep_corrections(pool) -> LifecycleSummary:
    """Run the four lifecycle transitions in order. Returns row counts.

    Order matters: we run ``applied → resolved`` BEFORE ``open → applied`` so a
    given pass advances each row by at most one state. Reversing would let a
    fresh open jump straight to resolved if the timing aligned. Force-close runs
    after the natural transitions, and DELETE runs last so it picks up rows
    just-resolved by the previous statement (their ``resolved_at`` is now, so
    they won't be deleted today, but the ordering is the safe one).
    """
    summary = LifecycleSummary()
    async with pool.acquire() as conn:
        r_app_to_res = await conn.execute(_SQL_APPLIED_TO_RESOLVED)
        r_open_to_app = await conn.execute(_SQL_OPEN_TO_APPLIED)
        r_force = await conn.execute(_SQL_FORCE_CLOSE)
        r_delete = await conn.execute(_SQL_DELETE_RESOLVED)
    summary.transitioned_to_applied = _count(r_open_to_app)
    summary.transitioned_to_resolved = _count(r_app_to_res)
    summary.force_closed = _count(r_force)
    summary.deleted = _count(r_delete)
    return summary


async def correction_status_counts(pool) -> dict[str, int]:
    """Return ``{status: count}`` for ``event_type='correction'`` rows.

    Used by ``consolidation_pass`` to populate ``HealthReport.extra
    .corrections_lifecycle`` so the JSONB blob carries the lifecycle snapshot
    next to the consolidation diff.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(_SQL_STATUS_COUNTS)
    return {r["status"]: int(r["cnt"]) for r in rows}
