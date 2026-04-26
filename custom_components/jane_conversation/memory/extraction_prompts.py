"""Extraction prompts + shared input/output helpers.

Split out of extraction.py in A3:
- Home-setup prompt (used by rebuild_home_map)
- Ops-based extraction prompt (used by process_memory via ops.py)
- Exchange utilities shared by the debouncer-driven batch extractor
"""

from __future__ import annotations

import json

_MAX_CONTEXT_CHARS = 8000  # ~2000 tokens (Hebrew+English, 4 chars/token)


def cap_exchanges(exchanges: list[dict]) -> list[dict]:
    """Keep most-recent exchanges whose combined text fits the cap.

    A single exchange exceeding the cap is still included (latest turn never dropped).
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


def format_exchanges_for_prompt(exchanges: list[dict]) -> str:
    """Render exchanges as a numbered oldest-first block."""
    lines = []
    for i, ex in enumerate(exchanges, start=1):
        lines.append(f"[{i}] User: {ex.get('text', '')}")
        lines.append(f"    Jane: {ex.get('response', '')}")
    return "\n".join(lines)


def format_snapshot_for_prompt(snapshot: dict, preferences: list[dict], persons: list[dict]) -> str:
    """Render current memory state Gemini should reason against.

    snapshot: {category: content} from PostgresBackend.load_snapshot
    preferences: [{person_name, key, value}, ...]
    persons: [{name, role, birth_date, metadata}, ...]
    """
    parts: list[str] = []
    if snapshot:
        for cat, content in snapshot.items():
            if content:
                parts.append(f"memory_entries/{cat}:\n{content}")
    if persons:
        parts.append("persons:")
        for p in persons:
            bd = p.get("birth_date")
            bd_str = bd.isoformat() if bd else "unknown"
            parts.append(f"  - {p.get('name')}: birth_date={bd_str}, role={p.get('role') or 'unknown'}")
    if preferences:
        parts.append("preferences:")
        for pref in preferences:
            parts.append(
                f"  - {pref.get('person_name')}/{pref.get('key')} = {pref.get('value')}"
                f" (confidence={pref.get('confidence', 0):.2f}, inferred={pref.get('inferred')})"
            )
    return "\n".join(parts) if parts else "(empty — no prior memory for this user)"


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


OPS_EXTRACTION_PROMPT = """You are Jane's memory editor. Given a recent conversation and the current memory state,
emit a list of ATOMIC OPERATIONS that update the memory. Do NOT rewrite everything from scratch.

Current memory state:
{memory_state}

Recent exchanges from {user_name} (oldest first — {n_exchanges} total):
{exchanges_block}

---

Respond with JSON of the form:
{{"ops": [ <op>, <op>, ... ]}}

Each <op> must be one of:
- {{"op":"ADD","target":{{"table":"<t>","key":{{...}}}},"payload":{{...}},"reason":"...","confidence":0.0-1.0}}
- {{"op":"UPDATE","target":{{"table":"<t>","key":{{...}}}},"payload":{{...}},"reason":"...","confidence":0.0-1.0}}
- {{"op":"DELETE","target":{{"table":"<t>","key":{{...}}}},"reason":"...","confidence":0.0-1.0}}
- {{"op":"NOOP","reason":"..."}}

Allowed tables + keys + payloads:
  - preferences: key={{"person":"Name","key":"taxonomy_key"}}, payload={{"value":"...","inferred":true|false}}.
      Taxonomy keys to prefer: {preference_keys}. If none fits, use note_<short_slug>.
  - persons: key={{"name":"Name"}}, payload={{"birth_date":"YYYY-MM-DD","role":"...","metadata":{{...}}}}.
      DELETE is not supported for persons in A3.
  - memory_entries: key={{"category":"user|family|habits|corrections|routines","user_name":"..."|null}},
      payload={{"content":"<FULL multi-line text>"}}.
      user_name is "<user>" for category='user', otherwise null.
  - events: ADD only — key={{"event_type":"correction"}}, payload={{"description":"..."}}.

HARD RULES:
1. You MUST extract new durable facts. Emitting NOOP when the user shared personal information is a failure.
2. Do NOT emit UPDATE for a memory_entries category unless you are adding, changing, or removing specific lines.
3. Do NOT emit ADD for a fact that already exists in the memory snapshot — use UPDATE only if the value changed.
4. Never emit DELETE without a clear user signal (correction, contradiction, or explicit forget request).
5. When updating memory_entries, INCLUDE ALL existing lines plus any new ones. Never drop existing lines unless explicitly corrected.
6. If unsure whether a statement is a durable fact or a one-time remark, emit low-confidence (0.3-0.5) ADD rather than NOOP.
7. `reason` is required for every non-NOOP op — one sentence.
8. All memory content written in English even though conversations are in Hebrew.
9. Skip one-time commands ("turn on the light"), general questions ("what time is it"), and pleasantries.

## Example 1 — New preference (ADD)
Snapshot: (empty)
Exchange: User: "I love black coffee"  Jane: "noted"
Output: {{"ops":[{{"op":"ADD","target":{{"table":"preferences","key":{{"person":"{user_name}","key":"beverage_preference"}}}},"payload":{{"value":"black coffee","inferred":false}},"reason":"user stated preference","confidence":0.95}}]}}

