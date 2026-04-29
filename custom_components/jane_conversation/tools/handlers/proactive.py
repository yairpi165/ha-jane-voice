"""Proactive decision tool handler — S3.2 (JANE-45).

Owns the LLM-callable surface for ``log_proactive_decision``. Pulls runtime
context (pg_pool, redis) off ``hass.data[DOMAIN]``, calls the writer in
``memory.proactive_decisions``, and advances the speech-budget counter
when the action was voice-routed and non-critical (D4 + D8: critical
urgency bypasses the budget; safety always speaks).
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

from ...brain.proactive import increment_speech_budget
from ...const import DOMAIN
from ...memory.proactive_decisions import record_proactive_decision

_LOGGER = logging.getLogger(__name__)


async def handle_log_proactive_decision(
    hass: HomeAssistant,
    args: dict,
    *,
    user_name: str = "system",
) -> str:
    """Record one proactive_decision row + optionally advance speech budget.

    The ``user_name`` kwarg comes from ``execute_tool``'s special-case
    dispatch (the JANE-42 ``handle_set_household_mode`` precedent).
    Today it's not stored on the row directly — ``person`` from the
    args is the canonical attribution per D2 — but threading it here
    means the next handler that needs it doesn't require a refactor.

    Returns a short Hebrew confirmation. Never raises: the writer is
    failure-soft, and budget increment errors are also swallowed.
    """
    trigger = (args.get("trigger") or "").strip() or "unknown"
    action_taken = (args.get("action_taken") or "").strip()
    reasoning = (args.get("reasoning") or "").strip()
    urgency = (args.get("urgency") or "normal").strip()
    routed_via = (args.get("routed_via") or "none").strip()
    person = args.get("person") or None

    jane = hass.data.get(DOMAIN)
    pg_pool = getattr(jane, "pg_pool", None)
    redis = getattr(jane, "redis", None)

    # Mode is read here, not passed by the LLM, so we have a canonical
    # value at write-time (the LLM may not preserve mode through tool
    # chains). Defensive: any error → "unknown" so the audit row still
    # writes.
    try:
        from ...memory.household_mode import get_active_mode

        mode = get_active_mode(hass)
    except Exception as e:  # noqa: BLE001
        _LOGGER.debug("get_active_mode failed in log_proactive_decision: %s", e)
        mode = "unknown"

    event_id = await record_proactive_decision(
        pg_pool,
        trigger=trigger,
        mode=mode,
        action_taken=action_taken,
        reasoning=reasoning,
        person=person,
        urgency=urgency,
        routed_via=routed_via if routed_via != "none" else None,
    )

    # Advance the speech budget only for voice-routed non-critical actions
    # (D4). Critical urgency bypasses the budget — safety always speaks
    # and shouldn't be capped. Notification/silent/none routes never
    # consume the budget.
    if routed_via == "voice" and urgency != "critical":
        await increment_speech_budget(hass, redis)

    if event_id is None:
        # Audit failed but we still log the call — no point returning an
        # error to the LLM, it would re-invoke. Hebrew confirmation that
        # signals success without lying about persistence.
        _LOGGER.warning("log_proactive_decision audit-write failed for trigger=%s", trigger)
        return "רשמתי בזיכרון המקומי בלבד."
    return "רשמתי."
