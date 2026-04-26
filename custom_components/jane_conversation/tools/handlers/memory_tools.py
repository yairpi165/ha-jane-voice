"""Memory handlers — save_memory, forget_memory, read_memory."""

import json
import logging
import time

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_VALID_FORGET_CATEGORIES = {"user", "family", "habits", "routines"}


def _resolve_user_name(hass: HomeAssistant, gemini_name: str) -> str:
    """Resolve user_name to HA person friendly_name to avoid duplicates."""
    gemini_lower = gemini_name.lower().strip()
    for state in hass.states.async_all("person"):
        friendly = state.attributes.get("friendly_name", "")
        entity_slug = state.entity_id.split(".")[-1]
        if (
            gemini_lower == entity_slug
            or entity_slug.startswith(gemini_lower + "_")
            or gemini_lower == friendly.lower()
        ):
            return friendly
    return gemini_name


async def handle_save_memory(hass: HomeAssistant, args: dict) -> str:
    """Explicitly save to Jane's memory (PG backend)."""
    from ...memory.manager import get_backend

    backend = get_backend()
    category = args.get("category", "")
    content = args.get("content", "")
    user_name = _resolve_user_name(hass, args.get("user_name", "default"))

    if not content:
        return "Error: content is required."

    valid = {"user", "family", "habits", "corrections", "routines"}
    if category not in valid:
        return f"Unknown category: {category}. Use: {', '.join(valid)}"

    uname = user_name if category == "user" else None
    existing = await backend.load(category, uname)
    new_content = (existing + "\n" + content) if existing else content

    await backend.save(category, new_content, uname)
    _LOGGER.info("Memory saved: category=%s, length=%d", category, len(new_content))
    return f"Saved to {category} memory."


async def handle_forget_memory(hass: HomeAssistant, args: dict) -> str:
    """Emit a DELETE op through OpApplier for a specific preference or memory category.

    Returns a JSON string so Jane can phrase the response in Hebrew herself rather than
    reading raw English error text aloud.
    """
    from ...const import DOMAIN
    from ...memory.manager import get_backend
    from ...memory.ops import MemoryOp
    from ...memory.ops_applier import OpApplier

    def _err(code: str, detail: str = "") -> str:
        return json.dumps({"status": "error", "code": code, "detail": detail}, ensure_ascii=False)

    target_table = args.get("target_table", "")
    target_key = args.get("target_key")
    reason = (args.get("reason") or "").strip() or "user requested forget"

    if target_table not in {"preferences", "memory_entries"}:
        return _err("invalid_table", str(target_table))
    if not isinstance(target_key, dict):
        return _err("invalid_target_key_shape", type(target_key).__name__)

    if target_table == "preferences":
        person = _resolve_user_name(hass, target_key.get("person", "default"))
        pref_key = target_key.get("key")
        if not pref_key:
            return _err("missing_preference_key")
        op_key = {"person": person, "key": pref_key}
        user_name = person
    else:  # memory_entries — `corrections` intentionally excluded (A3 moved corrections
        # to op-based writes; the text-blob category is vestigial).
        category = target_key.get("category")
        if category not in _VALID_FORGET_CATEGORIES:
            return _err("invalid_category", str(category))
        resolved_user = _resolve_user_name(hass, target_key.get("user_name", "default"))
        op_key = {
            "category": category,
            "user_name": resolved_user if category == "user" else None,
        }
        user_name = resolved_user

    jane = hass.data.get(DOMAIN)
    if not jane or not getattr(jane, "structured", None) or not getattr(jane, "pg_pool", None):
        return _err("subsystem_unavailable")

    # Pre-check: if the row isn't live, return a clean noop without emitting a DELETE op.
    # OpApplier counts every DELETE as `result.deleted`, so we need this guard to distinguish
    # "actually forgot something" from "user asked to forget something that wasn't stored".
    backend = get_backend()
    if target_table == "preferences":
        existing = await jane.structured.load_preference(op_key["person"], op_key["key"])
        if not existing:
            return json.dumps(
                {"status": "noop", "code": "not_live", "table": target_table, "key": op_key},
                ensure_ascii=False,
            )
    else:  # memory_entries
        existing = await backend.load(op_key["category"], op_key["user_name"])
        if not existing:
            return json.dumps(
                {"status": "noop", "code": "not_live", "table": target_table, "key": op_key},
                ensure_ascii=False,
            )

    op = MemoryOp(
        op="DELETE",
        target_table=target_table,
        target_key=op_key,
        payload={},
        reason=reason,
        confidence=1.0,
    )
    session_id = f"tool-forget-{int(time.time())}"
    applier = OpApplier(backend=backend, structured=jane.structured, pg_pool=jane.pg_pool)
    raw_response = json.dumps({"tool": "forget_memory", "args": args}, ensure_ascii=False)
    result = await applier.apply_all(
        [op],
        user_name=user_name,
        session_id=session_id,
        memory_snapshot={},
        raw_response=raw_response,
    )

    if result.deleted:
        _LOGGER.info("forget_memory: deleted %s/%s", target_table, op_key)
        # B2 (JANE-81): mark this fact as "recently removed" so the extractor's
        # next ADD on the same key gets downgraded to NOOP. Populated AT
        # forget time (not at later consolidation purge) because the soft-
        # delete revives via ON CONFLICT — the tombstone alone gives no
        # protection. ZSET TTL aligns with tombstone retention (30d).
        if target_table == "preferences" and getattr(jane, "redis", None):
            from ...memory.consolidation_pass import (
                RECENTLY_REMOVED_KEY,
                RECENTLY_REMOVED_TTL_SECONDS,
            )
            from ...memory.structured import _normalize_pref_key

            try:
                person = await jane.structured.canonical_person(op_key.get("person", ""), user_name)
                norm_key = _normalize_pref_key(op_key.get("key", ""))
                score = int(time.time())
                await jane.redis.zadd(RECENTLY_REMOVED_KEY, {f"{person}:{norm_key}": score})
                await jane.redis.expire(RECENTLY_REMOVED_KEY, RECENTLY_REMOVED_TTL_SECONDS)
            except Exception as e:
                # WARNING (not DEBUG): forget_memory is a synchronous user-facing
                # tool and the ZSET write is part of its success contract — if it
                # fails, the soft-delete will revive via ON CONFLICT on the next
                # ADD and Jane will silently "remember" something the user
                # explicitly forgot. Make the breakage visible at default HA logs.
                _LOGGER.warning(
                    "forget_memory: recently_removed ZSET write failed for %s/%s: %s",
                    target_table,
                    op_key,
                    e,
                )
        return json.dumps({"status": "ok", "table": target_table, "key": op_key}, ensure_ascii=False)
    if result.failed:
        return _err("apply_failed")
    return json.dumps(
        {"status": "noop", "code": "not_live", "table": target_table, "key": op_key},
        ensure_ascii=False,
    )


