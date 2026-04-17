"""Memory handlers — save_memory, read_memory."""

import logging

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


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
