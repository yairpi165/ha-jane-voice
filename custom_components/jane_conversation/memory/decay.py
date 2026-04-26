"""B3 (JANE-83) — category-aware multiplicative preference decay.

Extracted from ``structured.py`` to keep the latter under the 300-line cap and
to match the one-concern-per-file pattern used by ``correction_lifecycle.py``,
``consolidation_pass.py``, ``preference_optimizer.py``, etc.

Three disjoint UPDATEs per call:

- **volatile** (catch-all incl. ``note_*`` slugs and any unknown key):
  3% / day after 7 d grace.
- **stable** (routines, screen time, greeting/goodnight styles):
  1% / day after 14 d grace.
- **permanent** (football_teams, action_style, tool_usage_policy, ...):
  0.2% / day after 30 d grace.

Decay is multiplicative (``confidence × (1 − rate)``), with a ``confidence >
0.05`` SQL floor so a row stops further decay once effectively gone. The
visibility cliff at ``min_confidence=0.5`` (in ``load_preferences``) is a
separate read-path concern — see ``sequential-wishing-whisper.md``.

The three UPDATEs are mutually exclusive by construction: volatile uses
``key != ALL(STABLE ∪ PERMANENT)`` while the other two key against disjoint
explicit lists. ``test_decay_categories_are_disjoint`` locks the disjoint
invariant so an accidental overlap would surface in CI rather than as silent
double-decay.
"""

from __future__ import annotations

import logging

from ..const import PERMANENT_KEYS, STABLE_KEYS

_LOGGER = logging.getLogger(__name__)

_SQL_VOLATILE = """
UPDATE preferences
   SET confidence = confidence * (1 - 0.03), updated_at = NOW()
 WHERE inferred = TRUE AND confidence > 0.05 AND deleted_at IS NULL
   AND last_reinforced < NOW() - INTERVAL '7 days'
   AND key != ALL($1::text[])
"""

_SQL_STABLE = """
UPDATE preferences
   SET confidence = confidence * (1 - 0.01), updated_at = NOW()
 WHERE inferred = TRUE AND confidence > 0.05 AND deleted_at IS NULL
   AND last_reinforced < NOW() - INTERVAL '14 days'
   AND key = ANY($1::text[])
"""

_SQL_PERMANENT = """
UPDATE preferences
   SET confidence = confidence * (1 - 0.002), updated_at = NOW()
 WHERE inferred = TRUE AND confidence > 0.05 AND deleted_at IS NULL
   AND last_reinforced < NOW() - INTERVAL '30 days'
   AND key = ANY($1::text[])
"""


def _count(result: str) -> int:
    """Parse asyncpg's ``"UPDATE N"`` status string."""
    return int(result.split()[-1]) if result else 0


async def decay_preferences(pool) -> tuple[int, int, int]:
    """Run the three category UPDATEs. Returns ``(volatile, stable, permanent)``."""
    excluded = list(STABLE_KEYS) + list(PERMANENT_KEYS)
    async with pool.acquire() as conn:
        r_v = await conn.execute(_SQL_VOLATILE, excluded)
        r_s = await conn.execute(_SQL_STABLE, list(STABLE_KEYS))
        r_p = await conn.execute(_SQL_PERMANENT, list(PERMANENT_KEYS))
    return _count(r_v), _count(r_s), _count(r_p)
