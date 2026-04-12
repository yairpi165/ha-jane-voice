"""Jane Voice Assistant — Custom conversation agent for Home Assistant."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_FIREBASE_KEY_PATH, CONF_GEMINI_API_KEY, CONF_PG_HOST, DOMAIN
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

    client = await hass.async_add_executor_job(
        lambda: genai.Client(api_key=entry.data[CONF_GEMINI_API_KEY])
    )
    await hass.async_add_executor_job(rebuild_home_map, client, hass)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _LOGGER.info("Jane Voice Assistant loaded (storage: %s)", "PostgreSQL" if pg_host else "files")
    return True


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
            min_size=2,
            max_size=5,
        )

        # Store pool for cleanup
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN]["_pg_pool"] = pool

        from .memory.storage import DualWriteBackend, FileBackend, PostgresBackend

        pg_backend = PostgresBackend(pool)
        file_backend = FileBackend(
            hass.config.path("jane_memory"), hass
        )
        backend = DualWriteBackend(pg_backend, file_backend)

        _LOGGER.info("PostgreSQL connected: %s:%s/%s",
                      data.get(CONF_PG_HOST), data.get(CONF_PG_PORT), data.get(CONF_PG_DATABASE))
        return backend

    except Exception as e:
        _LOGGER.error("Failed to connect to PostgreSQL, falling back to files: %s", e)
        return None


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle config entry updates (options flow)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Jane config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        domain_data = hass.data.get(DOMAIN, {})
        domain_data.pop(entry.entry_id, None)
        # Close PG pool
        pool = domain_data.pop("_pg_pool", None)
        if pool:
            await pool.close()
    return unload_ok
