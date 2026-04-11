"""Memory manager — init, load, save, append, tracking."""

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

# Firebase backup handle (set by __init__.py if configured)
_hass = None

# Memory stored in HA config directory
_memory_dir: Path | None = None

# Anti-repetition: track recent response openings (in-memory only)
_recent_responses: list[str] = []


def get_recent_responses() -> str:
    """Return recent response openings for anti-repetition injection."""
    if not _recent_responses:
        return ""
    return "Your recent response openings (don't repeat these): " + " | ".join(_recent_responses[-10:])


def track_response(response: str):
    """Track a response opening to avoid repetition."""
    if not response:
        return
    opening = response.strip()[:60]
    _recent_responses.append(opening)
    if len(_recent_responses) > 20:
        _recent_responses.pop(0)


def init_memory(config_dir: str, hass=None):
    """Initialize memory directory under HA config."""
    global _memory_dir, _hass
    _memory_dir = Path(config_dir) / "jane_memory"
    (_memory_dir / "users").mkdir(parents=True, exist_ok=True)
    _hass = hass


def get_memory_dir() -> Path | None:
    return _memory_dir


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


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
# Save
# ---------------------------------------------------------------------------

def _write(path: Path, content: str, firebase_doc: str | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)

    # Background Firebase backup (thread-safe)
    if firebase_doc and _hass:
        asyncio.run_coroutine_threadsafe(
            _firebase_backup(firebase_doc, content), _hass.loop
        )


async def _firebase_backup(doc_name: str, content: str):
    """Push memory to Firestore in background. Never blocks or raises."""
    try:
        from .firebase import backup_memory

        await backup_memory(doc_name, content)
    except Exception as e:
        _LOGGER.warning("Firebase backup failed for %s: %s", doc_name, e)


def save_user_memory(user_name: str, content: str):
    name = user_name.lower().strip()
    _write(get_memory_dir() / "users" / f"{name}.md", content, f"users_{name}")


def save_family_memory(content: str):
    _write(get_memory_dir() / "family.md", content, "family")


def save_habits_memory(content: str):
    _write(get_memory_dir() / "habits.md", content, "habits")


def save_corrections(content: str):
    _write(get_memory_dir() / "corrections.md", content, "corrections")


def save_routines(content: str):
    _write(get_memory_dir() / "routines.md", content, "routines")


# ---------------------------------------------------------------------------
# Action log
# ---------------------------------------------------------------------------

def append_action(user_name: str, description: str):
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
    """Append to permanent command history log (never pruned)."""
    path = get_memory_dir() / "history.log"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"[{now}] {user_name}: {user_text}\n[{now}] Jane: {response_text}\n\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(entry)
