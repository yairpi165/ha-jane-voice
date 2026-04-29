"""Household mode tool handlers — S3.1 (JANE-42).

Owns the LLM-callable surface for `set_household_mode`. The actual flip +
audit-row write live in `memory.household_mode`; this module is just the
glue that pulls the runtime context (pg_pool, triggered_by) off
`hass.data[DOMAIN]` and shapes the Hebrew confirmation string Jane reads.
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

from ...const import DOMAIN
from ...memory.household_mode import set_active_mode
from ...modes import HOUSEHOLD_MODES

_LOGGER = logging.getLogger(__name__)


async def handle_set_household_mode(hass: HomeAssistant, args: dict) -> str:
    """Switch Jane's active household mode and log the transition.

    Returns a Hebrew string for the LLM to read aloud — confirmation on
    success, deny on validation / service failure. Never raises.
    """
    mode = (args.get("mode") or "").strip()
    trigger = (args.get("trigger") or "voice").strip()
    reason = args.get("reason")

    if mode not in HOUSEHOLD_MODES:
        return f"מצב לא ידוע: {mode!r}. המצבים הזמינים: {', '.join(HOUSEHOLD_MODES)}."

    jane = hass.data.get(DOMAIN)
    pg_pool = getattr(jane, "pg_pool", None)
    # `triggered_by` is intentionally None here. The earlier draft used
    # ``args.get("triggered_by")``, but ``triggered_by`` is NOT in the
    # ``TOOL_SET_HOUSEHOLD_MODE`` schema — Gemini can't pass it, and we
    # explicitly don't want the LLM to hallucinate a speaker name.
    # The resolved speaker (JANE-71's ``user_name``) lives one layer up
    # in ``execute_tool`` but doesn't currently thread through the
    # ``_HANDLER_MAP`` dispatch boundary. S3.2 owns that wiring (it has
    # to do it anyway for the false_positive_alert_rate KPI baseline);
    # until then, voice-initiated rows carry ``triggered_by=NULL`` and
    # the trigger phrase in ``reason`` is the per-flip audit signal.
    triggered_by = None

    deny = await set_active_mode(
        hass,
        pg_pool,
        new_mode=mode,
        trigger=trigger,
        triggered_by=triggered_by,
        reason=reason,
    )
    if deny is not None:
        return deny
    return f"עברתי למצב {mode}."
