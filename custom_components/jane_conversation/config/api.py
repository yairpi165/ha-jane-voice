"""Config Store API — HA REST API client for automations, scripts, scenes.

Uses the same REST API as HA's MCP server and UI:
  POST   /api/config/{resource}/config/{id}  -> create/update
  GET    /api/config/{resource}/config/{id}  -> read
  DELETE /api/config/{resource}/config/{id}  -> delete

No direct YAML file manipulation — HA handles all serialization via .storage/.
"""

import asyncio
import logging
import time
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..const import DOMAIN
from .normalize import (
    normalize_config_for_roundtrip,
    normalize_config_keys,
    strip_empty_config_fields,
)

_LOGGER = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """Convert text to an ASCII-only slug for script IDs."""
    from .normalize import _slugify as _do_slugify

    return _do_slugify(text)


# ---------------------------------------------------------------------------
# Auth — internal Long-Lived Access Token
# ---------------------------------------------------------------------------

async def _get_api_token(hass: HomeAssistant) -> str:
    """Get or create an internal API access token for Config Store calls."""
    domain_data = hass.data.get(DOMAIN, {})

    refresh_token = domain_data.get("_api_refresh_token")
    if refresh_token is not None:
        return hass.auth.async_create_access_token(refresh_token)

    owner = await hass.auth.async_get_owner()
    if owner is None:
        raise RuntimeError("No owner user found in Home Assistant")

    for rt in owner.refresh_tokens.values():
        if rt.client_name == "Jane Internal API":
            domain_data["_api_refresh_token"] = rt
            return hass.auth.async_create_access_token(rt)

    refresh_token = await hass.auth.async_create_refresh_token(
        owner,
        client_name="Jane Internal API",
        token_type="long_lived_access_token",
        access_token_expiration=timedelta(days=3650),
    )
    domain_data["_api_refresh_token"] = refresh_token
    return hass.auth.async_create_access_token(refresh_token)


# ---------------------------------------------------------------------------
# HTTP — authenticated requests to HA's Config Store REST API
# ---------------------------------------------------------------------------

async def ha_config_request(
    hass: HomeAssistant,
    method: str,
    path: str,
    json_data: dict | None = None,
) -> dict:
    """Make an authenticated request to HA's Config Store REST API."""
    token = await _get_api_token(hass)
    session = async_get_clientsession(hass)

    port = getattr(hass.http, "server_port", 8123)
    url = f"http://127.0.0.1:{port}/api{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    kwargs: dict = {"headers": headers}
    if json_data is not None:
        kwargs["json"] = json_data

    _LOGGER.debug("Config API %s %s", method, path)
    async with session.request(method, url, **kwargs) as resp:
        if resp.status >= 400:
            text = await resp.text()
            _LOGGER.error("Config API error %s %s: %s %s", method, path, resp.status, text)
            raise RuntimeError(f"HA Config API error {resp.status}: {text}")
        try:
            return await resp.json(content_type=None)
        except Exception:
            return {}


# ---------------------------------------------------------------------------
# Resolve — entity_id -> unique_id
# ---------------------------------------------------------------------------

async def resolve_config_id(
    hass: HomeAssistant, resource: str, identifier: str
) -> str:
    """Convert entity_id to unique_id/storage_key if needed."""
    if not identifier.startswith(f"{resource}."):
        return identifier

    if resource == "script":
        try:
            from homeassistant.helpers import entity_registry as er

            ent_reg = er.async_get(hass)
            entry = ent_reg.async_get(identifier)
            if entry and entry.unique_id and isinstance(entry.unique_id, str):
                _LOGGER.debug("Resolved %s -> storage key %s", identifier, entry.unique_id)
                return entry.unique_id
        except Exception:
            pass
        bare_id = identifier.removeprefix("script.")
        _LOGGER.debug("Resolved %s -> bare id %s", identifier, bare_id)
        return bare_id
    else:
        state = hass.states.get(identifier)
        if state is None:
            raise RuntimeError(f"Entity {identifier} not found")
        unique_id = state.attributes.get("id")
        if not unique_id:
            raise RuntimeError(f"Entity {identifier} has no unique_id attribute")
        _LOGGER.debug("Resolved %s -> unique_id %s", identifier, unique_id)
        return str(unique_id)


