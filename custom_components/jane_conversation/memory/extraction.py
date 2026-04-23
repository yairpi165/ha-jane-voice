"""Memory extraction — LLM-based memory processing and home map generation.

Fully async — reads/writes via PostgresBackend, no MD file I/O.
"""

import json
import logging

from google import genai
from google.genai import types

from ..const import DOMAIN, GEMINI_MODEL_FAST, PREFERENCE_KEY_TAXONOMY
from .manager import get_backend

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


async def rebuild_home_map(client: genai.Client, hass):
    """Generate home layout via Gemini and store in PG."""
    backend = get_backend()
    existing = await backend.load("home")
    if existing:
        return

    relevant_domains = {"light", "climate", "cover", "media_player", "fan", "vacuum", "water_heater"}
    skip_keywords = {
        "camera",
        "motion_detection",
        "microphone",
        "speaker",
        "audio_recording",
        "pet_detection",
        "rtsp",
        "extra_dry",
        "child_lock",
        "notification",
        "backup_map",
        "wetness_level",
        "suction_level",
        "mop_pad",
        "cleaning_mode",
        "cleaning_times",
        "cleaning_route",
        "floor_material",
        "visibility",
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
        response = await hass.async_add_executor_job(
            _call_gemini_simple, client, prompt, "Generate the home layout now.", 1500
        )
        content = response.candidates[0].content.parts[0].text.strip()
        if not content.startswith("#"):
            content = "# Home Layout\n\n" + content
        await backend.save("home", content)
        _LOGGER.info("Home map created by Gemini (stored in PG)")
    except Exception as e:
        _LOGGER.error("Home map generation failed: %s", e)


def _call_gemini_simple(client, system_prompt, user_msg, max_tokens):
    """Sync Gemini call for simple generation tasks (runs in executor)."""
    return client.models.generate_content(
        model=GEMINI_MODEL_FAST,
        contents=user_msg,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=max_tokens,
            temperature=0.3,
        ),
    )


_MAX_CONTEXT_CHARS = 8000  # ~2000 tokens (Hebrew+English, 4 chars/token)


def _cap_exchanges(exchanges: list[dict]) -> list[dict]:
    """Keep most-recent exchanges whose combined text fits the cap.

    A single exchange that exceeds the cap is still included (the `and kept` guard),
    so we never drop the latest turn.
    """
    total = 0
    kept: list[dict] = []
    for ex in reversed(exchanges):
        size = len(ex.get("text", "")) + len(ex.get("response", ""))
        if total + size > _MAX_CONTEXT_CHARS and kept:
            break
        total += size
        kept.append(ex)
    return list(reversed(kept))


def _format_exchanges_for_prompt(exchanges: list[dict]) -> str:
    """Render exchanges as a numbered oldest-first block for the extraction prompt."""
    lines = []
    for i, ex in enumerate(exchanges, start=1):
        lines.append(f"[{i}] User: {ex.get('text', '')}")
        lines.append(f"    Jane: {ex.get('response', '')}")
    return "\n".join(lines)


MEMORY_EXTRACTION_PROMPT = """You are the memory manager for Jane, a Hebrew smart home assistant.
Analyze the conversation and decide what to remember.

Current memory:
{memory_context}

---

Recent exchanges from {user_name} (oldest first — {n_exchanges} total):
{exchanges_block}

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
  ] or null if no new preferences,
  "birthdays": [
    {{"person": "name", "date": "actual date like 1992-07-17"}}
  ] or null if no birthdays mentioned
}}"""


def _call_with_retry(client: genai.Client, prompt: str, max_retries: int = 1):
    """Call Gemini with one retry on transient errors (503, 429). Sync — runs in executor."""
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
                time.sleep(5)  # Blocking sleep OK — runs in executor thread
            else:
                raise


