"""S3.0 (JANE-71) — Speaker resolution for Jane's voice/text input.

The ladder, per the Notion design page (D1–D12 + V1–V4):
  Step 0  HA context.user_id (filtered against "default")    → 1.0
  Step 1  device_id → device_registry → area → sole resident → 0.85
  Step 2  exactly one person home (jane:presence)            → 0.95
  Step 3  active jane:session:{device_id} (TTL 15m)          → recency-decayed
  Step 4  pending-ask flow ("מי מדבר?" + re-execute)         → see speaker_pending_ask.py
  Step 5  fallback to primary_user                           → 0.3

Step 4 lives in `speaker_pending_ask.py` because it spans two turns and is
driven from `conversation.py` directly (it short-circuits `think`). Internal
helpers live in `speaker_helpers.py` to keep this file under the 300-line cap.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from ..const import (
    REDIS_KEY_SPEAKER_SESSION_PREFIX,
    SPEAKER_SESSION_TTL_SECONDS,
)
from .speaker_helpers import (
    get_primary_user,
    get_redis,
    is_exactly_one_home,
    resolve_sole_resident_in_area,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_USER_NAME = "default"
FALLBACK_CONFIDENCE = 0.3
SESSION_RECENCY_DECAY_FLOOR = 0.5
SESSION_REFRESH_THRESHOLD = 0.7


@dataclass
class SpeakerSession:
    """Per-device speaker session under jane:session:{device_id} (D5)."""

    user_name: str
    conversation_id: str
    ts: float
    confidence: float

    def to_json(self) -> str:
        return json.dumps(
            {
                "user_name": self.user_name,
                "conversation_id": self.conversation_id,
                "ts": self.ts,
                "confidence": self.confidence,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> SpeakerSession | None:
        try:
            data = json.loads(raw)
            return cls(
                user_name=data["user_name"],
                conversation_id=data["conversation_id"],
                ts=float(data["ts"]),
                confidence=float(data["confidence"]),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None


async def resolve_speaker(
    hass: HomeAssistant,
    device_id: str | None,
    conversation_id: str | None,
    ha_user_id: str | None,
) -> tuple[str, float, str]:
    """Resolve the current speaker. Returns (user_name, confidence, layer).

    `layer` is one of "step_0" / "step_1" / "step_2" / "step_3" / "step_5"
    (Step 4 short-circuits `think` from `conversation.py`). Never raises.
    """
    name = await _step_0_ha_context(hass, ha_user_id)
    if name is not None:
        return name, 1.0, "step_0"

    if device_id:
        name = await _step_1_device_area(hass, device_id)
        if name is not None:
            return name, 0.85, "step_1"

    name = await _step_2_presence(hass)
    if name is not None:
        return name, 0.95, "step_2"

    if device_id:
        session = await _step_3_speaker_session(hass, device_id)
        if session is not None:
            return session.user_name, session.confidence, "step_3"

    name = await _step_5_fallback(hass)
    return name, FALLBACK_CONFIDENCE, "step_5"


async def _step_0_ha_context(hass: HomeAssistant, ha_user_id: str | None) -> str | None:
    """Step 0 — HA `context.user_id` non-empty AND != 'default' (per D2)."""
    if not ha_user_id or ha_user_id == DEFAULT_USER_NAME:
        return None
    try:
        user = await hass.auth.async_get_user(ha_user_id)
    except Exception:  # noqa: BLE001
        return None
    if user is None:
        return None
    return user.name or None


async def _step_1_device_area(hass: HomeAssistant, device_id: str) -> str | None:
    """Step 1 — device_id → device_registry → area → sole resident (D9)."""
    try:
        device = dr.async_get(hass).async_get(device_id)
    except Exception:  # noqa: BLE001
        return None
    if device is None or device.area_id is None:
        return None
    return await resolve_sole_resident_in_area(hass, device.area_id)


async def _step_2_presence(hass: HomeAssistant) -> str | None:
    """Step 2 — exactly one person home. Redis-first, hass.states fallback."""
    name, count = await is_exactly_one_home(hass)
    return name if count == 1 else None


async def _step_3_speaker_session(hass: HomeAssistant, device_id: str) -> SpeakerSession | None:
    """Step 3 — read jane:session:{device_id} and apply recency decay.

    Decay: stored_confidence × (0.95 ** minutes_since_last_update), floor 0.5.
    Redis TTL handles hard expiry at 15m.
    """
    redis = get_redis(hass)
    if redis is None:
        return None
    key = f"{REDIS_KEY_SPEAKER_SESSION_PREFIX}:{device_id}"
    try:
        raw = await redis.get(key)
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    session = SpeakerSession.from_json(raw)
    if session is None:
        return None
    minutes_elapsed = max(0.0, (time.time() - session.ts) / 60.0)
    decayed = session.confidence * (0.95**minutes_elapsed)
    session.confidence = max(decayed, SESSION_RECENCY_DECAY_FLOOR)
    return session


async def _step_5_fallback(hass: HomeAssistant) -> str:
    """Step 5 — fallback to `persons.metadata.is_primary = true` (D8)."""
    name = await get_primary_user(hass)
    return name or DEFAULT_USER_NAME


async def write_speaker_session(
    hass: HomeAssistant,
    device_id: str | None,
    user_name: str,
    conversation_id: str | None,
    confidence: float,
) -> None:
    """Persist the resolved speaker session for the next turn from this device.

    Only refreshes when confidence ≥ 0.7. Lower confidences shouldn't poison
    the session for the next turn.
    """
    if not device_id or confidence < SESSION_REFRESH_THRESHOLD:
        return
    redis = get_redis(hass)
    if redis is None:
        return
    session = SpeakerSession(
        user_name=user_name,
        conversation_id=conversation_id or "",
        ts=time.time(),
        confidence=confidence,
    )
    key = f"{REDIS_KEY_SPEAKER_SESSION_PREFIX}:{device_id}"
    try:
        await redis.set(key, session.to_json(), ex=SPEAKER_SESSION_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Speaker session write failed for %s", device_id, exc_info=True)
