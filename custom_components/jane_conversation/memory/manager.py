"""Memory manager — load, save, append via StorageBackend."""

import logging
from pathlib import Path

from .storage import FileBackend, StorageBackend

_LOGGER = logging.getLogger(__name__)

# Active storage backend (set by init_memory)
_backend: StorageBackend | None = None

# Memory directory (kept for extraction.py and legacy access)
_memory_dir: Path | None = None

# For test compatibility
_recent_responses: list[str] = []


def get_backend() -> StorageBackend:
    """Get the active storage backend."""
    if _backend is None:
        raise RuntimeError("Memory not initialized. Call init_memory() first.")
    return _backend


def init_memory(config_dir: str, hass=None, backend: StorageBackend | None = None):
    """Initialize memory system."""
    global _memory_dir, _backend
    _memory_dir = Path(config_dir) / "jane_memory"
    (_memory_dir / "users").mkdir(parents=True, exist_ok=True)

    if backend is not None:
        _backend = backend
    else:
        _backend = FileBackend(_memory_dir, hass)


def get_memory_dir() -> Path | None:
    return _memory_dir


# ---------------------------------------------------------------------------
# Sync wrappers (called from executor by brain/conversation)
# These call the async backend via the backend's sync-compatible methods
# For FileBackend, the async methods are actually sync under the hood
# ---------------------------------------------------------------------------