async def process_memory(
    client: genai.Client,
    user_name: str,
    exchanges: list[dict],
    action: str,  # TODO(A3): action no longer meaningful per-turn in multi-exchange bursts
    hass=None,
):
    """Analyze burst of exchanges and update memory if needed. Async — runs on event loop.

    exchanges: list of {"text": str, "response": str, "ts": float, "user": str}.
    """
    # Narrow single-exchange ha_service skip — multi-exchange always extracts
    # (one turn may be a short device command while another carries durable facts).
    if action == "ha_service" and len(exchanges) == 1:
        if len(exchanges[0].get("response", "")) < 30:
            return

    capped = _cap_exchanges(exchanges)
    if not capped:
        _LOGGER.debug("process_memory: no exchanges after cap — skipping")
        return

    backend = get_backend()
    memory_context = await backend.load_all(user_name)

    prompt = (
        MEMORY_EXTRACTION_PROMPT.replace("{memory_context}", memory_context)
        .replace("{user_name}", user_name)
        .replace("{n_exchanges}", str(len(capped)))
        .replace("{exchanges_block}", _format_exchanges_for_prompt(capped))
        .replace("{preference_keys}", PREFERENCE_KEY_TAXONOMY)
    )

    try:
        # Gemini SDK is sync — run in executor
        response = await hass.async_add_executor_job(_call_with_retry, client, prompt)

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
            await backend.save("user", _ensure_str(result["user"]), user_name)
        if result.get("family"):
            await backend.save("family", _ensure_str(result["family"]))
        if result.get("habits"):
            await backend.save("habits", _ensure_str(result["habits"]))
        if result.get("corrections"):
            await _save_correction_dedup(hass, user_name, _ensure_str(result["corrections"]))
        if result.get("routines"):
            await backend.save("routines", _ensure_str(result["routines"]))

        # Save structured preferences to PG
        await _save_structured_preferences(hass, user_name, result.get("preferences"))

        # Save birthdays to persons table
        await _save_birthdays(hass, user_name, result.get("birthdays"))

        _LOGGER.info("Memory updated for %s", user_name)

    except Exception as e:
        _LOGGER.warning("Memory extraction failed: %s", e)


async def _save_structured_preferences(hass, user_name: str, preferences: list | None):
    """Save structured preferences to PG. Non-fatal."""
    if not preferences or hass is None:
        return
    try:
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
                try:
                    await store.save_preference(person, key, value, inferred=inferred)
                except Exception as e:
                    _LOGGER.warning("Pref save failed (%s/%s): %s", person, key, e)
        _LOGGER.debug("Saved %d structured preferences", len(preferences))
    except Exception as e:
        _LOGGER.debug("Structured preference save skipped: %s", e)


async def _save_birthdays(hass, user_name: str, birthdays: list | None):
    """Save birthday dates to persons table. Non-fatal."""
    if not birthdays or hass is None:
        return
    try:
        store = getattr(hass.data.get(DOMAIN), "structured", None)
        if store is None:
            return

        for entry in birthdays:
            if not isinstance(entry, dict):
                continue
            person = _resolve_person_name(hass, entry.get("person", ""))
            date_str = entry.get("date", "")
            if not person or not date_str:
                continue
            normalized = _normalize_date(date_str)
            if normalized is None:
                _LOGGER.warning("Could not parse birthday date '%s' for %s", date_str, person)
                continue
            try:
                await store.save_person(person, birth_date=normalized)
            except Exception as e:
                _LOGGER.warning("Birthday save failed (%s): %s", person, e)
        _LOGGER.debug("Saved %d birthday updates", len(birthdays))
    except Exception as e:
        _LOGGER.debug("Birthday save skipped: %s", e)


def _normalize_date(date_str: str):
    """Parse various date formats to datetime.date object (for asyncpg)."""
    from datetime import datetime as _dt

    if not date_str or not date_str.strip():
        return None
    s = date_str.strip()
    # Try common formats (Gemini typically returns YYYY-MM-DD or Month D, YYYY)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%d %B %Y"):
        try:
            return _dt.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


async def _save_correction_dedup(hass, user_name: str, correction_text: str):
    """Save correction to PG only if no identical correction exists in last 24h."""
    try:
        pool = getattr(hass.data.get(DOMAIN), "pg_pool", None)
        if pool is None:
            await get_backend().append_event("correction", user_name, correction_text, {"source": "extraction"})
            return

        async with pool.acquire() as conn:
            exists = await conn.fetchval(
                """SELECT 1 FROM events
                   WHERE event_type = 'correction' AND user_name = $1
                     AND description = $2
                     AND timestamp > NOW() - INTERVAL '24 hours'
                   LIMIT 1""",
                user_name,
                correction_text,
            )
            if not exists:
                await conn.execute(
                    """INSERT INTO events (event_type, user_name, description, metadata)
                       VALUES ('correction', $1, $2, '{"source": "extraction"}'::jsonb)""",
                    user_name,
                    correction_text,
                )
    except Exception as e:
        _LOGGER.debug("Correction dedup failed: %s", e)


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
