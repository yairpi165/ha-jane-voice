"""Memory extraction — LLM-based memory processing and home map generation."""

import json
import logging

from google import genai
from google.genai import types

from ..const import GEMINI_MODEL_FAST, PREFERENCE_KEY_TAXONOMY
from .manager import (
    _read,
    _write,
    get_memory_dir,
    load_all_memory,
    save_family_memory,
    save_habits_memory,
    save_routines,
    save_user_memory,
    schedule_pg_append,
)

_LOGGER = logging.getLogger(__name__)


def _ensure_str(value) -> str:
    """Coerce dict/list to str — Gemini sometimes returns JSON objects for categories."""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _repair_json(raw: str) -> dict:
    """Repair truncated JSON from Gemini extraction. Raises JSONDecodeError if unfixable."""
    repaired = raw
    # Close unclosed string (count unescaped quotes only)
    escaped_count = raw.count('\\"')
    real_quotes = raw.count('"') - escaped_count
    if real_quotes % 2 != 0:
        repaired += '"'
    if repaired.rstrip().endswith(","):
        repaired = repaired.rstrip()[:-1]
    # Close brackets then braces (order matters for nested structures)
    repaired += "]" * max(repaired.count("[") - repaired.count("]"), 0)
    repaired += "}" * max(repaired.count("{") - repaired.count("}"), 0)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass
    # Last resort: truncate at last complete element
    last_comma = repaired.rfind(",")
    if last_comma > 10:
        truncated = repaired[:last_comma]
        truncated += "]" * max(truncated.count("[") - truncated.count("]"), 0)
        truncated += "}" * max(truncated.count("{") - truncated.count("}"), 0)
        try:
            return json.loads(truncated)
        except json.JSONDecodeError:
            pass
    raise json.JSONDecodeError("All repair attempts failed", raw, 0)

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


def rebuild_home_map(client: genai.Client, hass):
    """Generate home.md by asking Gemini to organize HA entities by room."""
    home_path = get_memory_dir() / "home.md"
    if home_path.exists() and _read(home_path):
        return

    relevant_domains = {"light", "climate", "cover", "media_player", "fan", "vacuum", "water_heater"}
    skip_keywords = {
        "camera", "motion_detection", "microphone", "speaker", "audio_recording", "pet_detection",
        "rtsp", "extra_dry", "child_lock", "notification", "backup_map", "wetness_level",
        "suction_level", "mop_pad", "cleaning_mode", "cleaning_times", "cleaning_route",
        "floor_material", "visibility",
    }
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
        response = client.models.generate_content(
            model=GEMINI_MODEL_FAST,
            contents="Generate the home layout now.",
            config=types.GenerateContentConfig(
                system_instruction=prompt,
                max_output_tokens=1500,
                temperature=0.3,
            ),
        )
        content = response.candidates[0].content.parts[0].text.strip()
        if not content.startswith("#"):
            content = "# Home Layout\n\n" + content
        _write(home_path, content)
        _LOGGER.info("Home map created by Gemini")
    except Exception as e:
        _LOGGER.error("Home map generation failed: %s", e)


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
1. REWRITE from scratch — do NOT carry over previous content verbatim.
   Look at the MEANING, not the wording. If three lines say the same thing
   differently, keep ONE — the most specific version.
2. MERGE aggressively — "use tools not guess", "always use tools", "prefers tools
   for device status" are ALL the same preference. Keep only ONE line.
3. New information wins over old when they conflict.
4. Write ALL memory in English, even though conversations are in Hebrew.
5. Keep each category CONCISE — max 20 lines. If over 15 lines, you MUST merge more.
6. If nothing worth remembering — return null for that category.

SAVE these aggressively:
- Family members: names, ages, relationships, preferences, hobbies
- Personal preferences: likes/dislikes, personality, communication style
- Patterns: recurring requests, time-based habits
- Routines: multi-step sequences ("goodnight" means lights off + shutters down + AC 24)

DO NOT save:
- One-time commands: "turn on the light" -> skip
- General questions: "what time is it?" -> skip
- Pleasantries with no new info: "thank you" -> skip
- Device inventories or entity IDs — these belong in home layout, not user memory
- Automation lists — these are queried live from HA, not stored as static text
- Corrections — return them separately, they are stored as events

Format for "user" category:
```
Name: ...
Location: ...

Preferences:
- one preference per line, no duplicates

Interests:
- one interest per line
```

