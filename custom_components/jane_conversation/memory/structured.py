"""Structured Memory Store — persons, preferences (S1.3).

Separate from StorageBackend: different access patterns (query by person,
list all preferences) don't fit the load(category)/save(category) interface.
"""

import logging

_LOGGER = logging.getLogger(__name__)


class StructuredMemoryStore:
    """Typed access to persons and preferences tables in PostgreSQL."""

    def __init__(self, pool):
        self._pool = pool

    # ------------------------------------------------------------------
    # Preferences
    # ------------------------------------------------------------------

    async def save_preference(
        self,
        person_name: str,
        key: str,
        value: str,
        inferred: bool = False,
        confidence: float | None = None,
        source: str = "extraction",
    ) -> None:
        """Upsert a preference. Explicit confirms inferred (confidence → 1.0)."""
        if confidence is None:
            confidence = 0.7 if inferred else 1.0

        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO preferences (person_name, key, value, confidence, inferred, source, last_reinforced)
                   VALUES ($1, $2, $3, $4, $5, $6, NOW())
                   ON CONFLICT (person_name, key) DO UPDATE SET
                       value = EXCLUDED.value,
                       confidence = CASE
                           WHEN preferences.inferred AND NOT EXCLUDED.inferred THEN 1.0
                           ELSE GREATEST(preferences.confidence, EXCLUDED.confidence)
                       END,
                       inferred = EXCLUDED.inferred,
                       last_reinforced = NOW(),
                       updated_at = NOW(),
                       deleted_at = NULL""",
                person_name,
                key,
                value,
                confidence,
                inferred,
                source,
            )

    async def load_preferences(self, person_name: str, min_confidence: float = 0.5) -> list[dict]:
        """Load preferences for a person, ordered by confidence DESC."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT key, value, confidence, inferred
                   FROM preferences
                   WHERE person_name = $1 AND confidence >= $2
                     AND deleted_at IS NULL
                   ORDER BY inferred ASC, confidence DESC""",
                person_name,
                min_confidence,
            )
            return [dict(r) for r in rows]

    async def load_all_preferences(self, min_confidence: float = 0.5) -> dict[str, list[dict]]:
        """Load all preferences grouped by person_name."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT person_name, key, value, confidence, inferred
                   FROM preferences
                   WHERE confidence >= $1
                     AND deleted_at IS NULL
                   ORDER BY person_name, inferred ASC, confidence DESC""",
                min_confidence,
            )
        result: dict[str, list[dict]] = {}
        for r in rows:
            name = r["person_name"]
            result.setdefault(name, []).append(dict(r))
        return result

    async def load_preference(self, person_name: str, key: str) -> dict | None:
        """Load a single preference row by (person_name, key), or None if missing."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT key, value, confidence, inferred, source
                   FROM preferences
                   WHERE person_name = $1 AND key = $2
                     AND deleted_at IS NULL""",
                person_name,
                key,
            )
            return dict(row) if row else None

    async def delete_preference(self, person_name: str, key: str) -> dict | None:
        """Soft-delete a preference and return the pre-delete row (for before_state).

        A4: sets deleted_at = NOW(); double-delete is a no-op (guarded by deleted_at IS NULL).
        Re-saving the same (person_name, key) revives the row via save_preference's ON CONFLICT.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """UPDATE preferences
                      SET deleted_at = NOW()
                   WHERE person_name = $1 AND key = $2
                     AND deleted_at IS NULL
                   RETURNING key, value, confidence, inferred, source""",
                person_name,
                key,
            )
            return dict(row) if row else None

    async def reinforce_preference(self, person_name: str, key: str) -> None:
        """Reset confidence and last_reinforced for a preference."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """UPDATE preferences
                   SET confidence = 1.0, last_reinforced = NOW(), updated_at = NOW()
                   WHERE person_name = $1 AND key = $2
                     AND deleted_at IS NULL""",
                person_name,
                key,
            )

    async def decay_preferences(self) -> int:
        """Decay inferred preferences not reinforced in 7+ days. Subtract 0.05 per run (daily)."""
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """UPDATE preferences
                   SET confidence = GREATEST(0.0, confidence - 0.05),
                       updated_at = NOW()
                   WHERE inferred = TRUE
                     AND confidence > 0.0
                     AND last_reinforced < NOW() - INTERVAL '7 days'
                     AND deleted_at IS NULL"""
            )
            # asyncpg returns "UPDATE N"
            count = int(result.split()[-1]) if result else 0
            if count:
                _LOGGER.info("Decayed %d inferred preferences", count)
            return count

    # ------------------------------------------------------------------
    # Persons
    # ------------------------------------------------------------------

    async def save_person(
        self,
        name: str,
        role: str | None = None,
        birth_date=None,
        metadata: dict | None = None,
    ) -> None:
        """Upsert a person.

        Note: metadata merge uses jsonb || (shallow merge).
        Calling save_person with overlapping keys will overwrite, not append.
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO persons (name, role, birth_date, metadata)
                   VALUES ($1, $2, $3, $4::jsonb)
                   ON CONFLICT (name) DO UPDATE SET
                       role = COALESCE(EXCLUDED.role, persons.role),
                       birth_date = COALESCE(EXCLUDED.birth_date, persons.birth_date),
                       metadata = persons.metadata || EXCLUDED.metadata,
                       updated_at = NOW()""",
                name,
                role,
                birth_date,
                _json_dumps(metadata) if metadata else "{}",
            )

    async def load_persons(self) -> list[dict]:
        """Load all persons."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT name, role, birth_date, metadata FROM persons ORDER BY id")
            return [dict(r) for r in rows]

    async def load_person(self, name: str) -> dict | None:
        """Load a single person row by name, or None if missing."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT name, role, birth_date, metadata FROM persons WHERE name = $1",
                name,
            )
            return dict(row) if row else None

    # ------------------------------------------------------------------
    # Relationships (populated in Phase E, not used before)
    # ------------------------------------------------------------------

    async def save_relationship(self, person_a: str, person_b: str, relation: str) -> None:
        """Save a relationship between two persons (creates persons if needed)."""
        async with self._pool.acquire() as conn:
            # Ensure both persons exist
            for name in (person_a, person_b):
                await conn.execute(
                    "INSERT INTO persons (name) VALUES ($1) ON CONFLICT (name) DO NOTHING",
                    name,
                )
            await conn.execute(
                """INSERT INTO relationships (person_a_id, person_b_id, relation)
                   SELECT a.id, b.id, $3
                   FROM persons a, persons b
                   WHERE a.name = $1 AND b.name = $2
                   ON CONFLICT (person_a_id, person_b_id, relation) DO NOTHING""",
                person_a,
                person_b,
                relation,
            )


def _json_dumps(obj: dict | None) -> str:
    """Safe JSON serialization for JSONB fields."""
    import json

    return json.dumps(obj or {}, ensure_ascii=False)
