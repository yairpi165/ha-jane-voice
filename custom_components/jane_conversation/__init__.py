"""Jane Voice Assistant — Custom conversation agent for Home Assistant."""

import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform

from .const import DOMAIN, CONF_OPENAI_API_KEY, CONF_FIREBASE_KEY_PATH
from .memory import init_memory, rebuild_home_map

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CONVERSATION]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Jane from a config entry."""
    # Initialize memory directory
    await hass.async_add_executor_job(init_memory, hass.config.config_dir, hass)

    # Initialize Firebase backup if configured
    firebase_key = entry.data.get(CONF_FIREBASE_KEY_PATH)
    if firebase_key:
        from .firebase import init_firebase, restore_all_memory
        from .memory import get_memory_dir

        ok = await hass.async_add_executor_job(init_firebase, firebase_key)
        if ok:
            await restore_all_memory(get_memory_dir())
            _LOGGER.info("Firebase memory backup enabled")

    # Build home map on first setup (OpenAI client init is blocking)
    from openai import OpenAI

    client = await hass.async_add_executor_job(
        lambda: OpenAI(api_key=entry.data[CONF_OPENAI_API_KEY])
    )
    await hass.async_add_executor_job(rebuild_home_map, client, hass)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Listen for config updates (e.g. Tavily key added via options flow)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _LOGGER.info("Jane Voice Assistant loaded")
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle config entry updates (options flow)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Jane config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
