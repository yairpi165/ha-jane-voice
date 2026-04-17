"""Context assembly — home awareness, routines, layout."""

import logging

from homeassistant.core import HomeAssistant

from ..const import DEFAULT_SKIP_KEYWORDS, DEFAULT_TRACKED_DOMAINS, normalize_person_state, parse_csv
from ..memory import get_backend

_LOGGER = logging.getLogger(__name__)

_FALLBACK_DOMAINS = parse_csv(DEFAULT_TRACKED_DOMAINS) - {"person"}
_FALLBACK_SKIP = parse_csv(DEFAULT_SKIP_KEYWORDS)
_OFF_STATES = {"off", "unavailable", "idle", "unknown", "standby"}


async def load_routines_index(hass: HomeAssistant) -> str:
    """Load routines — from PG RoutineStore if available, else memory_entries fallback."""
    from ..const import DOMAIN

    routine_store = getattr(hass.data.get(DOMAIN), "routines", None)
    if routine_store:
        try:
            return await routine_store.load_routines_for_context()
        except Exception:
            _LOGGER.debug("RoutineStore unavailable, falling back to memory_entries")

    try:
        return await get_backend().load("routines")
    except Exception:
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
    """Build context from live hass.states (fallback when Redis unavailable)."""
    from .working_memory import describe_entity

    parts = []

    weather = hass.states.get("weather.forecast_home")
    if weather:
        temp = weather.attributes.get("temperature", "?")
        parts.append(f"Weather: {weather.state}, {temp}°C")

    people_lines = []
    for state in hass.states.async_all("person"):
        name = state.attributes.get("friendly_name", "?")
        status = normalize_person_state(state.state)
        people_lines.append(f"{name}: {status}")
    if people_lines:
        parts.append("People: " + ", ".join(people_lines))

    active = []
    for state in hass.states.async_all():
        if state.domain not in _FALLBACK_DOMAINS:
            continue
        if state.state in _OFF_STATES:
            continue
        if any(kw in state.entity_id.lower() for kw in _FALLBACK_SKIP):
            continue
        active.append(describe_entity(state))
    if active:
        parts.append(f"Active: {', '.join(active[:15])}")

    return "\n".join(parts) if parts else ""
