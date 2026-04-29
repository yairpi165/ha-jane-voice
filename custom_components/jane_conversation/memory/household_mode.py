"""Household Mode read/write — S3.1 (JANE-42).

The active mode lives in `input_select.jane_household_mode` (HA UI helper,
auto-created at setup). This module is the single boundary between the
HA state surface and the rest of Jane:

- `get_active_mode(hass)` — synchronous, O(1) state read. No cache (D15);
  hass.states.get is in-memory. If the helper is missing or in `unknown`
  state (e.g. brand-new install before the auto-create ran) we fall
  back to MODE_NORMAL so the gate path never raises.
- `set_active_mode(hass, pg_pool, ...)` — flip the helper via the
  public input_select.select_option service AND log a row into
  household_mode_transitions. The two are coupled: every UI flip should
  carry an audit row.
- `log_transition(...)` — pure PG write. Failures swallowed (a logging
  miss must not break a working mode change).
- `build_mode_context(active_mode)` — the Hebrew block injected into
  Gemini's system_instruction so Jane prompts her phrasing.
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

from ..modes import HELPER_ENTITY_ID, HOUSEHOLD_MODES, MODE_NORMAL, MODE_RULES

_LOGGER = logging.getLogger(__name__)


def get_active_mode(hass: HomeAssistant) -> str:
    """Read the active household mode from the input_select helper.

    Falls back to MODE_NORMAL when the helper is missing or in `unknown` /
    `unavailable` state so consumers (engine.think(), execute_tool gate)
    never have to handle a None or raise.
    """
    state = hass.states.get(HELPER_ENTITY_ID)
    if state is None:
        return MODE_NORMAL
    value = state.state
    if value in (None, "", "unknown", "unavailable"):
        return MODE_NORMAL
    if value not in HOUSEHOLD_MODES:
        # Foreign value snuck in (manual edit of the helper). Don't crash
        # the gate path; degrade to NORMAL and log so an operator can spot
        # the drift.
        _LOGGER.warning("Unknown household mode value %r — falling back to %s", value, MODE_NORMAL)
        return MODE_NORMAL
    return value


async def log_transition(
    pg_pool,
    *,
    from_mode: str | None,
    to_mode: str,
    trigger: str,
    triggered_by: str | None,
    reason: str | None,
) -> None:
    """Write one row into household_mode_transitions. Failures are swallowed."""
    if pg_pool is None:
        _LOGGER.debug("log_transition skipped — pg_pool not yet wired")
        return
    try:
        async with pg_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO household_mode_transitions
                       (from_mode, to_mode, trigger, triggered_by, reason)
                   VALUES ($1, $2, $3, $4, $5)""",
                from_mode,
                to_mode,
                trigger,
                triggered_by,
                reason,
            )
    except Exception as e:  # noqa: BLE001 — logging is best-effort
        _LOGGER.warning("Failed to log mode transition %s→%s: %s", from_mode, to_mode, e)


async def set_active_mode(
    hass: HomeAssistant,
    pg_pool,
    *,
    new_mode: str,
    trigger: str,
    triggered_by: str | None = None,
    reason: str | None = None,
) -> str | None:
    """Flip the helper to ``new_mode`` and log the transition.

    Returns None on success, or a Hebrew deny-string on validation
    failure (unknown mode). PG write failures are logged but do not
    fail the call — the user-visible flip is what matters; the audit
    row is best-effort.
    """
    if new_mode not in HOUSEHOLD_MODES:
        return f"מצב לא ידוע: {new_mode}"

    from_mode = get_active_mode(hass)
    if from_mode == new_mode:
        # No-op — still log so the audit trail captures redundant requests.
        await log_transition(
            pg_pool,
            from_mode=from_mode,
            to_mode=new_mode,
            trigger=trigger,
            triggered_by=triggered_by,
            reason=reason,
        )
        return None

    try:
        await hass.services.async_call(
            "input_select",
            "select_option",
            {"entity_id": HELPER_ENTITY_ID, "option": new_mode},
            blocking=True,
        )
    except Exception as e:  # noqa: BLE001
        _LOGGER.error("input_select.select_option failed for mode=%s: %s", new_mode, e)
        return f"לא הצלחתי לעבור למצב {new_mode}"

    await log_transition(
        pg_pool,
        from_mode=from_mode,
        to_mode=new_mode,
        trigger=trigger,
        triggered_by=triggered_by,
        reason=reason,
    )
    return None


def build_mode_context(active_mode: str) -> str:
    """Hebrew block describing the current mode + behaviour rules.

    Injected into Gemini's system_instruction so Jane *prompts* her
    behaviour to match the mode (the hard gate at execute_tool is the
    enforcement backstop, not the only signal).
    """
    rules = MODE_RULES.get(active_mode, MODE_RULES[MODE_NORMAL])
    tts_label = "כן" if rules.get("tts", True) else "לא"
    proactive_label = "כן" if rules.get("proactive", True) else "לא"
    return (
        f"מצב נוכחי: {active_mode}.\n"
        f"התנהגות: {rules.get('behavior', '')}\n"
        f"מותר להכריז בקול: {tts_label}. יזימה: {proactive_label}."
    )
