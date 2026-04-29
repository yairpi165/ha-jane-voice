"""Proactive triggers + decision logging — S3.2 (JANE-45).

This module is the brain-side surface for the [PROACTIVE] flow:

1. ``_parse_proactive_payload`` — turns an HA-fired ``[PROACTIVE] {desc}.
   Time: HH:MM. Mode: {mode}.`` string into a structured payload with
   explicit fallback rules (D2). Returns None for malformed payloads
   that can't be acted on (no description AND no Time).

2. ``route_alert(action_type, urgency, mode)`` — pure routing decision
   per D6 / D8. Returns ``"voice" | "notification" | "silent"``.

3. Trust-budget primitives (D4, D10, D13): ``check_speech_budget`` /
   ``increment_speech_budget`` against a Redis daily counter, plus
   ``check_dismissal_streak`` against PG ``user_overrides``. All three
   are failure-soft — any backend error returns ALLOW so a buggy
   storage path can't silently silence Jane.

The actual ``conversation.py`` integration (``[PROACTIVE]`` detection +
mode gate + handler dispatch) lives in ``conversation.py``; this module
is the I/O-light substrate it leans on.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import time as time_t
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from ..memory.household_mode import get_active_mode
from ..modes import MODE_RULES

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parse helper (D2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProactivePayload:
    """Structured form of a [PROACTIVE] message after fallback resolution."""

    description: str
    time_str: str  # "HH:MM" — always populated
    mode: str  # always populated; from message OR get_active_mode
    person: str | None  # heuristic extraction from description


_PROACTIVE_PREFIX = "[PROACTIVE]"
_TIME_RE = re.compile(r"Time:\s*(\d{1,2}:\d{2})", re.IGNORECASE)
_MODE_RE = re.compile(r"Mode:\s*([^.\n]+?)(?:[.\n]|$)", re.IGNORECASE)
# Heuristic person extraction: "{Name} arrived" or "{Name} left" — the HA
# automation owns the person attribution; we just lift it from the text so
# downstream callers (KPI queries, dismissal correlation) can group by it.
_PERSON_RE = re.compile(
    r"^\s*(?:‏)?(\S+)\s+(?:arrived|left|came home|got home|הגיע|הגיעה|יצא|יצאה)", re.IGNORECASE | re.UNICODE
)


def is_proactive_message(text: str) -> bool:
    """Return True iff `text` (after lstrip) starts with the [PROACTIVE] tag.

    Trivial helper exposed for callers that need to gate behavior without
    invoking the full parser.
    """
    return text is not None and text.lstrip().startswith(_PROACTIVE_PREFIX)


def _parse_proactive_payload(text: str, hass: HomeAssistant) -> ProactivePayload | None:
    """Parse a `[PROACTIVE] ...` string with explicit fallbacks (D2).

    Fallback rules:
    - Missing ``Time`` → use ``dt_util.now(hass.config.time_zone)``.
    - Bogus ``Time`` (``25:99``) → use the hass-local now.
    - Missing ``Mode`` → ``get_active_mode(hass)``.
    - Missing description AND missing Time → return None (drop + audit).
    - Missing description but Time present → allow with empty description
      (Jane reasons from time + mode).
    """
    if not is_proactive_message(text):
        return None
    body = text.lstrip()[len(_PROACTIVE_PREFIX) :].strip()

    # Time extraction with bogus-value fallback.
    raw_time = _TIME_RE.search(body)
    time_str: str | None = None
    if raw_time:
        candidate = raw_time.group(1)
        try:
            hh, mm = map(int, candidate.split(":"))
            time_t(hour=hh, minute=mm)  # validates 0-23/0-59
            time_str = f"{hh:02d}:{mm:02d}"
        except (ValueError, TypeError):
            _LOGGER.debug("Bogus Time in [PROACTIVE]: %r — falling back to now()", candidate)
    if time_str is None:
        time_str = dt_util.now().strftime("%H:%M")

    # Mode extraction with active-mode fallback.
    raw_mode = _MODE_RE.search(body)
    mode = (raw_mode.group(1).strip() if raw_mode else "").strip()
    if not mode or mode not in MODE_RULES:
        try:
            mode = get_active_mode(hass)
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("get_active_mode failed during parse — defaulting to NORMAL: %s", e)
            from ..modes import MODE_NORMAL

            mode = MODE_NORMAL

    # Description = body up to first known field marker. Strip trailing
    # punctuation. If the message was literally "[PROACTIVE]" with no body
    # AND no Time was extracted, drop.
    desc_end = len(body)
    for marker_re in (_TIME_RE, _MODE_RE):
        m = marker_re.search(body)
        if m:
            desc_end = min(desc_end, m.start())
    description = body[:desc_end].strip().rstrip(".").strip()

    if not description and not raw_time:
        # Nothing actionable — caller will write a "dropped_malformed_payload"
        # audit row and return early.
        return None

    person_m = _PERSON_RE.search(description) if description else None
    person = person_m.group(1) if person_m else None

    return ProactivePayload(
        description=description,
        time_str=time_str,
        mode=mode,
        person=person,
    )


# ---------------------------------------------------------------------------
# route_alert (D6, D8)
# ---------------------------------------------------------------------------


def route_alert(action_type: str, urgency: str, mode: str) -> str:
    """Decide the surface for a proactive action: voice / notification / silent.

    Logic (S3.2 — observation-class actions only):
        urgency == 'critical' → 'voice' (safety always speaks; bypasses mode)
        MODE_RULES[mode]['tts'] is False → 'notification'  (silent in-house)
        else → 'voice'

    LIMITATION (D6): this signature is correct for S3.2's three Tier-1
    triggers (arrival / all-away / goodnight), all of which are
    *observation-class* actions. Master Arch §4.3's Action Taxonomy
    requires a 4th ``risk`` parameter for irreversible actions (unlock
    door, disable alarm) — those need explicit voice confirmation
    regardless of mode/urgency. S3.3+ tickets that introduce such
    actions MUST extend this signature; a SYSTEM_PROMPT rule warns
    against silent expansion.

    ``action_type`` is currently logged but unused for routing — it's
    reserved for per-type policy in S3.3 (e.g. security alerts override
    comfort even at non-critical urgency).
    """
    if urgency == "critical":
        return "voice"
    rules = MODE_RULES.get(mode, {})
    if rules.get("tts", True) is False:
        return "notification"
    return "voice"


# ---------------------------------------------------------------------------
# Trust-budget primitives (D4, D10, D13)
# ---------------------------------------------------------------------------


_SPEECH_DAILY_CAP = 2
_BUDGET_KEY_PREFIX = "jane:proactive:speech_count:"
# 26h TTL — small buffer past local-day so a cross-DST or clock-skew event
# can't double-charge the previous day's budget against the new day.
_BUDGET_TTL_SECONDS = 26 * 3600


def _local_day_key(hass: HomeAssistant) -> str:
    """Return the Redis key for today's speech budget in household-local TZ.

    D13 — uses the household's configured time zone via
    ``hass.config.time_zone``; falls back to UTC if unset (defensive).
    "Max 2 per day" is a household-trust contract, not a system contract,
    so local-day is what the user means.
    """
    try:
        tz_name = getattr(hass.config, "time_zone", None)
        local_now = dt_util.now(dt_util.get_time_zone(tz_name)) if tz_name else dt_util.utcnow()
    except Exception as e:  # noqa: BLE001
        _LOGGER.debug("Could not resolve household TZ — using UTC: %s", e)
        local_now = dt_util.utcnow()
    return f"{_BUDGET_KEY_PREFIX}{local_now.date().isoformat()}"


async def check_speech_budget(hass: HomeAssistant, redis) -> bool:
    """True iff today's local-day proactive speech count < 2.

    Failure-soft: any Redis error → True (allow). The trust-budget exists
    to be a polite cap, not a hard safety mechanism — if Redis is down,
    the cost of letting Jane speak is low; the cost of silencing every
    proactive alert is high.
    """
    if redis is None:
        return True
    try:
        key = _local_day_key(hass)
        raw = await redis.get(key)
        if raw is None:
            return True
        return int(raw) < _SPEECH_DAILY_CAP
    except Exception as e:  # noqa: BLE001
        _LOGGER.debug("Speech-budget check errored — allowing: %s", e)
        return True


async def increment_speech_budget(hass: HomeAssistant, redis) -> None:
    """Atomically INCR + EXPIRE the daily counter. Failure-soft."""
    if redis is None:
        return
    try:
        key = _local_day_key(hass)
        new_value = await redis.incr(key)
        if new_value == 1:
            # Set TTL only on first set-of-day so subsequent increments
            # don't reset the window mid-day.
            await redis.expire(key, _BUDGET_TTL_SECONDS)
    except Exception as e:  # noqa: BLE001
        _LOGGER.debug("Speech-budget increment errored: %s", e)


_DISMISSAL_STREAK_LEN = 3
_DISMISSAL_WINDOW_DAYS = 7


async def check_dismissal_streak(pg_pool, action_type: str, *, days: int = _DISMISSAL_WINDOW_DAYS) -> bool:
    """True iff this action_type is NOT under a 3-strike suppression.

    Returns False (suppress) only when the most recent 3 ``user_overrides``
    rows for ``action_type`` (within ``days``) are ALL ``override_type='dismissed'``.
    Mixed dismissals + reverses + corrects don't count as a streak — the
    user still cared enough to actively override, just differently.

    Failure-soft: any PG error → True (allow). Same reasoning as the
    speech-budget check.
    """
    if pg_pool is None:
        return True
    try:
        async with pg_pool.acquire() as conn:
            # asyncpg binds Python `timedelta` to PG `interval` natively. The
            # earlier ($2 || ' days')::interval / '7 days'::interval forms
            # both tripped asyncpg's type validation and silently fell into
            # the failure-soft branch — caught only via DEBUG logs on the
            # dev VM. Use timedelta to keep the bind type-safe.
            rows = await conn.fetch(
                """SELECT override_type FROM user_overrides
                   WHERE action_type = $1
                     AND ts > NOW() - $2::interval
                   ORDER BY ts DESC
                   LIMIT $3""",
                action_type,
                timedelta(days=days),
                _DISMISSAL_STREAK_LEN,
            )
    except Exception as e:  # noqa: BLE001
        _LOGGER.debug("Dismissal-streak check errored — allowing: %s", e)
        return True
    if len(rows) < _DISMISSAL_STREAK_LEN:
        return True
    return not all(r["override_type"] == "dismissed" for r in rows)
