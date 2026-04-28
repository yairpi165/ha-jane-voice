"""Routine Store — structured Smart Routines in PostgreSQL (S1.5).

Replaces routines.md with a queryable table. Supports occurrence tracking,
confidence scores, and substring-based trigger matching.
"""

import json
import logging

_LOGGER = logging.getLogger(__name__)


class RoutineStore:
    """Typed access to the routines table in PostgreSQL."""

    def __init__(self, pool):
        self._pool = pool

    async def save_routine(
        self,
        name: str,
        trigger_phrase: str,
        steps: list[dict],
        script_id: str | None = None,
        confidence: float = 1.0,
    ) -> None:
        """Upsert a routine. Updates steps and bumps occurrence on conflict."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO routines (name, trigger_phrase, steps, script_id, confidence)
                   VALUES ($1, $2, $3::jsonb, $4, $5)
                   ON CONFLICT (name) DO UPDATE SET
                       trigger_phrase = EXCLUDED.trigger_phrase,
                       steps = EXCLUDED.steps,
                       script_id = COALESCE(EXCLUDED.script_id, routines.script_id),
                       confidence = GREATEST(routines.confidence, EXCLUDED.confidence),
                       updated_at = NOW()""",
                name,
                trigger_phrase,
                json.dumps(steps, ensure_ascii=False),
                script_id,
                confidence,
            )

    async def load_routines(self) -> list[dict]:
        """Load all routines ordered by usage."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT name, trigger_phrase, steps, script_id,
                          confidence, occurrence_count, last_used
                   FROM routines
                   ORDER BY occurrence_count DESC, last_used DESC"""
            )
            return [dict(r) for r in rows]

    async def find_routine(self, trigger_phrase: str) -> dict | None:
        """Match using substring containment (case-insensitive).

        Returns first routine where the input contains the stored trigger
        or vice versa. Semantic matching deferred to S1.6 (pgvector).
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT name, trigger_phrase, steps, script_id,
                          confidence, occurrence_count
                   FROM routines
                   WHERE LOWER($1) LIKE '%%' || LOWER(trigger_phrase) || '%%'
                      OR LOWER(trigger_phrase) LIKE '%%' || LOWER($1) || '%%'
                   ORDER BY occurrence_count DESC
                   LIMIT 1""",
                trigger_phrase,
            )
            return dict(row) if row else None

    async def increment_occurrence(self, name: str) -> None:
        """Bump occurrence count and last_used timestamp."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """UPDATE routines
                   SET occurrence_count = occurrence_count + 1,
                       last_used = NOW(),
                       updated_at = NOW()
                   WHERE name = $1""",
                name,
            )

    async def get_top_routines(self, limit: int = 10) -> list[dict]:
        """Get most-used routines for context injection."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT name, trigger_phrase, occurrence_count
                   FROM routines
                   ORDER BY occurrence_count DESC
                   LIMIT $1""",
                limit,
            )
            return [dict(r) for r in rows]

    async def load_routines_for_context(self) -> str:
        """Format top routines as concise text for Gemini system_instruction."""
        routines = await self.get_top_routines(10)
        if not routines:
            return ""
        lines = []
        for r in routines:
            lines.append(f'- {r["name"]} (trigger: "{r["trigger_phrase"]}", used {r["occurrence_count"]}x)')
        return "\n".join(lines)