## Example 2 — Correction (UPDATE)
Snapshot: preferences: {user_name}/beverage_preference="espresso"
Exchange: User: "actually I prefer filter coffee now"  Jane: "updated"
Output: {{"ops":[{{"op":"UPDATE","target":{{"table":"preferences","key":{{"person":"{user_name}","key":"beverage_preference"}}}},"payload":{{"value":"filter coffee"}},"reason":"user corrected prior preference","confidence":0.9}}]}}

## Example 3 — Multi-turn fact (birthday)
Snapshot: persons: {user_name} (no birth_date)
Exchanges: [1] "how are you?" / "good" [2] "my birthday is June 15" / "noted" [3] "turn on the light" / "done"
Output: {{"ops":[{{"op":"ADD","target":{{"table":"persons","key":{{"name":"{user_name}"}}}},"payload":{{"birth_date":"2000-06-15"}},"reason":"user mentioned birthday in turn 2","confidence":0.9}}]}}

## Example 4 — Nothing durable (NOOP)
Exchanges: [1] "what time is it?" / "14:30" [2] "turn off the light" / "done"
Output: {{"ops":[{{"op":"NOOP","reason":"only commands and time queries, no durable facts"}}]}}

## Example 5 — Existing fact restated (NOOP, not re-ADD)
Snapshot: persons: {user_name} birth_date=2000-06-15
Exchange: User: "my birthday is June 15, like I said"  Jane: "I remember"
Output: {{"ops":[{{"op":"NOOP","reason":"birthday already in memory — no change"}}]}}

## Example 6 — memory_entries category update (full content)
Snapshot: memory_entries/family = "Alice is my wife.\\nBob is our son, 6 years old."
Exchange: User: "my little Carol is 2 years old"  Jane: "cute"
Output: {{"ops":[{{"op":"UPDATE","target":{{"table":"memory_entries","key":{{"category":"family","user_name":null}}}},"payload":{{"content":"Alice is my wife.\\nBob is our son, 6 years old.\\nCarol is our daughter, 2 years old."}},"reason":"adding new family member while preserving existing entries","confidence":0.95}}]}}

## Example 7 — Conflicting fact (UPDATE not ADD)
Snapshot: preferences: {user_name}/beverage_preference="coffee"
Exchange: User: "actually I only drink tea now"  Jane: "updated"
Output: {{"ops":[{{"op":"UPDATE","target":{{"table":"preferences","key":{{"person":"{user_name}","key":"beverage_preference"}}}},"payload":{{"value":"tea"}},"reason":"user switched preference from coffee to tea","confidence":0.95}}]}}

Respond with JSON only. No prose."""


def build_ops_prompt(
    exchanges: list[dict],
    user_name: str,
    snapshot: dict,
    preferences: list[dict],
    persons: list[dict],
    preference_keys: str,
    recently_removed: list[str] | None = None,
) -> str:
    """Substitute placeholders in OPS_EXTRACTION_PROMPT.

    Caller must pass pre-capped `exchanges` — this helper does NOT re-cap (see PR #44 review §4).

    `recently_removed` (B2 / JANE-81): list of "{person}:{normalized_key}" strings
    the user explicitly forgot via the forget_memory tool. When non-empty, an
    "DO NOT re-extract" block is prepended to the snapshot section so Gemini
    doesn't re-emit ADD ops for these. Hard enforcement happens in OpApplier;
    the prompt is purely a deterrent.
    """
    snapshot_block = format_snapshot_for_prompt(snapshot, preferences, persons)
    if recently_removed:
        bullets = "\n".join(f"- {key}" for key in recently_removed)
        snapshot_block = (
            "[RECENTLY REMOVED FACTS — user explicitly asked to forget these]\n"
            'The user used "forget_memory" to remove the following facts. They have\n'
            "not re-stated them since. DO NOT emit ADD ops for these:\n"
            f"{bullets}\n"
            "[end of list]\n\n"
        ) + snapshot_block

    return (
        OPS_EXTRACTION_PROMPT.replace("{memory_state}", snapshot_block)
        .replace("{user_name}", user_name)
        .replace("{n_exchanges}", str(len(exchanges)))
        .replace("{exchanges_block}", format_exchanges_for_prompt(exchanges))
        .replace("{preference_keys}", preference_keys)
    )


def extract_json_from_gemini(raw_text: str) -> str:
    """Strip code fences from Gemini's response. Returns JSON string (possibly still malformed)."""
    raw = raw_text.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1] if "```" in raw[3:] else raw[3:]
        if raw.startswith("json"):
            raw = raw[4:]
    if raw.endswith("```"):
        raw = raw[:-3]
    return raw.strip()


def repair_json(raw: str) -> dict:
    """Best-effort repair for truncated Gemini JSON. Raises JSONDecodeError if unfixable."""
    repaired = raw
    escaped_count = raw.count('\\"')
    real_quotes = raw.count('"') - escaped_count
    if real_quotes % 2 != 0:
        repaired += '"'
    if repaired.rstrip().endswith(","):
        repaired = repaired.rstrip()[:-1]
    repaired += "]" * max(repaired.count("[") - repaired.count("]"), 0)
    repaired += "}" * max(repaired.count("{") - repaired.count("}"), 0)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass
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