def _read(path: Path) -> str:
    """Legacy sync read — used by extraction.py for home map."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def _write(path: Path, content: str, firebase_doc: str | None = None):
    """Legacy sync write — used by extraction.py for home map."""
    import asyncio

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)

    if firebase_doc and _backend and isinstance(_backend, FileBackend) and _backend._hass:
        asyncio.run_coroutine_threadsafe(
            _backend._firebase_backup(firebase_doc, content), _backend._hass.loop
        )


# ---------------------------------------------------------------------------
# Sync load functions (called from executor threads)
# ---------------------------------------------------------------------------

def load_user_memory(user_name: str) -> str:
    return _read(get_memory_dir() / "users" / f"{user_name.lower().strip()}.md")


def load_family_memory() -> str:
    return _read(get_memory_dir() / "family.md")


def load_habits_memory() -> str:
    return _read(get_memory_dir() / "habits.md")


def load_actions() -> str:
    return _read(get_memory_dir() / "actions.md")


def load_home() -> str:
    return _read(get_memory_dir() / "home.md")


def load_corrections() -> str:
    return _read(get_memory_dir() / "corrections.md")


def load_routines() -> str:
    return _read(get_memory_dir() / "routines.md")


def load_all_memory(user_name: str) -> str:
    sections = {
        "Personal Memory": load_user_memory(user_name),
        "Family Memory": load_family_memory(),
        "Behavioral Patterns": load_habits_memory(),
        "Recent Actions (24h)": load_actions(),
        "Home Layout": load_home(),
        "Corrections & Learnings": load_corrections(),
        "Routines": load_routines(),
    }
    parts = []
    for title, content in sections.items():
        parts.append(f"## {title}")
        parts.append(content if content else "No data yet.")
        parts.append("")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Sync save functions (called from extraction.py in executor)
# ---------------------------------------------------------------------------

def _schedule_on_pg(coro_factory, label: str = ""):
    """Schedule a PG coroutine from executor thread. Pass a zero-arg callable, not a coroutine."""
    import asyncio

    pg = getattr(_backend, "_pg", None) if _backend else None
    hass = getattr(getattr(_backend, "_file", None), "_hass", None) if _backend else None
    if not pg or not hass:
        return

    async def _safe():
        try:
            await coro_factory()
            _LOGGER.debug("PG OK: %s", label)
        except Exception as e:
            _LOGGER.warning("PG failed (%s): %s", label, e)

    try:
        asyncio.run_coroutine_threadsafe(_safe(), hass.loop)
    except Exception as e:
        _LOGGER.debug("PG scheduling failed (%s): %s", label, e)


def _schedule_backend_save(category: str, content: str, user_name: str | None = None):
    """Schedule PG save from executor thread. File already written by caller."""
    pg = getattr(_backend, "_pg", None) if _backend else None
    if pg:
        _schedule_on_pg(lambda: pg.save(category, content, user_name), f"save {category}/{user_name}")


def save_user_memory(user_name: str, content: str):
    name = user_name.lower().strip()
    _write(get_memory_dir() / "users" / f"{name}.md", content, f"users_{name}")
    _schedule_backend_save("user", content, name)


def save_family_memory(content: str):
    _write(get_memory_dir() / "family.md", content, "family")
    _schedule_backend_save("family", content)


def save_habits_memory(content: str):
    _write(get_memory_dir() / "habits.md", content, "habits")
    _schedule_backend_save("habits", content)


def save_corrections(content: str):
    _write(get_memory_dir() / "corrections.md", content, "corrections")
    _schedule_backend_save("corrections", content)


def save_routines(content: str):
    _write(get_memory_dir() / "routines.md", content, "routines")
    _schedule_backend_save("routines", content)


# ---------------------------------------------------------------------------
# Async functions (called from conversation.py on event loop)
# ---------------------------------------------------------------------------

async def async_append_action(user_name: str, description: str):
    """Append action via storage backend (async)."""
    await get_backend().append_event("action", user_name, description)


async def async_append_history(user_name: str, user_text: str, response_text: str):
    """Append conversation history via storage backend (async)."""
    await get_backend().append_event(
        "conversation", user_name, f"{user_name}: {user_text}",
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
    return "Your recent response openings (don't repeat these): " + " | ".join(responses)


# ---------------------------------------------------------------------------
# Sync compatibility (for brain/engine.py which runs in executor)
# ---------------------------------------------------------------------------

def get_recent_responses() -> str:
    """Sync version — uses local in-memory list."""
    if not _recent_responses:
        return ""
    return "Your recent response openings (don't repeat these): " + " | ".join(_recent_responses[-10:])


def track_response(response: str):
    """Sync version — updates local in-memory list."""
    if not response:
        return
    opening = response.strip()[:60]
    _recent_responses.append(opening)
    if len(_recent_responses) > 20:
        _recent_responses.pop(0)


def schedule_pg_append(event_type: str, user_name: str, description: str, metadata: dict | None = None):
    """Schedule PG append_event from executor thread."""
    pg = getattr(_backend, "_pg", None) if _backend else None
    if pg:
        _schedule_on_pg(
            lambda: pg.append_event(event_type, user_name, description, metadata),
            f"append {event_type}/{user_name}",
        )


# ---------------------------------------------------------------------------
# Legacy sync append (kept for backward compat, delegates to file directly)
# ---------------------------------------------------------------------------

def append_action(user_name: str, description: str):
    """Sync append action — legacy, used by conversation.py in executor."""
    from datetime import datetime, timedelta

    path = get_memory_dir() / "actions.md"
    now = datetime.now()
    new_line = f"- {now.strftime('%Y-%m-%d %H:%M')} — {description} ({user_name})"

    lines = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("- "):
                try:
                    ts_str = line.split(" — ")[0].replace("- ", "")
                    ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M")
                    if now - ts < timedelta(hours=24):
                        lines.append(line)
                except (ValueError, IndexError):
                    lines.append(line)
            elif line.startswith("#"):
                continue

    lines.append(new_line)
    content = "# Recent Actions (rolling 24h)\n\n" + "\n".join(lines) + "\n"
    _write(path, content)


def append_history(user_name: str, user_text: str, response_text: str):
    """Sync append history — legacy."""
    from datetime import datetime

    path = get_memory_dir() / "history.log"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"[{now}] {user_name}: {user_text}\n[{now}] Jane: {response_text}\n\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(entry)
