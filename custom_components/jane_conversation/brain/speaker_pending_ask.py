"""S3.0 (JANE-71) — Step 4 pending-ask state machine.

Spans two turns. Triggered from `tools/registry.py:execute_tool` when:
  policy gate denies + device_id known + no active speaker session ≥ 0.7
  + multiple persons home → write pending-ask + raise `SpeakerAskRequired`,
  which `brain/engine.py:think()` catches and turns into the Hebrew
  response "מי מדבר?".

Next turn: `conversation.py:async_process` reads the pending payload, matches
the user's reply to a known person via `match_known_person` (word-boundary
regex, ambiguous → None), recovers identity at confidence 0.85, replays the
original_request through `think()`.
"""

from __future__ import annotations

import json
import logging
import re
import time

from homeassistant.core import HomeAssistant

from ..const import (
    DOMAIN,
    PENDING_ASK_TTL_SECONDS,
    REDIS_KEY_PENDING_ASK_PREFIX,
)
from .speaker_helpers import get_redis

_LOGGER = logging.getLogger(__name__)

# What execute_tool emits as the tool result when raising SpeakerAskRequired
# would lose information (we still want a deterministic string for the LLM).
ASK_RESPONSE_HEBREW = "מי מדבר?"


class SpeakerAskRequired(Exception):
    """Raised by `execute_tool` when a low-confidence sensitive call should
    convert into a "מי מדבר?" turn instead of a deny.

    `brain/engine.py:think()` catches this exception, abandons the current
    tool-call iteration, and returns `ASK_RESPONSE_HEBREW` directly to
    `conversation.py`. The pending payload is already in Redis at raise time.
    """


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
    """Match `reply_text` against persons.name with word-boundary semantics.

    Returns the canonical name iff EXACTLY ONE known name appears as a whole
    word in the reply. Multiple matches → None (ambiguous; caller re-asks).
    Zero matches → None. Word-boundary prevents `"Al"` matching inside
    `"alice"`. Hebrew names work because `\\b` in Python regex tokenizes on
    Unicode word characters.
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
    candidates: list[str] = []
    for person in persons:
        name = person.get("name", "") or ""
        if not name:
            continue
        pattern = rf"(?:^|\W){re.escape(name.lower())}(?:$|\W)"
        if re.search(pattern, text_lower):
            candidates.append(name)
    if len(candidates) == 1:
        return candidates[0]
    return None  # 0 matches OR ambiguous (>1) → caller re-asks
