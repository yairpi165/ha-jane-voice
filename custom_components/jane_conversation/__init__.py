"""Jane Voice Assistant — Custom conversation agent for Home Assistant."""

import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform

from .const import DOMAIN
from .memory import init_memory, rebuild_home_map
from .const import CONF_OPENAI_API_KEY

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CONVERSATION]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Jane from a config entry."""
    # Initialize memory directory
    await hass.async_add_executor_job(init_memory, hass.config.config_dir)

    # Build home map on first setup
    from openai import OpenAI

    client = OpenAI(api_key=entry.data[CONF_OPENAI_API_KEY])
    await hass.async_add_executor_job(rebuild_home_map, client, hass)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.info("Jane Voice Assistant loaded")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Jane config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
