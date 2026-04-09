"""Jane Memory System — LLM-managed markdown memory."""

import asyncio
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta

from anthropic import Anthropic

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


def get_memory_dir() -> Path:
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


# ---------------------------------------------------------------------------
# Home map (GPT-generated on first run)
# ---------------------------------------------------------------------------

HOME_SETUP_PROMPT = """You are setting up the memory for Jane, a smart home assistant.
Below is a raw list of smart home devices from Home Assistant.

Devices:
{entity_list}

Write a concise home layout document in English, organized by ROOM (not by device type).
- Group devices by their likely room based on their name
- Include the entity_id in parentheses for each device
- Skip internal/config entities (timers, notifications, camera settings, robot vacuum sub-settings, child locks, dishwasher settings)
- Only include devices a user would actually ask to control: lights, AC, heater, fan, shutters, TV, water heater, robot vacuum (main entity only)
- Keep it concise — one line per device, max 50 lines total"""


def rebuild_home_map(client: Anthropic, hass):
    """Generate home.md by asking Claude to organize HA entities by room."""
    home_path = get_memory_dir() / "home.md"
    if home_path.exists() and _read(home_path):
        return

    relevant_domains = {"light", "climate", "cover", "media_player", "fan", "vacuum", "water_heater"}
    skip_keywords = {"camera", "motion_detection", "microphone", "speaker", "audio_recording",
                     "pet_detection", "rtsp", "extra_dry", "child_lock", "notification",
                     "backup_map", "wetness_level", "suction_level", "mop_pad", "cleaning_mode",
                     "cleaning_times", "cleaning_route", "floor_material", "visibility"}
    entities = []
    for state in hass.states.async_all():
        if state.domain in relevant_domains:
            eid = state.entity_id.lower()
            if any(kw in eid for kw in skip_keywords):
                continue
            name = state.attributes.get("friendly_name", state.entity_id)
            entities.append(f"- {name} ({state.entity_id}) [domain: {state.domain}, state: {state.state}]")

    if not entities:
        return

    prompt = HOME_SETUP_PROMPT.replace("{entity_list}", "\n".join(entities))

    try:
        from .const import CLAUDE_MODEL_FAST
        response = client.messages.create(
            model=CLAUDE_MODEL_FAST,
            system=prompt,
            messages=[{"role": "user", "content": "Generate the home layout now."}],
            max_tokens=1500,
            temperature=0.3,
        )
        content = response.content[0].text.strip()
        if not content.startswith("#"):
            content = "# Home Layout\n\n" + content
        _write(home_path, content)
        _LOGGER.info("Home map created by Claude")
    except Exception as e:
        _LOGGER.error("Home map generation failed: %s", e)


# ---------------------------------------------------------------------------
# Memory extraction (GPT-managed, runs in background)
# ---------------------------------------------------------------------------

MEMORY_EXTRACTION_PROMPT = """You are the memory manager for Jane, a Hebrew smart home assistant.
Analyze the conversation and decide what to remember.

Current memory:
{memory_context}

---

Latest exchange:
User ({user_name}): {user_text}
Jane: {jane_response}

---

Rules:
1. If a memory file needs updating — rewrite its ENTIRE content, merging new info with existing.
2. New information wins over old when they conflict.
3. Keep each file concise (max ~50 lines).
4. Write ALL memory in English, even though conversations are in Hebrew.
5. If nothing worth remembering — return null.

BE AGGRESSIVE about saving these:
- Family members: names, ages, relationships, preferences, hobbies — ALWAYS save
- Personal details: what they like/dislike, their routine, their job, their personality
- Corrections: if the user corrected Jane about anything, save the learning
- Patterns: recurring requests, time-based habits
- Routines: multi-step sequences ("goodnight" means lights off + shutters down + AC 24)

DO NOT save:
- One-time commands: "turn on the light" → skip
- General questions: "what time is it?" → skip
- Pleasantries with no new info: "thank you" → skip

Respond in JSON only:
{
  "user": "Full updated user memory, or null",
  "family": "Full updated family memory, or null",
  "habits": "Full updated habits, or null",
  "corrections": "Full updated corrections, or null",
  "routines": "Full updated routines, or null"
}"""


def process_memory(client: Anthropic, user_name: str, user_text: str, jane_response: str, action: str):
    """Analyze conversation and update memory if needed."""
    if action == "ha_service" and len(jane_response) < 30:
        return

    memory_context = load_all_memory(user_name)

    prompt = (
        MEMORY_EXTRACTION_PROMPT
        .replace("{memory_context}", memory_context)
        .replace("{user_name}", user_name)
        .replace("{user_text}", user_text)
        .replace("{jane_response}", jane_response)
    )

    try:
        from .const import CLAUDE_MODEL_FAST
        response = client.messages.create(
            model=CLAUDE_MODEL_FAST,
            system=prompt,
            messages=[{"role": "user", "content": "Analyze and respond with JSON."}],
            max_tokens=2000,
            temperature=0.3,
        )

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        if raw.endswith("```"):
            raw = raw[:-3]

        result = json.loads(raw.strip())

        if result.get("user"):
            save_user_memory(user_name, result["user"])
        if result.get("family"):
            save_family_memory(result["family"])
        if result.get("habits"):
            save_habits_memory(result["habits"])
        if result.get("corrections"):
            save_corrections(result["corrections"])
        if result.get("routines"):
            save_routines(result["routines"])

        _LOGGER.info("Memory updated for %s", user_name)

    except Exception as e:
        _LOGGER.warning("Memory extraction failed: %s", e)
