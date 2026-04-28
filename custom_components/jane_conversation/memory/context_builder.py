"""Memory context builder — formats persons + preferences for Gemini system_instruction.

S3.0 (JANE-71) made this confidence-aware. The per-field tier table:
  < 0.5  household-min — persons summary only.
  0.5–0.7 family-tier — persons + household-level preferences (`_family`)
                       + shared routines. NO per-person prefs, NO episodic.
  ≥ 0.7  personal/full — full behavior (current).

This closes JANE-62's Half B: shared-device speaker resolves at low confidence
(Steps 2/3) but `build_memory_context` no longer returns empty — it returns
household-level facts that are safe to expose without identity.
"""

import logging

from homeassistant.core import HomeAssistant

from ..const import DOMAIN
from .manager import get_backend

_LOGGER = logging.getLogger(__name__)

# Max lines in the memory context block
_MAX_LINES = 40
# Minimum confidence to include a preference
_MIN_CONFIDENCE = 0.5

# S3.0 confidence tier thresholds (mirror policy.py gates).
TIER_PERSONAL = 0.7  # ≥ this → personal/full context
TIER_FAMILY = 0.5  # ≥ this and < TIER_PERSONAL → family-tier context
# < TIER_FAMILY → household-min (persons summary only)


async def _fallback_pg(user_name: str) -> str:
    """Fall back to loading memory from PG memory_entries table."""
    try:
        return await get_backend().load_all(user_name)
    except Exception:
        return ""


async def build_memory_context(hass: HomeAssistant, user_name: str, confidence: float = 1.0) -> str:
    """Build a concise memory context string from structured PG data.

    `confidence` is the speaker-resolution confidence (S3.0). At < TIER_FAMILY
    only the persons summary is returned (household-min); at TIER_FAMILY ≤ c <
    TIER_PERSONAL only persons + household-level preferences (`_family`) are
    returned (no per-person prefs, no `memory_entries` fallback since those
    are personal-keyed). Default 1.0 preserves pre-S3.0 behavior for callers
    that haven't been threaded through `resolve_speaker` yet.

    Returns a formatted string for injection into Gemini system_instruction.
    Falls back to memory_entries table if structured store unavailable AND
    confidence ≥ TIER_PERSONAL.
    """
    try:
        store = getattr(hass.data.get(DOMAIN), "structured", None)
    except (AttributeError, TypeError):
        store = None
    if store is None:
        return await _fallback_pg(user_name) if confidence >= TIER_PERSONAL else ""

    try:
        persons = await store.load_persons()
        all_prefs = await store.load_all_preferences(min_confidence=_MIN_CONFIDENCE)
    except Exception as e:
        _LOGGER.warning("Structured memory unavailable, falling back to PG: %s", e)
        return await _fallback_pg(user_name) if confidence >= TIER_PERSONAL else ""

    if not persons and not all_prefs:
        return await _fallback_pg(user_name) if confidence >= TIER_PERSONAL else ""

    lines: list[str] = []

    # Persons summary — included at every tier (household-level fact).
    if persons:
        lines.append("## Family")
        for p in persons:
            parts = [p["name"]]
            if p.get("role"):
                parts.append(f"({p['role']})")
            # birth_date and metadata are personal — only at TIER_PERSONAL.
            if confidence >= TIER_PERSONAL:
                if p.get("birth_date"):
                    bd = p["birth_date"]
                    bd_str = bd.strftime("%d/%m/%Y") if hasattr(bd, "strftime") else str(bd)
                    parts.append(f"born {bd_str}")
                meta = p.get("metadata") or {}
                if isinstance(meta, str):
                    import json

                    try:
                        meta = json.loads(meta)
                    except (json.JSONDecodeError, TypeError):
                        meta = {}
                if meta and isinstance(meta, dict):
                    details = ", ".join(f"{k}: {v}" for k, v in meta.items() if v)
                    if details:
                        parts.append(f"— {details}")
            lines.append(f"- {' '.join(parts)}")
        lines.append("")

    # Household-min stops here.
    if confidence < TIER_FAMILY:
        return "\n".join(lines).strip()

    # Per-person preferences — only at TIER_PERSONAL (≥ 0.7).
    if confidence >= TIER_PERSONAL:
        for person_name, prefs in all_prefs.items():
            if person_name == "_family":
                continue  # handled separately
            lines.append(f"## {person_name}'s Preferences")
            for pref in prefs:
                line = f"- {pref['key'].replace('_', ' ').title()}: {pref['value']}"
                if pref.get("inferred"):
                    line += f" [inferred, {pref['confidence']:.1f}]"
                lines.append(line)
                if len(lines) >= _MAX_LINES:
                    break
            lines.append("")
            if len(lines) >= _MAX_LINES:
                break

    # Family-level preferences — included at family tier and above.
    family_prefs = all_prefs.get("_family", [])
    if family_prefs and len(lines) < _MAX_LINES:
        lines.append("## Household Rules")
        for pref in family_prefs:
            lines.append(f"- {pref['key'].replace('_', ' ').title()}: {pref['value']}")
            if len(lines) >= _MAX_LINES:
                break

    result = "\n".join(lines).strip()

    # If structured context is thin AND we're at personal tier, supplement
    # with memory_entries (which carry personal narratives keyed by name).
    if len(lines) < 3 and confidence >= TIER_PERSONAL:
        pg_content = await _fallback_pg(user_name)
        if pg_content:
            result = (result + "\n\n" + pg_content).strip() if result else pg_content

    if result:
        _LOGGER.debug("Memory context: %d lines, %d chars, confidence=%.2f", len(lines), len(result), confidence)
    return result


_MAX_EPISODIC_CHARS = 800  # ~200 tokens


async def build_episodic_context(hass: HomeAssistant, hours: int = 12, confidence: float = 1.0) -> str:
    """Build a concise episodic context from recent episodes + yesterday's summary.

    Episodic narratives are personal — only returned at confidence ≥ TIER_PERSONAL
    (S3.0 D11 — episodic context reads are in PERSONAL_DATA_ACTIONS).

    Returns a formatted string for injection into Gemini system_instruction.
    Hard limit: _MAX_EPISODIC_CHARS (~200 tokens). Priority: daily summary first.
    """
    if confidence < TIER_PERSONAL:
        return ""

    try:
        episodic = getattr(hass.data.get(DOMAIN), "episodic", None)
    except (AttributeError, TypeError):
        episodic = None
    if episodic is None:
        return ""

    try:
        from datetime import date, datetime, timedelta

        now = datetime.now().astimezone()
        start = now - timedelta(hours=hours)

        episodes = await episodic.query_episodes(start, now, limit=10)
        yesterday = date.today() - timedelta(days=1)
        daily = await episodic.get_daily_summary(yesterday)
    except Exception as e:
        _LOGGER.debug("Episodic context unavailable: %s", e)
        return ""

    if not episodes and not daily:
        return ""

    parts: list[str] = []
    chars = 0

    # Yesterday's summary first (most compact, most useful)
    if daily:
        summary = daily.get("summary", "") if isinstance(daily, dict) else str(daily)
        if summary:
            line = f"אתמול: {summary}"
            parts.append(line)
            chars += len(line)

    # Recent episodes (most recent first)
    for ep in episodes:
        ts = ep["start_ts"]
        time_str = ts.strftime("%H:%M") if hasattr(ts, "strftime") else str(ts)[:5]
        line = f"{time_str} — {ep['title']}"
        if chars + len(line) > _MAX_EPISODIC_CHARS:
            break
        parts.append(line)
        chars += len(line)

    return "\n".join(parts)