async def handle_query_history(hass: HomeAssistant, args: dict) -> str:
    """Query episodic history — time-based + semantic search."""
    from datetime import datetime, timedelta

    from ...const import DOMAIN

    episodic = getattr(hass.data.get(DOMAIN), "episodic", None)
    if not episodic:
        return "History not available — episodic memory not configured."

    hours = min(int(args.get("hours_back", 24)), 168)
    query = args.get("query", "")
    now = datetime.now().astimezone()
    start = now - timedelta(hours=hours)

    episodes = await episodic.query_episodes(start, now, limit=20)

    semantic_results = []
    semantic_summaries = []
    if query:
        try:
            from ...memory.embeddings import generate_embedding

            client = getattr(hass.data.get(DOMAIN), "gemini_client", None)
            if client:
                embedding = await generate_embedding(hass, client, query)
                if embedding:
                    semantic_results = await episodic.semantic_search(embedding, limit=5)
                    semantic_summaries = await episodic.semantic_search_summaries(embedding, limit=3)
        except Exception:
            pass

    seen_ids = set()
    lines = []

    for ep in semantic_results:
        if ep["id"] in seen_ids:
            continue
        seen_ids.add(ep["id"])
        ts = ep["start_ts"]
        time_str = ts.strftime("%d/%m %H:%M") if hasattr(ts, "strftime") else str(ts)
        sim = ep.get("similarity", 0)
        lines.append(f"[{sim:.0%}] {time_str} — {ep['title']}: {ep['summary']}")

    for ep in episodes:
        if ep["id"] in seen_ids:
            continue
        seen_ids.add(ep["id"])
        ts = ep["start_ts"]
        time_str = ts.strftime("%d/%m %H:%M") if hasattr(ts, "strftime") else str(ts)
        lines.append(f"{time_str} — {ep['title']}: {ep['summary']}")

    for ds in semantic_summaries if query else []:
        date_str = str(ds["summary_date"])
        lines.append(f"[daily] {date_str}: {ds['summary']}")

    if not lines:
        return f"No episodes found in the last {hours} hours."
    return "\n".join(lines)


async def handle_read_memory(hass: HomeAssistant, args: dict) -> str:
    """Read memory from PG backend."""
    from ...const import DOMAIN
    from ...memory.manager import get_backend

    category = args.get("category", "")
    user_name = args.get("user_name", "default")

    valid = {"user", "family", "habits", "corrections", "routines", "actions"}
    if category not in valid:
        return f"Unknown category: {category}. Available: {', '.join(valid)}"

    # Actions live in events table, not memory_entries
    if category == "actions":
        try:
            pool = getattr(hass.data.get(DOMAIN), "pg_pool", None)
            if pool:
                async with pool.acquire() as conn:
                    rows = await conn.fetch(
                        """SELECT description, timestamp FROM events
                           WHERE event_type = 'action'
                             AND timestamp > NOW() - INTERVAL '24 hours'
                           ORDER BY timestamp DESC LIMIT 20""",
                    )
                    if not rows:
                        return "No recent actions in the last 24 hours."
                    lines = [f"- {r['timestamp'].strftime('%H:%M')} — {r['description']}" for r in rows]
                    return "Recent actions (24h):\n" + "\n".join(lines)
        except Exception:
            return "Could not load actions."

    backend = get_backend()
    uname = user_name if category == "user" else None
    content = await backend.load(category, uname)
    if not content:
        return f"No {category} memory saved yet."
    return content
