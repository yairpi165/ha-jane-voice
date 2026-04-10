"""Config Store API — HA REST API client for automations, scripts, scenes.

Uses the same REST API as HA's MCP server and UI:
  POST   /api/config/{resource}/config/{id}  → create/update
  GET    /api/config/{resource}/config/{id}  → read
  DELETE /api/config/{resource}/config/{id}  → delete

No direct YAML file manipulation — HA handles all serialization via .storage/.
"""

import asyncio
import json
import logging
import time
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_CONFIG_API_RESOURCES = {"automation", "scene", "script"}


# ---------------------------------------------------------------------------
# Auth — internal Long-Lived Access Token
# ---------------------------------------------------------------------------

async def _get_api_token(hass: HomeAssistant) -> str:
    """Get or create an internal API access token for Config Store calls."""
    domain_data = hass.data.get(DOMAIN, {})

    # Reuse cached refresh token
    refresh_token = domain_data.get("_api_refresh_token")
    if refresh_token is not None:
        return hass.auth.async_create_access_token(refresh_token)

    # Find existing or create new refresh token
    owner = await hass.auth.async_get_owner()
    if owner is None:
        raise RuntimeError("No owner user found in Home Assistant")

    for rt in owner.refresh_tokens.values():
        if rt.client_name == "Jane Internal API":
            domain_data["_api_refresh_token"] = rt
            return hass.auth.async_create_access_token(rt)

    # Create new long-lived refresh token
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
# Resolve — entity_id → unique_id (like MCP's _resolve_automation_id)
# ---------------------------------------------------------------------------

async def resolve_config_id(
    hass: HomeAssistant, resource: str, identifier: str
) -> str:
    """Convert entity_id to unique_id if needed.

    If identifier starts with the resource domain (e.g. 'automation.'),
    fetch state and extract the 'id' attribute (Config Store unique_id).
    Otherwise assume it's already a unique_id.
    """
    if identifier.startswith(f"{resource}."):
        state = hass.states.get(identifier)
        if state is None:
            raise RuntimeError(f"Entity {identifier} not found")
        unique_id = state.attributes.get("id")
        if not unique_id:
            raise RuntimeError(f"Entity {identifier} has no unique_id attribute")
        _LOGGER.debug("Resolved %s → unique_id %s", identifier, unique_id)
        return str(unique_id)
    return identifier


# ---------------------------------------------------------------------------
# Normalize — match MCP's normalization pipeline
# ---------------------------------------------------------------------------

def normalize_config_keys(config: dict) -> dict:
    """Normalize root-level plural keys to singular (triggers→trigger, etc.).

    Only normalizes at root level — deeper keys like 'conditions' inside
    choose/if blocks must stay plural (HA requires it).
    """
    normalized = config.copy()
    for plural, singular in [
        ("triggers", "trigger"),
        ("actions", "action"),
        ("conditions", "condition"),
    ]:
        if plural in normalized and singular not in normalized:
            normalized[singular] = normalized.pop(plural)
    return normalized


def normalize_trigger_keys(triggers: list) -> list:
    """Normalize trigger objects: 'trigger' key → 'platform' key.

    HA GET API returns triggers with 'trigger' key for platform type,
    but SET API expects 'platform'. Needed for round-trip compatibility.
    """
    result = []
    for trigger in triggers:
        if not isinstance(trigger, dict):
            result.append(trigger)
            continue
        t = trigger.copy()
        if "trigger" in t and "platform" not in t:
            t["platform"] = t.pop("trigger")
        result.append(t)
    return result


def normalize_config_for_roundtrip(config: dict) -> dict:
    """Normalize config from GET response so it can be used in SET directly."""
    normalized = normalize_config_keys(config)
    if "trigger" in normalized and isinstance(normalized["trigger"], list):
        normalized["trigger"] = normalize_trigger_keys(normalized["trigger"])
    return normalized


def strip_empty_config_fields(config: dict) -> dict:
    """Remove empty trigger/action/condition arrays.

    Blueprint automations should not have these fields — empty arrays
    override the blueprint's own config and break the automation.
    """
    cleaned = config.copy()
    for field in ("trigger", "action", "condition"):
        if field in cleaned and cleaned[field] == []:
            del cleaned[field]
    return cleaned


# ---------------------------------------------------------------------------
# Poll — verify entity was created/removed (like MCP's _poll_for_automation_entity)
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
                _LOGGER.debug(
                    "Found entity %s for unique_id %s", state.entity_id, unique_id
                )
                return state.entity_id
    _LOGGER.warning("Entity for unique_id %s not found after polling", unique_id)
    return None


# ---------------------------------------------------------------------------
# High-level operations (called by tool handlers)
# ---------------------------------------------------------------------------

async def set_config(
    hass: HomeAssistant,
    resource: str,
    config: dict,
    identifier: str | None = None,
) -> dict:
    """Create or update an automation/script/scene via Config Store API.

    Returns dict with keys: unique_id, entity_id, operation.
    """
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

    if identifier is None:
        # Create new
        unique_id = str(int(time.time() * 1000))
        operation = "created"
    else:
        # Update existing
        unique_id = await resolve_config_id(hass, resource, identifier)
        operation = "updated"

    if "id" not in config:
        config["id"] = unique_id

    await ha_config_request(
        hass, "POST",
        f"/config/{resource}/config/{unique_id}",
        json_data=config,
    )
    _LOGGER.info("%s %s '%s' via Config Store API", operation.title(), resource, unique_id)

    # Poll for entity on create
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
