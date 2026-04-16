"""Consolidation Worker — groups raw events into episodes and daily summaries (S1.4).

Runs periodically (every 6 hours) to convert raw state_change events into
meaningful episode narratives. Generates a daily summary once per day.
"""

import json
import logging
import time
from datetime import datetime, timedelta

from ..const import DOMAIN, EPISODE_GAP_MINUTES, EPISODE_MAX_DURATION_MINUTES, GEMINI_MODEL_FAST

_LOGGER = logging.getLogger(__name__)

MAX_LLM_CALLS_PER_WINDOW = 3

# Template thresholds
MIN_EVENTS_FOR_EPISODE = 2  # Skip single-event clusters


class ConsolidationWorker:
    """Groups raw events into episodes and generates daily summaries."""

    def __init__(self, episodic_store, hass):
        self._episodic = episodic_store
        self._hass = hass

    def _get_gemini_client(self):
        """Lazily obtain Gemini client from hass.data."""
        return getattr(self._hass.data.get(DOMAIN), "gemini_client", None)

    # ------------------------------------------------------------------
    # Event → Episode consolidation
    # ------------------------------------------------------------------

    async def consolidate_events(self) -> int:
        """Group recent events into episodes. Returns number of episodes created."""
        # Idempotency check
        now = datetime.now().astimezone()
        window_start = now - timedelta(hours=6)

        last_run = await self._episodic.get_last_consolidation_ts()
        if last_run and last_run >= window_start:
            _LOGGER.debug("Consolidation: window already processed (last=%s)", last_run)
            return 0

        events = await self._episodic.query_events(window_start, now, event_type="state_change")
        if not events:
            await self._episodic.set_last_consolidation_ts(now)
            return 0

        # Also include conversation events for richer episodes
        convos = await self._episodic.query_events(window_start, now, event_type="conversation")
        all_events = sorted(events + convos, key=lambda e: e["timestamp"])

        clusters = _cluster_events(all_events)
        episode_count = 0
        llm_calls = 0

        for cluster in clusters:
            if len(cluster) < MIN_EVENTS_FOR_EPISODE:
                continue

            is_complex = len(cluster) > 5 or _is_mixed_domain(cluster)

            if is_complex and llm_calls < MAX_LLM_CALLS_PER_WINDOW:
                episode = await self._summarize_with_llm(cluster)
                llm_calls += 1
            else:
                episode = _template_summary(cluster)

            if episode:
                start_ts = cluster[0]["timestamp"]
                end_ts = cluster[-1]["timestamp"]
                ep_id = await self._episodic.save_episode(
                    title=episode["title"],
                    summary=episode["summary"],
                    start_ts=start_ts,
                    end_ts=end_ts,
                    episode_type=episode.get("episode_type", "activity"),
                )
                episode_count += 1
                # Generate embedding for semantic search (non-fatal)
                await self._embed_episode(ep_id, episode["title"], episode["summary"])

        await self._episodic.set_last_consolidation_ts(now)

        if episode_count:
            _LOGGER.info("Consolidated %d episodes from %d events", episode_count, len(all_events))
        return episode_count

    async def _summarize_with_llm(self, cluster: list[dict]) -> dict | None:
        """Use Gemini Flash to summarize a complex event cluster."""
        client = self._get_gemini_client()
        if not client:
            return _template_summary(cluster)

        event_lines = []
        for e in cluster[:20]:  # Cap at 20 events to control token usage
            ts = e["timestamp"].strftime("%H:%M")
            event_lines.append(f"{ts} {e['description']}")

        prompt = (
            "אתה מנתח אירועים בבית חכם. תן סיכום קצר בעברית.\n"
            "החזר JSON בלבד:\n"
            '{"title": "כותרת קצרה", "summary": "משפט אחד", '
            '"episode_type": "arrival|departure|routine|conversation|activity"}\n\n'
            "אירועים:\n" + "\n".join(event_lines)
        )

        try:
            response = await self._hass.async_add_executor_job(
                lambda: _call_gemini(client, prompt)
            )

            if not response or not response.candidates:
                return _template_summary(cluster)

            raw = response.candidates[0].content.parts[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            if raw.endswith("```"):
                raw = raw[:-3]

            return json.loads(raw.strip())
        except Exception as e:
            _LOGGER.debug("LLM episode summary failed, using template: %s", e)
            return _template_summary(cluster)

    # ------------------------------------------------------------------
    # Embeddings (S1.6)
    # ------------------------------------------------------------------

    async def _embed_episode(self, episode_id: int, title: str, summary: str):
        """Generate and store embedding for an episode. Non-fatal."""
        try:
            from .embeddings import generate_embedding, store_episode_embedding

            client = self._get_gemini_client()
            if not client:
                return
            embedding = await generate_embedding(self._hass, client, f"{title} {summary}")
            if embedding:
                await store_episode_embedding(self._episodic._pool, episode_id, embedding)
        except Exception as e:
            _LOGGER.debug("Episode embedding failed (id=%d): %s", episode_id, e)

    async def _embed_summary(self, summary_date, summary: str):
        """Generate and store embedding for a daily summary. Non-fatal."""
        try:
            from .embeddings import generate_embedding, store_summary_embedding

            client = self._get_gemini_client()
            if not client:
                return
            embedding = await generate_embedding(self._hass, client, summary)
            if embedding:
                await store_summary_embedding(self._episodic._pool, summary_date, embedding)
        except Exception as e:
            _LOGGER.debug("Summary embedding failed (%s): %s", summary_date, e)

    # ------------------------------------------------------------------
    # Daily summary
    # ------------------------------------------------------------------

    async def generate_daily_summary(self) -> bool:
        """Generate a daily summary for yesterday. Returns True if created."""
        from datetime import date

        yesterday = date.today() - timedelta(days=1)

        # Check if already exists
        existing = await self._episodic.get_daily_summary(yesterday)
        if existing:
            return False

        # Get yesterday's episodes
        start = datetime.combine(yesterday, datetime.min.time()).astimezone()
        end = start + timedelta(days=1)

        episodes = await self._episodic.query_episodes(start, end, limit=50)
        events = await self._episodic.query_events(start, end)

        if not episodes and not events:
            return False

        client = self._get_gemini_client()
        if client and episodes:
            summary = await self._generate_summary_with_llm(episodes, len(events))
        else:
            summary = _template_daily_summary(episodes, len(events))

        await self._episodic.save_daily_summary(
            summary_date=yesterday,
            summary=summary,
            event_count=len(events),
            episode_count=len(episodes),
        )
        # Generate embedding for semantic search (non-fatal)
        await self._embed_summary(yesterday, summary)
        _LOGGER.info("Daily summary created for %s (%d episodes, %d events)", yesterday, len(episodes), len(events))
        return True

    async def _generate_summary_with_llm(self, episodes: list[dict], event_count: int) -> str:
        """Use Gemini Flash to generate a daily narrative summary in Hebrew."""
        client = self._get_gemini_client()
        if not client:
            return _template_daily_summary(episodes, event_count)

        episode_lines = []
        for ep in episodes[:15]:
            t = ep["start_ts"].strftime("%H:%M") if hasattr(ep["start_ts"], "strftime") else str(ep["start_ts"])
            episode_lines.append(f"{t} — {ep['title']}: {ep['summary']}")

        prompt = (
            "אתה מסכם את היום של משפחה בבית חכם. תן סיכום טבעי בעברית, 2-4 משפטים.\n"
            "אל תשתמש באמוג'ים. תתאר את היום בצורה טבעית ותמציתית.\n\n"
            f"סך הכל {event_count} אירועים, {len(episodes)} אפיזודות:\n"
            + "\n".join(episode_lines)
        )

        try:
            response = await self._hass.async_add_executor_job(
                lambda: _call_gemini(client, prompt)
            )
            if response and response.candidates:
                return response.candidates[0].content.parts[0].text.strip()
        except Exception as e:
            _LOGGER.debug("LLM daily summary failed, using template: %s", e)

        return _template_daily_summary(episodes, event_count)


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _call_gemini(client, prompt: str):
    """Synchronous Gemini Flash call with retry (runs in executor)."""
    from google.genai import types

    for attempt in range(2):
        try:
            return client.models.generate_content(
                model=GEMINI_MODEL_FAST,
                contents=prompt,
                config=types.GenerateContentConfig(
                    max_output_tokens=300,
                    temperature=0.3,
                ),
            )
        except Exception as e:
            if attempt == 0 and ("503" in str(e) or "429" in str(e) or "UNAVAILABLE" in str(e)):
                time.sleep(3)  # Blocking sleep OK — runs in executor thread
            else:
                raise
    return None


def _cluster_events(events: list[dict]) -> list[list[dict]]:
    """Group events by temporal proximity. Split on gap or max duration."""
    if not events:
        return []

    gap = timedelta(minutes=EPISODE_GAP_MINUTES)
    max_dur = timedelta(minutes=EPISODE_MAX_DURATION_MINUTES)

    clusters = []
    current = [events[0]]

    for event in events[1:]:
        prev_ts = current[-1]["timestamp"]
        curr_ts = event["timestamp"]
        cluster_start = current[0]["timestamp"]

        if (curr_ts - prev_ts) > gap or (curr_ts - cluster_start) > max_dur:
            clusters.append(current)
            current = [event]
        else:
            current.append(event)

    clusters.append(current)
    return clusters


def _is_mixed_domain(cluster: list[dict]) -> bool:
    """Check if cluster has events from multiple domains."""
    domains = set()
    for e in cluster:
        if e.get("event_type") == "conversation":
            domains.add("conversation")
            continue
        meta = e.get("metadata")
        if isinstance(meta, dict):
            eid = meta.get("entity_id", "")
            domain = eid.split(".")[0] if "." in eid else ""
            if domain:
                domains.add(domain)
    return len(domains) > 1


def _template_summary(cluster: list[dict]) -> dict | None:
    """Generate a template-based episode summary (no LLM call)."""
    if not cluster:
        return None
    descriptions = [e["description"] for e in cluster if e.get("description")]
    if not descriptions:
        return None

    episode_type = "activity"
    desc_text = " ".join(descriptions).lower()
    if any(w in desc_text for w in ["home", "not_home", "הביתה"]):
        episode_type = "arrival" if "home" in desc_text and "not_home" not in desc_text else "departure"
    if any(e.get("event_type") == "conversation" for e in cluster) and len(cluster) <= 3:
        episode_type = "conversation"

    first = descriptions[0]
    title = first if len(first) <= 60 else first[:57] + "..."
    summary = "; ".join(descriptions) if len(descriptions) <= 3 else f"{first}; ועוד {len(descriptions) - 1} אירועים"
    return {"title": title, "summary": summary, "episode_type": episode_type}


def _template_daily_summary(episodes: list[dict], event_count: int) -> str:
    """Generate a template-based daily summary (no LLM)."""
    if not episodes:
        return f"יום שקט — {event_count} אירועי מערכת, ללא אפיזודות משמעותיות."
    titles = ", ".join(ep.get("title", "") for ep in episodes[:5])
    return f"{event_count} אירועים, {len(episodes)} אפיזודות: {titles}"