Additionally, extract structured preferences using EXACTLY these known keys:
{preference_keys}
If no known key fits, use: note_<short_slug>

Respond in JSON only:
{{
  "user": "Full rewritten user memory, or null",
  "family": "Full rewritten family memory, or null",
  "habits": "Full rewritten habits, or null",
  "corrections": "Correction text if user corrected Jane, or null",
  "routines": "Full rewritten routines, or null",
  "preferences": [
    {{"person": "name", "key": "known_key", "value": "the preference", "inferred": false}}
  ] or null if no new preferences
}}"""


def _call_with_retry(client: genai.Client, prompt: str, max_retries: int = 1):
    """Call Gemini with one retry on transient errors (503, 429)."""
    import time

    for attempt in range(max_retries + 1):
        try:
            return client.models.generate_content(
                model=GEMINI_MODEL_FAST,
                contents="Analyze and respond with compact JSON. Return null for unchanged categories.",
                config=types.GenerateContentConfig(
                    system_instruction=prompt,
                    max_output_tokens=4000,
                    temperature=0.3,
                ),
            )
        except Exception as e:
            if attempt < max_retries and ("503" in str(e) or "429" in str(e) or "UNAVAILABLE" in str(e)):
                _LOGGER.info("Extraction API error, retrying in 5s: %s", e)
                time.sleep(5)  # Blocking sleep OK — runs in executor thread, not event loop
            else:
                raise


def process_memory(client: genai.Client, user_name: str, user_text: str, jane_response: str, action: str, hass=None):
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
        .replace("{preference_keys}", PREFERENCE_KEY_TAXONOMY)
    )

    try:
        response = _call_with_retry(client, prompt)

        raw = response.candidates[0].content.parts[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        if raw.endswith("```"):
            raw = raw[:-3]

        raw = raw.strip()

        # Fix truncated JSON — try to close it gracefully
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            _LOGGER.warning("Memory extraction JSON truncated, attempting repair")
            result = _repair_json(raw)

        if result.get("user"):
            save_user_memory(user_name, _ensure_str(result["user"]))
        if result.get("family"):
            save_family_memory(_ensure_str(result["family"]))
        if result.get("habits"):
            save_habits_memory(_ensure_str(result["habits"]))
        if result.get("corrections"):
            schedule_pg_append("correction", user_name, _ensure_str(result["corrections"]), {"source": "extraction"})
        if result.get("routines"):
            save_routines(_ensure_str(result["routines"]))

        # S1.3: Save structured preferences to PG
        _save_structured_preferences(hass, user_name, result.get("preferences"))

        _LOGGER.info("Memory updated for %s", user_name)

    except Exception as e:
        _LOGGER.warning("Memory extraction failed: %s", e)


def _save_structured_preferences(hass, user_name: str, preferences: list | None):
    """Save structured preferences to PG. Non-fatal."""
    if not preferences or hass is None:
        return
    try:
        from ..const import DOMAIN
        from .manager import _schedule_on_pg

        store = getattr(hass.data.get(DOMAIN), "structured", None)
        if store is None:
            return

        for pref in preferences:
            if not isinstance(pref, dict):
                continue
            person = _resolve_person_name(hass, pref.get("person", user_name))
            key = pref.get("key", "")
            value = pref.get("value", "")
            inferred = pref.get("inferred", False)
            if key and value:
                _schedule_on_pg(
                    lambda p=person, k=key, v=value, i=inferred: store.save_preference(p, k, v, inferred=i),
                    f"pref {person}/{key}",
                )
        _LOGGER.debug("Scheduled %d structured preferences", len(preferences))
    except Exception as e:
        _LOGGER.debug("Structured preference save skipped: %s", e)


def _resolve_person_name(hass, gemini_name: str) -> str:
    """Resolve Gemini's English name to HA person friendly_name (thread-safe)."""
    gemini_lower = gemini_name.lower().strip()
    try:
        for eid in hass.states.async_entity_ids("person"):
            state = hass.states.get(eid)
            if state is None:
                continue
            friendly = state.attributes.get("friendly_name", "")
            slug = eid.split(".")[-1]
            if gemini_lower in (slug, friendly.lower()) or slug.startswith(gemini_lower + "_"):
                return friendly
    except Exception as e:
        _LOGGER.debug("Person name resolution failed for %s: %s", gemini_name, e)
    return gemini_name