# ---------------------------------------------------------------------------
# Poll — verify entity was created
# ---------------------------------------------------------------------------

async def poll_for_entity(
    hass: HomeAssistant, resource: str, unique_id: str
) -> str | None:
    """Poll HA states to find the entity_id assigned to a newly created item."""
    for attempt in range(3):
        await asyncio.sleep(1 * (attempt + 1))
        states = hass.states.async_all(resource)
        for state in states:
            if state.attributes.get("id") == unique_id:
                _LOGGER.debug("Found entity %s for unique_id %s", state.entity_id, unique_id)
                return state.entity_id
    _LOGGER.warning("Entity for unique_id %s not found after polling", unique_id)
    return None


# ---------------------------------------------------------------------------
# High-level operations
# ---------------------------------------------------------------------------

async def set_config(
    hass: HomeAssistant,
    resource: str,
    config: dict,
    identifier: str | None = None,
) -> dict:
    """Create or update an automation/script/scene via Config Store API."""
    config = normalize_config_keys(config)

    if resource == "automation":
        if "use_blueprint" in config:
            config = strip_empty_config_fields(config)
            if "alias" not in config:
                raise ValueError("'alias' is required for blueprint automations.")
        else:
            missing = [f for f in ("alias", "trigger", "action") if f not in config]
            if missing:
                raise ValueError(f"Missing required fields: {', '.join(missing)}")
    elif resource == "script":
        if "sequence" not in config and "use_blueprint" not in config:
            raise ValueError("Scripts require 'sequence' or 'use_blueprint'.")

    if identifier is None:
        if resource == "script":
            alias = config.get("alias", "")
            unique_id = _slugify(alias) if alias else str(int(time.time() * 1000))
        else:
            unique_id = str(int(time.time() * 1000))
        operation = "created"
    else:
        unique_id = await resolve_config_id(hass, resource, identifier)
        operation = "updated"

    if resource != "script":
        if "id" not in config:
            config["id"] = unique_id
    else:
        config.pop("id", None)

    await ha_config_request(
        hass, "POST",
        f"/config/{resource}/config/{unique_id}",
        json_data=config,
    )
    _LOGGER.info("%s %s '%s' via Config Store API", operation.title(), resource, unique_id)

    entity_id = None
    if operation == "created":
        entity_id = await poll_for_entity(hass, resource, unique_id)

    return {
        "unique_id": unique_id,
        "entity_id": entity_id,
        "operation": operation,
    }


async def get_config(
    hass: HomeAssistant,
    resource: str,
    identifier: str,
) -> dict:
    """Read config of an automation/script/scene via Config Store API."""
    unique_id = await resolve_config_id(hass, resource, identifier)
    config = await ha_config_request(
        hass, "GET", f"/config/{resource}/config/{unique_id}"
    )
    return normalize_config_for_roundtrip(config)


async def remove_config(
    hass: HomeAssistant,
    resource: str,
    identifier: str,
) -> dict:
    """Delete an automation/script/scene via Config Store API."""
    unique_id = await resolve_config_id(hass, resource, identifier)
    await ha_config_request(
        hass, "DELETE", f"/config/{resource}/config/{unique_id}"
    )
    _LOGGER.info("Deleted %s '%s' via Config Store API", resource, unique_id)
    return {"unique_id": unique_id, "operation": "deleted"}


async def list_config(
    hass: HomeAssistant,
    resource: str,
) -> list[dict]:
    """List all automations/scripts/scenes from HA states."""
    states = hass.states.async_all(resource)
    return [
        {
            "id": s.attributes.get("id", s.entity_id),
            "alias": s.attributes.get("friendly_name", "?"),
        }
        for s in states
    ]
