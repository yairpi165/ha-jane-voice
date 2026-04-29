"""Proactive decision audit writer — S3.2 (JANE-45 / D3).

A pure ``events`` writer for ``event_type='proactive_decision'`` rows.
Lives outside ``EpisodicStore`` to keep ``episodic.py`` under the 300-line
cap, mirroring the ``correction_lifecycle.py`` pattern from B4 / JANE-83.

Every [PROACTIVE] message produces exactly one row from this writer —
including suppressions (mode-gate / dropped-malformed / 3-strike
dismissal). A missing row means the integration crashed, not that
"nothing happened". The full Decision Log + decision_outcomes schema is
JANE-46 / S4.1; this is the lightweight Phase-3 stand-in.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

_LOGGER = logging.getLogger(__name__)


async def record_proactive_decision(
    pg_pool,
    *,
    trigger: str,
    mode: str,
    action_taken: str,
    reasoning: str,
    person: str | None = None,
    urgency: str = "normal",
    routed_via: str | None = None,
) -> int | None:
    """Insert one ``proactive_decision`` row in ``events`` and return its id.

    ``description`` is a one-line human-readable summary used by the
    existing event-query path. ``metadata`` carries the full structured
    fields as JSONB so KPI queries can group/filter without schema
    changes.

    ``routed_via`` is None for suppressions / drops; "voice" /
    "notification" / "silent" for actions actually taken. The returned
    event_id is the FK target for ``user_overrides.proactive_decision_id``
    when the user later dismisses this row's action.

    Failure-soft: returns None on PG error so a logging miss never breaks
    the response path.
    """
    if pg_pool is None:
        _LOGGER.debug("record_proactive_decision skipped — pg_pool not yet wired")
        return None

    ts = datetime.now(tz=UTC)
    description = f"{trigger} → {action_taken} (mode={mode}" + (f", routed={routed_via}" if routed_via else "") + ")"
    metadata = json.dumps(
        {
            "trigger": trigger,
            "mode": mode,
            "action_taken": action_taken,
            "reasoning": reasoning,
            "person": person,
            "urgency": urgency,
            "routed_via": routed_via,
        },
        ensure_ascii=False,
    )

    try:
        async with pg_pool.acquire() as conn:
            return await conn.fetchval(
                """INSERT INTO events (timestamp, event_type, user_name, description, metadata)
                   VALUES ($1, 'proactive_decision', $2, $3, $4::jsonb)
                   RETURNING id""",
                ts,
                person,
                description,
                metadata,
            )
    except Exception as e:  # noqa: BLE001
        _LOGGER.warning("Failed to record proactive_decision (%s → %s): %s", trigger, action_taken, e)
        return None
