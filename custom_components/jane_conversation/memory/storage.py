"""Storage backend abstraction for Jane memory system.

Three implementations:
- FileBackend: current MD files (default, always available)
- PostgresBackend: PostgreSQL via asyncpg
- DualWriteBackend: writes to both, reads from PG with file fallback
"""

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from pathlib import Path

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


class FileBackend(StorageBackend):
    """Current MD file implementation — always available as fallback."""

    def __init__(self, memory_dir: Path, hass=None):
        self._dir = memory_dir
        self._hass = hass
        self._recent_responses: list[str] = []

    def _read(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return ""

    def _write(self, path: Path, content: str, firebase_doc: str | None = None):
        import asyncio

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)

        if firebase_doc and self._hass:
            asyncio.run_coroutine_threadsafe(
                self._firebase_backup(firebase_doc, content), self._hass.loop
            )

    async def _firebase_backup(self, doc_name: str, content: str):
        try:
            from .firebase import backup_memory

            await backup_memory(doc_name, content)
        except Exception as e:
            _LOGGER.warning("Firebase backup failed for %s: %s", doc_name, e)

    def _category_path(self, category: str, user_name: str | None = None) -> Path:
        if category == "user" and user_name:
            return self._dir / "users" / f"{user_name.lower().strip()}.md"
        return self._dir / f"{category}.md"

    def _firebase_doc(self, category: str, user_name: str | None = None) -> str | None:
        if category in ("actions", "home"):
            return None
        if category == "user" and user_name:
            return f"users_{user_name.lower().strip()}"
        return category

    async def load(self, category: str, user_name: str | None = None) -> str:
        return self._read(self._category_path(category, user_name))

    async def save(self, category: str, content: str, user_name: str | None = None) -> None:
        path = self._category_path(category, user_name)
        doc = self._firebase_doc(category, user_name)
        self._write(path, content, doc)

    async def append_event(
        self, event_type: str, user_name: str, description: str, metadata: dict | None = None
    ) -> None:
        if event_type == "action":
            path = self._dir / "actions.md"
            now = datetime.now()
            new_line = f"- {now.strftime('%Y-%m-%d %H:%M')} — {description} ({user_name})"

            lines = []
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.startswith("- "):
                        try:
                            ts_str = line.split(" — ")[0].replace("- ", "")
                            ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M")
                            if now - ts < timedelta(hours=24):
                                lines.append(line)
                        except (ValueError, IndexError):
                            lines.append(line)
                    elif line.startswith("#"):
                        continue

            lines.append(new_line)
            content = "# Recent Actions (rolling 24h)\n\n" + "\n".join(lines) + "\n"
            self._write(path, content)

        elif event_type == "conversation":
            path = self._dir / "history.log"
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            user_text = metadata.get("user_text", "") if metadata else ""
            response_text = metadata.get("response_text", "") if metadata else ""
            entry = f"[{now}] {user_name}: {user_text}\n[{now}] Jane: {response_text}\n\n"
            with open(path, "a", encoding="utf-8") as f:
                f.write(entry)

    async def get_recent_responses(self, limit: int = 10) -> list[str]:
        if not self._recent_responses:
            return []
        return self._recent_responses[-limit:]

    async def track_response(self, opening: str) -> None:
        if not opening:
            return
        self._recent_responses.append(opening.strip()[:60])
        if len(self._recent_responses) > 20:
            self._recent_responses.pop(0)

    async def load_all(self, user_name: str) -> str:
        categories = {
            "Personal Memory": ("user", user_name),
            "Family Memory": ("family", None),
            "Behavioral Patterns": ("habits", None),
            "Recent Actions (24h)": ("actions", None),
            "Home Layout": ("home", None),
            "Corrections & Learnings": ("corrections", None),
            "Routines": ("routines", None),
        }
        parts = []
        for title, (cat, uname) in categories.items():
            content = await self.load(cat, uname)
            parts.append(f"## {title}")
            parts.append(content if content else "No data yet.")
            parts.append("")
        return "\n".join(parts)


class PostgresBackend(StorageBackend):
    """PostgreSQL implementation via asyncpg."""

    def __init__(self, pool):
        self._pool = pool

    async def load(self, category: str, user_name: str | None = None) -> str:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT content FROM memory_entries WHERE category = $1 AND "
                "(user_name = $2 OR (user_name IS NULL AND $2 IS NULL))",
                category, user_name,
            )
            return row["content"] if row else ""

    async def save(self, category: str, content: str, user_name: str | None = None) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO memory_entries (category, user_name, content, updated_at)
                   VALUES ($1, $2, $3, NOW())
                   ON CONFLICT (category, user_name)
                   DO UPDATE SET content = $3, updated_at = NOW()""",
                category, user_name, content,
            )

    async def append_event(
        self, event_type: str, user_name: str, description: str, metadata: dict | None = None
    ) -> None:
        import json

        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO events (event_type, user_name, description, metadata)
                   VALUES ($1, $2, $3, $4::jsonb)""",
                event_type, user_name, description,
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


class DualWriteBackend(StorageBackend):
    """Transition backend: writes to both PG and files, reads from PG with file fallback."""

    def __init__(self, pg_backend: PostgresBackend, file_backend: FileBackend):
        self._pg = pg_backend
        self._file = file_backend

    async def load(self, category: str, user_name: str | None = None) -> str:
        try:
            content = await self._pg.load(category, user_name)
            if content:
                return content
        except Exception as e:
            _LOGGER.warning("PG load failed for %s, falling back to file: %s", category, e)
        return await self._file.load(category, user_name)

    async def save(self, category: str, content: str, user_name: str | None = None) -> None:
        # Always write to files (safety net)
        await self._file.save(category, content, user_name)
        # Also write to PG
        try:
            await self._pg.save(category, content, user_name)
        except Exception as e:
            _LOGGER.warning("PG save failed for %s, file write succeeded: %s", category, e)

    async def append_event(
        self, event_type: str, user_name: str, description: str, metadata: dict | None = None
    ) -> None:
        await self._file.append_event(event_type, user_name, description, metadata)
        try:
            await self._pg.append_event(event_type, user_name, description, metadata)
        except Exception as e:
            _LOGGER.warning("PG append_event failed, file write succeeded: %s", e)

    async def get_recent_responses(self, limit: int = 10) -> list[str]:
        try:
            return await self._pg.get_recent_responses(limit)
        except Exception as e:
            _LOGGER.warning("PG get_recent_responses failed, using file: %s", e)
            return await self._file.get_recent_responses(limit)

    async def track_response(self, opening: str) -> None:
        await self._file.track_response(opening)
        try:
            await self._pg.track_response(opening)
        except Exception as e:
            _LOGGER.warning("PG track_response failed: %s", e)

    async def load_all(self, user_name: str) -> str:
        try:
            content = await self._pg.load_all(user_name)
            if content and "No data yet." not in content[:100]:
                return content
        except Exception as e:
            _LOGGER.warning("PG load_all failed, falling back to file: %s", e)
        return await self._file.load_all(user_name)
