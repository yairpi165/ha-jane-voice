"""S3.0 (JANE-71) — Step 4 pending-ask state machine.

⚠ New scope beyond the v2 lockdown — apply higher review scrutiny here.

Flow (per Notion §"Pending-ask State Machine"):
  1. Triggering action satisfies SENSITIVE/PERSONAL_DATA + no session ≥ 0.7
     + multiple home → Jane responds "מי מדבר?" (no tool calls).
  2. `set_pending_ask` persists `{conversation_id, original_request, ts}`
     under `jane:pending_speaker_ask:{device_id}` with TTL 60s.
  3. Next turn: `check_pending_ask` is called from `conversation.py` BEFORE
     `resolve_speaker`. If the user reply matches a known person name
     (`match_known_person`), resolve at confidence 0.85, store the speaker
     session, and re-execute the original request.
  4. TTL expiry / unknown reply → drop pending, treat as fresh turn at
     fallback confidence 0.3.
"""

from __future__ import annotations

import json
import logging
import time

from homeassistant.core import HomeAssistant

from ..const import (
    DOMAIN,
    PENDING_ASK_TTL_SECONDS,
    REDIS_KEY_PENDING_ASK_PREFIX,
)
from .speaker_helpers import get_redis

_LOGGER = logging.getLogger(__name__)


async def check_pending_ask(hass: HomeAssistant, device_id: str | None) -> dict | None:
    """Fetch a pending ask payload if any. Returns dict or None. Never raises."""
    if not device_id:
        return None
    redis = get_redis(hass)
    if redis is None:
        return None
    key = f"{REDIS_KEY_PENDING_ASK_PREFIX}:{device_id}"
    try:
        raw = await redis.get(key)
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


async def set_pending_ask(
    hass: HomeAssistant,
    device_id: str | None,
    conversation_id: str | None,
    original_request: str,
) -> None:
    """Persist a pending ask for the next turn from this device."""
    if not device_id:
        return
    redis = get_redis(hass)
    if redis is None:
        return
    payload = json.dumps(
        {
            "conversation_id": conversation_id or "",
            "original_request": original_request,
            "ts": time.time(),
        }
    )
    key = f"{REDIS_KEY_PENDING_ASK_PREFIX}:{device_id}"
    try:
        await redis.set(key, payload, ex=PENDING_ASK_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Pending-ask write failed for %s", device_id, exc_info=True)


async def clear_pending_ask(hass: HomeAssistant, device_id: str | None) -> None:
    """Drop the pending key (after consumption or invalid reply)."""
    if not device_id:
        return
    redis = get_redis(hass)
    if redis is None:
        return
    key = f"{REDIS_KEY_PENDING_ASK_PREFIX}:{device_id}"
    try:
        await redis.delete(key)
    except Exception:  # noqa: BLE001
        pass


async def match_known_person(hass: HomeAssistant, reply_text: str) -> str | None:
    """If reply_text contains a known person name, return that name.

    Used by `conversation.py` to recover speaker identity after the user
    answers 'מי מדבר?'. Case-insensitive substring match against
    `persons.name` from the structured store.
    """
    if not reply_text:
        return None
    structured = getattr(hass.data.get(DOMAIN), "structured", None)
    if structured is None:
        return None
    try:
        persons = await structured.load_persons()
    except Exception:  # noqa: BLE001
        return None
    text_lower = reply_text.lower()
    for person in persons:
        name = person.get("name", "")
        if name and name.lower() in text_lower:
            return name
    return None
