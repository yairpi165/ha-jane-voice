"""Storage backend abstraction for Jane memory system.

PostgresBackend is the sole implementation. MD files removed in PR B.
"""

import json
import logging
from abc import ABC, abstractmethod

_LOGGER = logging.getLogger(__name__)


class StorageBackend(ABC):
    """Abstract interface for memory storage."""

    @abstractmethod
    async def load(self, category: str, user_name: str | None = None) -> str:
        """Load memory content by category."""

    @abstractmethod
    async def save(self, category: str, content: str, user_name: str | None = None) -> None:
        """Save memory content by category."""

    @abstractmethod
    async def append_event(
        self, event_type: str, user_name: str, description: str, metadata: dict | None = None
    ) -> None:
        """Append an event to the audit trail."""

    @abstractmethod
    async def get_recent_responses(self, limit: int = 10) -> list[str]:
        """Get recent response openings for anti-repetition."""

    @abstractmethod
    async def track_response(self, opening: str) -> None:
        """Track a response opening."""

    @abstractmethod
    async def load_all(self, user_name: str) -> str:
        """Load all memory categories as formatted context string."""

    @abstractmethod
    async def load_snapshot(self, user_name: str) -> dict:
        """Return all memory categories as a {category: content} dict — used by ops extractor."""

    @abstractmethod
    async def delete_category(self, category: str, user_name: str | None = None) -> str | None:
        """Delete a memory_entries row and return its prior content (for before_state)."""


class PostgresBackend(StorageBackend):
    """PostgreSQL implementation via asyncpg."""

    def __init__(self, pool):
        self._pool = pool

    async def load(self, category: str, user_name: str | None = None) -> str:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT content FROM memory_entries WHERE category = $1 AND "
                "(user_name = $2 OR (user_name IS NULL AND $2 IS NULL))",
                category,
                user_name,
            )
            return row["content"] if row else ""

    async def save(
        self,
        category: str,
        content: str,
        user_name: str | None = None,
        conn=None,
    ) -> None:
        """Upsert a memory_entries row. `conn` lets the caller participate in an open tx."""
        sql = """INSERT INTO memory_entries (category, user_name, content, updated_at)
                   VALUES ($1, $2, $3, NOW())
                   ON CONFLICT (category, user_name)
                   DO UPDATE SET content = $3, updated_at = NOW()"""
        if conn is not None:
            await conn.execute(sql, category, user_name, content)
            return
        async with self._pool.acquire() as c:
            await c.execute(sql, category, user_name, content)

    async def delete_category(self, category: str, user_name: str | None = None) -> str | None:
        """Delete by (category, user_name) and return prior content, or None if not present."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """DELETE FROM memory_entries
                   WHERE category = $1
                     AND (user_name = $2 OR (user_name IS NULL AND $2 IS NULL))
                   RETURNING content""",
                category,
                user_name,
            )
            return row["content"] if row else None

    async def append_event(
        self, event_type: str, user_name: str, description: str, metadata: dict | None = None
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO events (event_type, user_name, description, metadata)
                   VALUES ($1, $2, $3, $4::jsonb)""",
                event_type,
                user_name,
                description,
                json.dumps(metadata) if metadata else "{}",
            )

    async def get_recent_responses(self, limit: int = 10) -> list[str]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT opening FROM response_tracking ORDER BY created_at DESC LIMIT $1",
                limit,
            )
            return [row["opening"] for row in reversed(rows)]

    async def track_response(self, opening: str) -> None:
        if not opening:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO response_tracking (opening) VALUES ($1)",
                opening.strip()[:60],
            )
            # Keep only last 50 entries
            await conn.execute(
                """DELETE FROM response_tracking
                   WHERE id NOT IN (
                       SELECT id FROM response_tracking ORDER BY created_at DESC LIMIT 50
                   )""",
            )

    async def load_all(self, user_name: str) -> str:
        categories = [
            ("Personal Memory", "user", user_name),
            ("Family Memory", "family", None),
            ("Behavioral Patterns", "habits", None),
            ("Recent Actions (24h)", "actions", None),
            ("Home Layout", "home", None),
            ("Corrections & Learnings", "corrections", None),
            ("Routines", "routines", None),
        ]
        parts = []
        for title, cat, uname in categories:
            content = await self.load(cat, uname)
            parts.append(f"## {title}")
            parts.append(content if content else "No data yet.")
            parts.append("")
        return "\n".join(parts)

    async def load_snapshot(self, user_name: str) -> dict:
        """Return {category: content} dict. Used by op extractor for prompt + before_state reuse."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT category, user_name, content FROM memory_entries
                   WHERE user_name = $1 OR user_name IS NULL""",
                user_name,
            )
        snapshot: dict = {}
        for r in rows:
            # user-scoped rows win over NULL rows for the same category if both exist.
            key = r["category"]
            if r["user_name"] == user_name or key not in snapshot:
                snapshot[key] = r["content"]
        return snapshot
