"""Memory context builder — formats persons + preferences for Gemini system_instruction."""

import logging

from homeassistant.core import HomeAssistant

from ..const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Max lines in the memory context block
_MAX_LINES = 20
# Minimum confidence to include a preference
_MIN_CONFIDENCE = 0.5


async def build_memory_context(hass: HomeAssistant, user_name: str) -> str:
    """Build a concise memory context string from structured PG data.

    Returns a formatted string for injection into Gemini system_instruction.
    Falls back to load_all_memory() markdown if structured store unavailable.
    """
    try:
        store = hass.data.get(DOMAIN, {}).get("_structured")
    except (AttributeError, TypeError):
        store = None
    if store is None:
        return await _fallback_markdown(hass, user_name)

    try:
        persons = await store.load_persons()
        all_prefs = await store.load_all_preferences(min_confidence=_MIN_CONFIDENCE)
    except Exception as e:
        _LOGGER.warning("Structured memory unavailable, falling back to markdown: %s", e)
        return await _fallback_markdown(hass, user_name)

    if not persons and not all_prefs:
        return await _fallback_markdown(hass, user_name)

    lines: list[str] = []

    # Family section
    if persons:
        lines.append("## Family")
        for p in persons:
            parts = [p["name"]]
            if p.get("role"):
                parts.append(f"({p['role']})")
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

    # Per-person preferences (_MAX_LINES is a soft cap — may exceed by 1 due to spacer lines)
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

    # Family-level preferences
    family_prefs = all_prefs.get("_family", [])
    if family_prefs and len(lines) < _MAX_LINES:
        lines.append("## Household Rules")
        for pref in family_prefs:
            lines.append(f"- {pref['key'].replace('_', ' ').title()}: {pref['value']}")
            if len(lines) >= _MAX_LINES:
                break

    result = "\n".join(lines).strip()
    if result:
        _LOGGER.debug("Memory context: %d lines, %d chars", len(lines), len(result))
    return result


async def _fallback_markdown(hass: HomeAssistant, user_name: str) -> str:
    """Fall back to loading markdown memory when structured store is unavailable."""
    try:
        from .manager import load_all_memory

        return await hass.async_add_executor_job(load_all_memory, user_name)
    except Exception:
        return ""
