"""Episodic Memory Store — events, episodes, daily summaries (S1.4).

Persists HA state changes to PostgreSQL for long-term episodic memory.
Works alongside Redis Working Memory (real-time, 1h TTL) — dual-write pattern.
"""

import json
import logging
from datetime import UTC, datetime, timedelta

_LOGGER = logging.getLogger(__name__)


class EpisodicStore:
    """Typed access to events, episodes, and daily_summaries in PostgreSQL."""

    def __init__(self, pool):
        self._pool = pool

    # ------------------------------------------------------------------
    # Events — persist state changes from Working Memory
    # ------------------------------------------------------------------

    async def persist_state_change(
        self,
        entity_id: str,
        friendly_name: str,
        old_state: str,
        new_state: str,
        timestamp: float,
    ) -> None:
        """Insert a state_change event and link it to the entity."""
        ts = datetime.fromtimestamp(timestamp, tz=UTC)
        description = f"{friendly_name}: {old_state} → {new_state}"
        metadata = json.dumps(
            {"entity_id": entity_id, "old_state": old_state, "new_state": new_state},
            ensure_ascii=False,
        )

        async with self._pool.acquire() as conn:
            event_id = await conn.fetchval(
                """INSERT INTO events (timestamp, event_type, user_name, description, metadata)
                   VALUES ($1, 'state_change', NULL, $2, $3::jsonb)
                   RETURNING id""",
                ts,
                description,
                metadata,
            )
            await conn.execute(
                """INSERT INTO event_entities (event_id, entity_id, friendly_name)
                   VALUES ($1, $2, $3)""",
                event_id,
                entity_id,
                friendly_name,
            )

    async def query_events(
        self,
        start: datetime,
        end: datetime,
        event_type: str | None = None,
        entity_id: str | None = None,
    ) -> list[dict]:
        """Query events in a time range with optional filters."""
        async with self._pool.acquire() as conn:
            if entity_id:
                rows = await conn.fetch(
                    """SELECT e.id, e.timestamp, e.event_type, e.user_name,
                              e.description, e.metadata
                       FROM events e
                       JOIN event_entities ee ON ee.event_id = e.id
                       WHERE e.timestamp >= $1 AND e.timestamp < $2
                         AND ($3::text IS NULL OR e.event_type = $3)
                         AND ee.entity_id = $4
                       ORDER BY e.timestamp""",
                    start,
                    end,
                    event_type,
                    entity_id,
                )
            else:
                rows = await conn.fetch(
                    """SELECT id, timestamp, event_type, user_name,
                              description, metadata
                       FROM events
                       WHERE timestamp >= $1 AND timestamp < $2
                         AND ($3::text IS NULL OR event_type = $3)
                       ORDER BY timestamp""",
                    start,
                    end,
                    event_type,
                )
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Episodes — consolidated event groups
    # ------------------------------------------------------------------

    async def save_episode(
        self,
        title: str,
        summary: str,
        start_ts: datetime,
        end_ts: datetime,
        episode_type: str = "activity",
        metadata: dict | None = None,
    ) -> int:
        """Insert an episode and return its id."""
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                """INSERT INTO episodes (title, summary, start_ts, end_ts, episode_type, metadata)
                   VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                   RETURNING id""",
                title,
                summary,
                start_ts,
                end_ts,
                episode_type,
                json.dumps(metadata or {}, ensure_ascii=False),
            )

    async def query_episodes(
        self,
        start: datetime,
        end: datetime,
        episode_type: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Query episodes overlapping a time range."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, title, summary, start_ts, end_ts, episode_type
                   FROM episodes
                   WHERE start_ts < $2 AND end_ts > $1
                     AND ($3::text IS NULL OR episode_type = $3)
                   ORDER BY start_ts DESC
                   LIMIT $4""",
                start,
                end,
                episode_type,
                limit,
            )
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Semantic search (S1.6 — pgvector)
    # ------------------------------------------------------------------

    async def semantic_search(self, query_embedding: list[float], limit: int = 5) -> list[dict]:
        """Find episodes most similar to query by cosine distance."""
        from .embeddings import _to_pg_vector

        vec_str = _to_pg_vector(query_embedding)
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SET LOCAL ivfflat.probes = 3")
            rows = await conn.fetch(
                """SELECT id, title, summary, start_ts, end_ts, episode_type,
                          1 - (embedding <=> $1::vector) AS similarity
                   FROM episodes
                   WHERE embedding IS NOT NULL
                   ORDER BY embedding <=> $1::vector
                   LIMIT $2""",
                vec_str,
                limit,
            )
            return [dict(r) for r in rows]

    async def semantic_search_summaries(self, query_embedding: list[float], limit: int = 3) -> list[dict]:
        """Find daily summaries most similar to query by cosine distance."""
        from .embeddings import _to_pg_vector

        vec_str = _to_pg_vector(query_embedding)
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SET LOCAL ivfflat.probes = 3")
            rows = await conn.fetch(
                """SELECT summary_date, summary, event_count, episode_count,
                          1 - (embedding <=> $1::vector) AS similarity
                   FROM daily_summaries
                   WHERE embedding IS NOT NULL
                   ORDER BY embedding <=> $1::vector
                   LIMIT $2""",
                vec_str,
                limit,
            )
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Daily Summaries
    # ------------------------------------------------------------------

    async def save_daily_summary(
        self,
        summary_date,
        summary: str,
        event_count: int = 0,
        episode_count: int = 0,
        metadata: dict | None = None,
    ) -> None:
        """Upsert a daily summary."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO daily_summaries (summary_date, summary, event_count, episode_count, metadata)
                   VALUES ($1, $2, $3, $4, $5::jsonb)
                   ON CONFLICT (summary_date) DO UPDATE SET
                       summary = EXCLUDED.summary,
                       event_count = EXCLUDED.event_count,
                       episode_count = EXCLUDED.episode_count,
                       metadata = EXCLUDED.metadata""",
                summary_date,
                summary,
                event_count,
                episode_count,
                json.dumps(metadata or {}, ensure_ascii=False),
            )

    async def get_daily_summary(self, summary_date) -> dict | None:
        """Get the daily summary for a specific date."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM daily_summaries WHERE summary_date = $1",
                summary_date,
            )
            return dict(row) if row else None

    # ------------------------------------------------------------------
    # Consolidation idempotency
    # ------------------------------------------------------------------

    async def get_last_consolidation_ts(self) -> datetime | None:
        """Get last consolidation window end from memory_entries sentinel."""
        async with self._pool.acquire() as conn:
            val = await conn.fetchval(
                """SELECT content FROM memory_entries
                   WHERE category = '_consolidation' AND user_name IS NULL""",
            )
            if val:
                return datetime.fromisoformat(val)
            return None

    async def set_last_consolidation_ts(self, ts: datetime) -> None:
        """Record last consolidation window end."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO memory_entries (category, user_name, content, updated_at)
                   VALUES ('_consolidation', NULL, $1, NOW())
                   ON CONFLICT (category, user_name)
                   DO UPDATE SET content = $1, updated_at = NOW()""",
                ts.isoformat(),
            )

    # ------------------------------------------------------------------
    # Retention cleanup
    # ------------------------------------------------------------------

    async def cleanup_old_data(
        self, event_days: int = 10, episode_days: int = 90, summary_days: int = 365
    ) -> dict[str, int]:
        """Delete old data per retention policy. Returns counts."""
        counts = {}
        async with self._pool.acquire() as conn:
            # Events (cascades to event_entities)
            r = await conn.execute(
                "DELETE FROM events WHERE timestamp < NOW() - $1::interval",
                timedelta(days=event_days),
            )
            counts["events"] = int(r.split()[-1]) if r else 0

            r = await conn.execute(
                "DELETE FROM episodes WHERE start_ts < NOW() - $1::interval",
                timedelta(days=episode_days),
            )
            counts["episodes"] = int(r.split()[-1]) if r else 0

            r = await conn.execute(
                "DELETE FROM daily_summaries WHERE summary_date < NOW() - $1::interval",
                timedelta(days=summary_days),
            )
            counts["summaries"] = int(r.split()[-1]) if r else 0

        total = sum(counts.values())
        if total:
            _LOGGER.info("Episodic cleanup: %s", counts)
        return counts
