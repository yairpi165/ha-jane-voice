"""Context assembly — home awareness, routines, layout."""

import logging
from pathlib import Path

from homeassistant.core import HomeAssistant

from ..memory import get_memory_dir

_LOGGER = logging.getLogger(__name__)


def load_routines_index() -> str:
    """Load routines memory for context injection — zero-cost cache hits."""
    mem_dir = get_memory_dir()
    if not mem_dir:
        return ""
    routines_path = Path(mem_dir) / "routines.md"
    if routines_path.exists():
        content = routines_path.read_text(encoding="utf-8").strip()
        if content:
            return content
    return ""


async def build_context(hass: HomeAssistant, working_memory=None) -> str:
    """Build concise home awareness context (~100-200 tokens).

    If working_memory is available, reads from Redis (richer, includes temporal data).
    Falls back to live hass.states queries if Redis is unavailable.
    """
    if working_memory is not None:
        try:
            context = await working_memory.get_context()
            if context:
                return context
        except Exception:
            _LOGGER.warning("Working memory unavailable, falling back to live query")

    return _build_context_live(hass)


def _build_context_live(hass: HomeAssistant) -> str:
    """Build context from live hass.states (original logic, used as fallback)."""
    parts = []

    weather = hass.states.get("weather.forecast_home")
    if weather:
        temp = weather.attributes.get("temperature", "?")
        parts.append(f"Weather: {weather.state}, {temp}°C")

    people_lines = []
    for state in hass.states.async_all("person"):
        name = state.attributes.get("friendly_name", "?")
        status = "home" if state.state == "home" else "away"
        people_lines.append(f"{name}: {status}")
    if people_lines:
        parts.append("People: " + ", ".join(people_lines))

    skip_keywords = {"camera", "motion", "microphone", "speaker", "rtsp", "recording", "detection"}
    active = []
    for state in hass.states.async_all():
        if state.domain in ("light", "climate", "media_player", "fan") and state.state not in (
            "off",
            "unavailable",
            "idle",
            "unknown",
            "standby",
        ):
            eid = state.entity_id.lower()
            if any(kw in eid for kw in skip_keywords):
                continue
            active.append(state.attributes.get("friendly_name", state.entity_id))
    if active:
        parts.append(f"Active: {', '.join(active[:10])}")

    return "\n".join(parts) if parts else ""
