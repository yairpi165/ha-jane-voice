"""Jane Voice Assistant — Custom conversation agent for Home Assistant."""

import importlib
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_FIREBASE_KEY_PATH,
    CONF_GEMINI_API_KEY,
    CONF_PG_HOST,
    CONF_REDIS_PASSWORD,
    CONF_REDIS_PORT,
    DEFAULT_REDIS_PORT,
    DOMAIN,
    JaneData,
)
from .memory import init_memory, rebuild_home_map

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CONVERSATION]


def _get_jane(hass: HomeAssistant) -> JaneData:
    """Get or create JaneData from hass.data."""
    hass.data.setdefault(DOMAIN, JaneData())
    return hass.data[DOMAIN]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Jane from a config entry."""
    jane = _get_jane(hass)
    jane.entry = entry

    # Determine storage backend
    backend = None
    pg_host = entry.options.get(CONF_PG_HOST) or entry.data.get(CONF_PG_HOST)

    if pg_host:
        backend = await _create_pg_backend(hass, entry)

    # Initialize Redis + Working Memory
    working_memory = None
    if pg_host:
        working_memory = await _create_working_memory(hass, entry, pg_host)

    # Initialize memory directory + backend
    await hass.async_add_executor_job(init_memory, hass.config.config_dir, hass, backend)

    # Initialize Firebase backup if configured
    firebase_key = entry.data.get(CONF_FIREBASE_KEY_PATH)
    if firebase_key:
        from .memory import get_memory_dir
        from .memory.firebase import init_firebase, restore_all_memory, sync_existing_memory

        ok = await hass.async_add_executor_job(init_firebase, firebase_key)
        if ok:
            await restore_all_memory(get_memory_dir())
            await sync_existing_memory(get_memory_dir(), hass)
            _LOGGER.info("Firebase memory backup enabled")

    # Build home map on first setup
    from google import genai

    client = await hass.async_add_executor_job(lambda: genai.Client(api_key=entry.data[CONF_GEMINI_API_KEY]))
    await hass.async_add_executor_job(rebuild_home_map, client, hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Register periodic tasks
    _register_periodic_tasks(hass, jane)

    redis_status = ", Redis working memory" if working_memory else ""
    _LOGGER.info("Jane Voice Assistant loaded (storage: %s%s)", "PostgreSQL" if pg_host else "files", redis_status)
    return True


def _register_periodic_tasks(hass: HomeAssistant, jane: JaneData) -> None:
    """Register all periodic background tasks."""
    from datetime import timedelta

    from homeassistant.helpers.event import async_track_time_interval

    # S1.3: Daily preference decay
    if jane.structured:
        async def _decay_task(_now):
            try:
                count = await jane.structured.decay_preferences()
                if count:
                    _LOGGER.info("Preference decay: %d preferences updated", count)
            except Exception as e:
                _LOGGER.debug("Preference decay failed: %s", e)

        jane.add_unsub(async_track_time_interval(hass, _decay_task, timedelta(hours=24)))

    # S1.4: Consolidation + daily summary + cleanup
    if jane.episodic:
        from .memory.consolidation import ConsolidationWorker

        worker = ConsolidationWorker(jane.episodic, hass)
        jane.consolidation = worker  # Set here (not in _create_pg_backend) — needs episodic + hass

        async def _consolidation_task(_now):
            try:
                count = await worker.consolidate_events()
                if count:
                    _LOGGER.info("Consolidation: %d episodes created", count)
            except Exception as e:
                _LOGGER.debug("Consolidation failed: %s", e)

        async def _daily_summary_task(_now):
            try:
                if await worker.generate_daily_summary():
                    _LOGGER.info("Daily summary created")
            except Exception as e:
                _LOGGER.debug("Daily summary failed: %s", e)

        async def _cleanup_task(_now):
            try:
                counts = await jane.episodic.cleanup_old_data()
                if any(counts.values()):
                    _LOGGER.info("Episodic cleanup: %s", counts)
            except Exception as e:
                _LOGGER.debug("Episodic cleanup failed: %s", e)

        jane.add_unsub(async_track_time_interval(hass, _consolidation_task, timedelta(hours=6)))
        jane.add_unsub(async_track_time_interval(hass, _daily_summary_task, timedelta(hours=24)))
        jane.add_unsub(async_track_time_interval(hass, _cleanup_task, timedelta(hours=24)))


async def _create_working_memory(hass: HomeAssistant, entry: ConfigEntry, pg_host: str):
    """Create Redis client and start Working Memory listener."""
    try:
        aioredis = await hass.async_add_executor_job(importlib.import_module, "redis.asyncio")
        jane = _get_jane(hass)

        data = {**entry.data, **entry.options}
        redis_port = int(data.get(CONF_REDIS_PORT, DEFAULT_REDIS_PORT))
        redis_password = data.get(CONF_REDIS_PASSWORD, "") or None

        client = aioredis.Redis(
            host=pg_host,
            port=redis_port,
            password=redis_password,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        await client.ping()

        from .brain.working_memory import WorkingMemory

        wm = WorkingMemory(client, hass, episodic=jane.episodic)
        unsub = await wm.start_listening()

        jane.redis = client
        jane.working_memory = wm
        jane.add_unsub(unsub)

        _LOGGER.info("Redis connected: %s:%s", pg_host, redis_port)
        return wm

    except Exception as e:
        _LOGGER.warning("Redis unavailable, working memory disabled: %s", e)
        return None


async def _create_pg_backend(hass: HomeAssistant, entry: ConfigEntry):
    """Create PostgreSQL storage backend with connection pool."""
    from .const import CONF_PG_DATABASE, CONF_PG_PASSWORD, CONF_PG_PORT, CONF_PG_USER

    try:
        import asyncpg

        data = {**entry.data, **entry.options}
        pool = await asyncpg.create_pool(
            host=data.get(CONF_PG_HOST, "localhost"),
            port=int(data.get(CONF_PG_PORT, 5432)),
            database=data.get(CONF_PG_DATABASE, "jane"),
            user=data.get(CONF_PG_USER, "postgres"),
            password=data.get(CONF_PG_PASSWORD, ""),
            ssl="disable",
            min_size=2,
            max_size=5,
        )

        jane = _get_jane(hass)
        jane.pg_pool = pool

        from .memory.storage import DualWriteBackend, FileBackend, PostgresBackend

        pg_backend = PostgresBackend(pool)
        file_backend = FileBackend(hass.config.path("jane_memory"), hass)
        backend = DualWriteBackend(pg_backend, file_backend)

        _LOGGER.info(
            "PostgreSQL connected: %s:%s/%s", data.get(CONF_PG_HOST), data.get(CONF_PG_PORT), data.get(CONF_PG_DATABASE)
        )

        # Auto-migrate MD files on first PG connect
        await _auto_migrate(pool, hass)

        # Initialize stores
        from .memory.episodic import EpisodicStore
        from .memory.structured import StructuredMemoryStore

        jane.structured = StructuredMemoryStore(pool)
        jane.episodic = EpisodicStore(pool)

        # Initialize routine store (S1.5)
        from .memory.routine_store import RoutineStore

        jane.routines = RoutineStore(pool)

        # Initialize policy store (S1.5)
        from .memory.policy import PolicyStore

        jane.policies = PolicyStore(pool)

        # Seed default policies for existing persons
        if jane.structured:
            try:
                persons = await jane.structured.load_persons()
                if persons:
                    seeded = await jane.policies.seed_defaults(persons)
                    if seeded:
                        _LOGGER.info("Seeded default policies for %d persons", seeded)
            except Exception as e:
                _LOGGER.debug("Policy seeding skipped: %s", e)

        # Auto-migrate MD → structured tables on first connect
        from pathlib import Path as _Path

        from .memory.migrate_structured import migrate_to_structured

        memory_dir = _Path(hass.config.config_dir) / "jane_memory"
        file_data = await hass.async_add_executor_job(_read_migration_files, memory_dir)
        if file_data:
            await migrate_to_structured(jane.structured, file_data)

        return backend

    except Exception as e:
        _LOGGER.error("Failed to connect to PostgreSQL, falling back to files: %s", e)
        return None


async def _auto_migrate(pool, hass: HomeAssistant) -> None:
    """Auto-migrate permanent MD memory files to PostgreSQL on first connect."""
    from pathlib import Path

    try:
        async with pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM memory_entries WHERE content != ''")
            if count > 0:
                _LOGGER.info("PG already has %d memory entries, skipping migration", count)
                return

        memory_dir = Path(hass.config.config_dir) / "jane_memory"
        if not memory_dir.exists():
            _LOGGER.info("No jane_memory directory found, skipping migration")
            return

        migrated = 0
        categories = {
            "family": memory_dir / "family.md",
            "habits": memory_dir / "habits.md",
            "corrections": memory_dir / "corrections.md",
            "routines": memory_dir / "routines.md",
            "home": memory_dir / "home.md",
        }

        async with pool.acquire() as conn:
            for category, path in categories.items():
                content = await hass.async_add_executor_job(
                    lambda p: p.read_text(encoding="utf-8").strip() if p.exists() else "", path
                )
                if content:
                    await conn.execute(
                        """INSERT INTO memory_entries (category, user_name, content, updated_at)
                           VALUES ($1, NULL, $2, NOW())
                           ON CONFLICT (category, user_name)
                           DO UPDATE SET content = $2, updated_at = NOW()""",
                        category,
                        content,
                    )
                    migrated += 1

            users_dir = memory_dir / "users"
            user_files = await hass.async_add_executor_job(
                lambda: list(users_dir.glob("*.md")) if users_dir.exists() else []
            )
            for user_file in user_files:
                content = await hass.async_add_executor_job(lambda p: p.read_text(encoding="utf-8").strip(), user_file)
                if content:
                    await conn.execute(
                        """INSERT INTO memory_entries (category, user_name, content, updated_at)
                           VALUES ('user', $1, $2, NOW())
                           ON CONFLICT (category, user_name)
                           DO UPDATE SET content = $2, updated_at = NOW()""",
                        user_file.stem,
                        content,
                    )
                    migrated += 1

        _LOGGER.info("Auto-migrated %d memory entries from MD files to PostgreSQL", migrated)

    except Exception as e:
        _LOGGER.warning("Auto-migration failed (non-fatal, files still work): %s", e)


def _read_migration_files(memory_dir) -> dict | None:
    """Read MD files for structured migration (sync, runs in executor)."""
    result = {}
    family = memory_dir / "family.md"
    if family.exists():
        result["family"] = family.read_text(encoding="utf-8")
    users = memory_dir / "users"
    if users.exists():
        result["users"] = {f.stem: f.read_text(encoding="utf-8") for f in users.glob("*.md")}
    return result or None


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle config entry updates (options flow)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Jane config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        jane = hass.data.pop(DOMAIN, None)
        if jane and isinstance(jane, JaneData):
            jane.cancel_all()
            if jane.redis:
                await jane.redis.aclose()
            if jane.pg_pool:
                await jane.pg_pool.close()
    return unload_ok
