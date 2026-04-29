"""S3.2 (JANE-45) — dispatch helper for [PROACTIVE] turns.

Extracted from `conversation.py` to keep that file under the 300-line cap.

Five branches, all but the last returning an empty TTS response — the
routing decision (voice / notification / silent) is controlled by the
LLM through its choice of tools, not by this helper returning speech:

1. Drop — payload too malformed to act on. Audit `dropped_malformed_payload`.
2. Mode-gate (D9) — `MODE_RULES[mode]['proactive']` is False. Audit
   `suppressed_by_mode`. No LLM call.
3. Dismissal streak (D4) — 3 dismissals of this trigger type in 7 days.
   Audit `suppressed_by_streak`. No LLM call.
4. Budget exhausted (D4 + D13) — daily 2-per-day speech cap reached.
   Pass-through with a system-prompt override note so the LLM downgrades
   voice→notification for non-critical events. Critical urgency may still
   speak (D8 safety bypass).
5. Dispatch — invoke think() with is_proactive=True. The LLM is expected
   to call log_proactive_decision exactly once via tool.

History and working_memory are NOT updated for proactive turns —
[PROACTIVE] turns aren't conversation; mixing them into history would
pollute the LLM's view of what the user has said.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from homeassistant.components import conversation
from homeassistant.components.conversation import ConversationResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent

from ..const import DOMAIN
from ..memory.household_mode import get_active_mode
from ..memory.proactive_decisions import record_proactive_decision
from ..modes import MODE_RULES
from . import think
from .proactive import (
    _parse_proactive_payload,
    check_dismissal_streak,
    check_speech_budget,
)

_LOGGER = logging.getLogger(__name__)


async def handle_proactive_dispatch(
    hass: HomeAssistant,
    user_input: conversation.ConversationInput,
    user_text: str,
    conversation_id: str,
    get_client: Callable[[], Awaitable],
    tavily_api_key: str | None,
) -> ConversationResult:
    """Run the [PROACTIVE] flow: parse → mode-gate → dispatch.

    `get_client` is a zero-arg awaitable returning a Gemini client. It is
    invoked lazily, AFTER the parse + mode-gate checks, so suppressed
    turns don't pay client-construction cost.
    """
    jane = hass.data.get(DOMAIN)
    pg_pool = getattr(jane, "pg_pool", None)
    empty = intent.IntentResponse(language=user_input.language or "he")
    empty.async_set_speech("")

    # 1. Parse with explicit fallbacks (D2).
    payload = _parse_proactive_payload(user_text, hass)
    if payload is None:
        _LOGGER.info("Dropping malformed [PROACTIVE]: %r", user_text)
        await record_proactive_decision(
            pg_pool,
            trigger="unknown",
            mode=get_active_mode(hass),
            action_taken="dropped_malformed_payload",
            reasoning="missing description AND time",
            routed_via=None,
        )
        return ConversationResult(conversation_id=conversation_id, response=empty)

    # 2. Mode gate (D9) — short-circuit BEFORE think().
    active_mode = payload.mode
    trigger_type = payload.description.split()[0] if payload.description else "unknown"
    if not MODE_RULES.get(active_mode, {}).get("proactive", True):
        _LOGGER.info(
            "Suppressing [PROACTIVE] in mode=%s (proactive=False): %s",
            active_mode,
            payload.description,
        )
        await record_proactive_decision(
            pg_pool,
            trigger=trigger_type,
            mode=active_mode,
            action_taken="suppressed_by_mode",
            reasoning=f"mode={active_mode} has proactive=False",
            person=payload.person,
            routed_via=None,
        )
        return ConversationResult(conversation_id=conversation_id, response=empty)

    # 3. Dismissal streak (D4) — short-circuit per-trigger-type when the
    # household has dismissed this same trigger 3+ times in 7 days. Mirrors
    # the mode-gate shape: audit row, no LLM call, return empty.
    if not await check_dismissal_streak(pg_pool, trigger_type):
        _LOGGER.info(
            "Suppressing [PROACTIVE] for trigger=%s (3-strike dismissal streak)",
            trigger_type,
        )
        await record_proactive_decision(
            pg_pool,
            trigger=trigger_type,
            mode=active_mode,
            action_taken="suppressed_by_streak",
            reasoning="3-strike dismissal within 7 days",
            person=payload.person,
            routed_via=None,
        )
        return ConversationResult(conversation_id=conversation_id, response=empty)

    # 4. Speech-budget context (D4 + D13) — pre-compute and pass to think()
    # so the LLM can downgrade voice→notification when the daily cap is hit.
    # The increment side is owned by handle_log_proactive_decision (D4: only
    # voice non-critical consumes a token); this is the read-side wiring.
    redis = getattr(jane, "redis", None)
    budget_available = await check_speech_budget(hass, redis)

    # 5. Dispatch to think() with is_proactive=True so the SYSTEM_PROMPT
    # gets the proactive instructions appended.
    client = await get_client()
    if jane:
        jane.gemini_client = client
    working_memory = getattr(jane, "working_memory", None)
    try:
        response_text = await think(
            client,
            user_text,
            "system",  # no real speaker
            hass,
            None,  # no history
            tavily_api_key,
            working_memory,
            confidence=1.0,
            device_id=None,
            conversation_id=conversation_id,
            is_proactive=True,
            proactive_budget_exhausted=not budget_available,
        )
    except Exception as e:  # noqa: BLE001
        _LOGGER.warning("[PROACTIVE] think() failed: %s", e)
        return ConversationResult(conversation_id=conversation_id, response=empty)

    _LOGGER.info("[PROACTIVE] response: %s", response_text)
    # Speech goes through tts_announce / send_notification tool calls
    # invoked inside think(); we deliberately don't return response_text
    # via TTS here — that would surface internal LLM text on the
    # conversation channel and bypass route_alert's mode gating.
    return ConversationResult(conversation_id=conversation_id, response=empty)
