"""Jane Voice Assistant — Custom conversation agent for Home Assistant."""

import logging

import redis.asyncio as aioredis
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
)
from .memory import init_memory, rebuild_home_map

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CONVERSATION]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Jane from a config entry."""
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

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    redis_status = ", Redis working memory" if working_memory else ""
    _LOGGER.info("Jane Voice Assistant loaded (storage: %s%s)", "PostgreSQL" if pg_host else "files", redis_status)
    return True


async def _create_working_memory(hass: HomeAssistant, entry: ConfigEntry, pg_host: str):
    """Create Redis client and start Working Memory listener."""
    try:
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

        wm = WorkingMemory(client, hass)
        unsub = await wm.start_listening()

        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN]["_redis"] = client
        hass.data[DOMAIN]["_working_memory"] = wm
        hass.data[DOMAIN]["_redis_unsub"] = unsub

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

        # Store pool for cleanup
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN]["_pg_pool"] = pool

        from .memory.storage import DualWriteBackend, FileBackend, PostgresBackend

        pg_backend = PostgresBackend(pool)
        file_backend = FileBackend(hass.config.path("jane_memory"), hass)
        backend = DualWriteBackend(pg_backend, file_backend)

        _LOGGER.info(
            "PostgreSQL connected: %s:%s/%s", data.get(CONF_PG_HOST), data.get(CONF_PG_PORT), data.get(CONF_PG_DATABASE)
        )

        # Auto-migrate MD files on first PG connect
        await _auto_migrate(pool, hass)

        return backend

    except Exception as e:
        _LOGGER.error("Failed to connect to PostgreSQL, falling back to files: %s", e)
        return None


async def _auto_migrate(pool, hass: HomeAssistant) -> None:
    """Auto-migrate permanent MD memory files to PostgreSQL on first connect.

    Migrates: family, habits, corrections, routines, home, user/* files.
    Does NOT migrate: actions.md (rolling 24h) or history.log (append-only).
    For full historical migration, use scripts/migrate_md_to_pg.py.
    """
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

            # Migrate user files
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


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle config entry updates (options flow)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Jane config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        domain_data = hass.data.get(DOMAIN, {})
        domain_data.pop(entry.entry_id, None)
        # Stop working memory listener
        redis_unsub = domain_data.pop("_redis_unsub", None)
        if redis_unsub:
            redis_unsub()
        domain_data.pop("_working_memory", None)
        # Close Redis client
        redis_client = domain_data.pop("_redis", None)
        if redis_client:
            await redis_client.aclose()
        # Close PG pool
        pool = domain_data.pop("_pg_pool", None)
        if pool:
            await pool.close()
    return unload_ok
