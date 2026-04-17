"""Memory manager — PG-only storage via StorageBackend."""

import logging

from .storage import StorageBackend

_LOGGER = logging.getLogger(__name__)

# Active storage backend (set by init_memory)
_backend: StorageBackend | None = None

# Hass reference (for firebase backup scheduling)
_hass = None

# In-memory anti-repetition list (used by sync engine code in executor)
_recent_responses: list[str] = []


def get_backend() -> StorageBackend:
    """Get the active storage backend."""
    if _backend is None:
        raise RuntimeError("Memory not initialized. Call init_memory() first.")
    return _backend


def init_memory(backend: StorageBackend, hass=None):
    """Initialize memory system with a PG backend."""
    global _backend, _hass
    _backend = backend
    _hass = hass


# ---------------------------------------------------------------------------
# Async functions (called from conversation.py on event loop)
# ---------------------------------------------------------------------------


async def async_append_action(user_name: str, description: str):
    """Append action via storage backend (async)."""
    await get_backend().append_event("action", user_name, description)


async def async_append_history(user_name: str, user_text: str, response_text: str):
    """Append conversation history via storage backend (async)."""
    await get_backend().append_event(
        "conversation",
        user_name,
        f"{user_name}: {user_text}",
        metadata={"user_text": user_text, "response_text": response_text},
    )


async def async_track_response(opening: str):
    """Track response opening via storage backend (async)."""
    await get_backend().track_response(opening)
    # Also update local list for sync access
    if opening:
        _recent_responses.append(opening.strip()[:60])
        if len(_recent_responses) > 20:
            _recent_responses.pop(0)


async def async_get_recent_responses() -> str:
    """Get recent responses via storage backend (async)."""
    responses = await get_backend().get_recent_responses(10)
    if not responses:
        return ""
    openings = " | ".join(responses)
    return (
        "ANTI-REPETITION: You already used these openings recently — "
        "DO NOT start with the same words or sentence structure. "
        "Vary your greeting, tone, and sentence pattern completely:\n" + openings
    )


# ---------------------------------------------------------------------------
# Sync compatibility (for brain/engine.py which runs in executor)
# ---------------------------------------------------------------------------


def get_recent_responses() -> str:
    """Sync version — uses local in-memory list."""
    if not _recent_responses:
        return ""
    openings = " | ".join(_recent_responses[-10:])
    return (
        "ANTI-REPETITION: You already used these openings recently — "
        "DO NOT start with the same words or sentence structure. "
        "Vary your greeting, tone, and sentence pattern completely:\n" + openings
    )


def track_response(response: str):
    """Sync version — updates local in-memory list."""
    if not response:
        return
    opening = response.strip()[:60]
    _recent_responses.append(opening)
    if len(_recent_responses) > 20:
        _recent_responses.pop(0)
