"""Config handlers — set_config, remove_config, list_config, get_config, get_automation_traces, deep_search."""

import json
import logging

from homeassistant.core import HomeAssistant

from ...config import (
    _CONFIG_API_RESOURCES,
    get_config,
    ha_config_request,
    list_config,
    remove_config,
    set_config,
)

_LOGGER = logging.getLogger(__name__)


async def handle_set_config(hass: HomeAssistant, args: dict, resource: str) -> str:
    """Create or update an automation/script/scene (like MCP's ha_config_set_*)."""
    config = args.get("config", {}) or {}
    identifier = args.get("identifier")

    # Handle config passed as JSON string
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except json.JSONDecodeError:
            return f"Error: config is not valid JSON: {config[:100]}"

    if not config:
        return "Error: config is required."

    # Guard: config has 'id' but no identifier → probably meant to update
    if identifier is None and "id" in config:
        existing_id = config["id"]
        return (
            f"Error: config contains 'id' ('{existing_id}') but no identifier was provided. "
            f"To update, pass identifier='{existing_id}'. "
            f"To create new, remove 'id' from config."
        )

    _LOGGER.info("set_%s called: identifier=%s, config_keys=%s",
                 resource, identifier, list(config.keys()))

    try:
        result = await set_config(hass, resource, config, identifier)
        alias = config.get("alias") or config.get("name", result["unique_id"])
        op = result["operation"]
        entity_id = result.get("entity_id")

        if op == "created" and entity_id:
            return f"Created {resource} '{alias}' (entity: {entity_id})."
        elif op == "created":
            return f"Created {resource} '{alias}' (entity not yet visible, may take a moment)."
        else:
            return f"Updated {resource} '{alias}'."
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        _LOGGER.error("set_%s failed: %s", resource, e)
        return f"Error creating/updating {resource}: {e}"


async def handle_remove_config(hass: HomeAssistant, args: dict, resource: str) -> str:
    """Delete an automation/script/scene (like MCP's ha_config_remove_*)."""
    identifier = args.get("identifier", "")
    if not identifier:
        return "Error: identifier is required."

    _LOGGER.info("remove_%s called: identifier=%s", resource, identifier)

    try:
        await remove_config(hass, resource, identifier)
        return f"Deleted {resource} '{identifier}'."
    except RuntimeError as e:
        if "404" in str(e):
            return f"{resource} '{identifier}' not found."
        return f"Error deleting {resource}: {e}"
    except Exception as e:
        _LOGGER.error("remove_%s failed: %s", resource, e)
        return f"Error deleting {resource}: {e}"


async def handle_list_config(hass: HomeAssistant, args: dict) -> str:
    """List all automations/scripts/scenes."""
    resource = args.get("resource", "")
    if resource not in _CONFIG_API_RESOURCES:
        return f"Unknown resource: {resource}. Use: automation, scene, script"

    items = await list_config(hass, resource)
    if not items:
        return f"No {resource}s found."
    return json.dumps(items, ensure_ascii=False)


async def handle_get_config(hass: HomeAssistant, args: dict, resource: str) -> str:
    """Read the full config of an automation or script via Config Store API."""
    item_id = args.get("item_id", "")
    if not item_id:
        return "Error: item_id is required."

    try:
        config = await get_config(hass, resource, item_id)
        return json.dumps(config, ensure_ascii=False, indent=2, default=str)
    except RuntimeError as e:
        if "404" in str(e):
            return f"{resource} with id '{item_id}' not found."
        return f"Error reading {resource} config: {e}"


async def handle_get_automation_traces(hass: HomeAssistant, args: dict) -> str:
    """Get recent execution traces of an automation."""
    automation_id = args.get("automation_id", "")
    if not automation_id:
        return "Error: automation_id is required."

    # Get automation state for last triggered
    state = hass.states.get(automation_id)
    if not state:
        return f"Automation '{automation_id}' not found."

    name = state.attributes.get("friendly_name", automation_id)
    last_triggered = state.attributes.get("last_triggered", "never")
    current_state = state.state  # on/off

    lines = [
        f"Automation: {name}",
        f"Status: {current_state}",
        f"Last triggered: {last_triggered}",
    ]

    # Traces are available in HA UI
    lines.append("(Detailed traces available in HA UI → Automations → Traces)")

    return "\n".join(lines)


async def handle_deep_search(hass: HomeAssistant, args: dict) -> str:
    """Search inside automations, scripts, and scenes via Config Store API."""
    query = args.get("query", "").lower()
    if not query:
        return "Error: query is required."

    results = []
    for domain in _CONFIG_API_RESOURCES:
        # Get all entities in this domain from HA states
        states = hass.states.async_all(domain)
        for state in states:
            unique_id = state.attributes.get("id", "")
            if not unique_id:
                continue
            try:
                config = await ha_config_request(
                    hass, "GET", f"/config/{domain}/config/{unique_id}"
                )
                config_str = json.dumps(config, ensure_ascii=False, default=str).lower()
                if query in config_str:
                    alias = config.get("alias") or state.attributes.get("friendly_name", "?")
                    results.append(f"- {domain}: {alias} (id: {unique_id})")
            except Exception:
                # Fall back to checking state attributes
                attr_str = json.dumps(dict(state.attributes), ensure_ascii=False, default=str).lower()
                if query in attr_str:
                    alias = state.attributes.get("friendly_name", "?")
                    results.append(f"- {domain}: {alias} (id: {unique_id})")

    if not results:
        return f"No automations, scripts, or scenes found containing '{query}'."
    return f"Found '{query}' in:\n" + "\n".join(results)
