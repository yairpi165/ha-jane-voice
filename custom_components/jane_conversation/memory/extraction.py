"""Memory extraction — Gemini-driven op generation + home map bootstrap.

A3: replaces the per-turn "REWRITE from scratch" JSON with a list of typed operations
(ADD/UPDATE/DELETE/NOOP) each validated and applied by OpApplier. The rewrite prompt
is gone; only the home-setup prompt remains for initial `home` category population.
"""

from __future__ import annotations

import json
import logging
import time

from google import genai
from google.genai import types

from ..const import DOMAIN, GEMINI_MODEL_FAST, PREFERENCE_KEY_TAXONOMY
from .extraction_prompts import (
    _MAX_CONTEXT_CHARS,
    HOME_SETUP_PROMPT,
    build_ops_prompt,
    cap_exchanges,
    extract_json_from_gemini,
    format_exchanges_for_prompt,
    repair_json,
)
from .manager import get_backend
from .ops import parse_ops_json
from .ops_applier import OpApplier

_LOGGER = logging.getLogger(__name__)

# Backwards-compat aliases for tests (pre-A3 naming convention)
_cap_exchanges = cap_exchanges
_format_exchanges_for_prompt = format_exchanges_for_prompt
_repair_json = repair_json
__all__ = [
    "process_memory", "rebuild_home_map", "_MAX_CONTEXT_CHARS",
    "_cap_exchanges", "_format_exchanges_for_prompt", "_repair_json",
    "_normalize_date",
]


def _ensure_str(value) -> str:
    """Coerce dict/list to str — kept for any callers of legacy save paths."""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


# ----- home map (unchanged from pre-A3) -----


async def rebuild_home_map(client: genai.Client, hass):
    """Generate home layout via Gemini and store in PG."""
    backend = get_backend()
    existing = await backend.load("home")
    if existing:
        return

    relevant_domains = {"light", "climate", "cover", "media_player", "fan", "vacuum", "water_heater"}
    skip_keywords = {
        "camera", "motion_detection", "microphone", "speaker", "audio_recording",
        "pet_detection", "rtsp", "extra_dry", "child_lock", "notification", "backup_map",
        "wetness_level", "suction_level", "mop_pad", "cleaning_mode", "cleaning_times",
        "cleaning_route", "floor_material", "visibility",
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
    """Sync Gemini call for simple generation (runs in executor)."""
    return client.models.generate_content(
        model=GEMINI_MODEL_FAST,
        contents=user_msg,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=max_tokens,
            temperature=0.3,
        ),
    )


def _call_with_retry(client: genai.Client, prompt: str, max_retries: int = 1):
    """Call Gemini with one retry on transient errors. Sync — runs in executor thread."""
    import time as _t

    for attempt in range(max_retries + 1):
        try:
            return client.models.generate_content(
                model=GEMINI_MODEL_FAST,
                contents="Analyze the conversation and respond with compact JSON ops.",
                config=types.GenerateContentConfig(
                    system_instruction=prompt,
                    max_output_tokens=4000,
                    temperature=0.3,
                ),
            )
        except Exception as e:
            if attempt < max_retries and ("503" in str(e) or "429" in str(e) or "UNAVAILABLE" in str(e)):
                _LOGGER.info("Extraction API error, retrying in 5s: %s", e)
                _t.sleep(5)
            else:
                raise


# ----- ops-based process_memory -----


async def process_memory(
    client: genai.Client,
    user_name: str,
    exchanges: list[dict],
    action: str,  # Legacy: only used for single-exchange ha_service skip
    hass=None,
):
    """Extract typed ops from a burst and apply them via OpApplier.

    exchanges: list of {"text", "response", "ts", "conv_id", "user"} from the debouncer.
    """
    # Legacy single-exchange short-response skip.
    if action == "ha_service" and len(exchanges) == 1 and len(exchanges[0].get("response", "")) < 30:
        return

    capped = cap_exchanges(exchanges)
    if not capped:
        _LOGGER.debug("process_memory: no exchanges after cap — skipping")
        return

    if hass is None:
        _LOGGER.warning("process_memory: no hass handle — skipping")
        return

    jane = hass.data.get(DOMAIN)
    backend = get_backend()
    structured = getattr(jane, "structured", None) if jane else None
    pg_pool = getattr(jane, "pg_pool", None) if jane else None
    if structured is None or pg_pool is None:
        _LOGGER.warning("process_memory: structured/pg_pool unavailable — skipping")
        return

    snapshot = await backend.load_snapshot(user_name) if hasattr(backend, "load_snapshot") else {}
    persons = await structured.load_persons()
    prefs_map = await structured.load_all_preferences(min_confidence=0.3)
    prefs_flat = [
        {"person_name": p, **pref}
        for p, prefs in prefs_map.items()
        for pref in prefs
    ]

    prompt = build_ops_prompt(capped, user_name, snapshot, prefs_flat, persons, PREFERENCE_KEY_TAXONOMY)

    try:
        response = await hass.async_add_executor_job(_call_with_retry, client, prompt)
    except Exception as e:
        _LOGGER.warning("Extraction Gemini call failed: %s", e)
        return

    raw = extract_json_from_gemini(response.candidates[0].content.parts[0].text)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        _LOGGER.warning("Ops extraction JSON truncated, attempting repair")
        try:
            data = repair_json(raw)
        except json.JSONDecodeError:
            _LOGGER.warning("Ops extraction JSON unrepairable — dropping batch")
            return

    ops = parse_ops_json(data)
    session_id = capped[-1].get("conv_id") or f"adhoc-{int(time.time())}"
    applier = OpApplier(backend=backend, structured=structured, pg_pool=pg_pool)
    result = await applier.apply_all(
        ops, user_name, session_id=session_id,
        memory_snapshot=snapshot, raw_response=raw,
    )
    _LOGGER.info(
        "Memory ops for %s (session=%s, %d ops): %s",
        user_name, session_id, len(ops), result.summary(),
    )


# ----- date parsing (kept here for backwards-compat with tests) -----


def _normalize_date(date_str: str):
    """Parse various date formats to datetime.date. Returns None on failure."""
    from datetime import datetime as _dt

    if not date_str or not date_str.strip():
        return None
    s = date_str.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%d %B %Y"):
        try:
            return _dt.strptime(s, fmt).date()
        except ValueError:
            continue
    return None
