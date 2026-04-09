"""Jane Tool Definitions and Execution Handlers."""

import asyncio
import json
import logging
import uuid
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.util.yaml import load_yaml, save_yaml

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool Definitions (OpenAI function calling format)
# ---------------------------------------------------------------------------

TOOL_GET_ENTITY_STATE = {
    "type": "function",
    "function": {
        "name": "get_entity_state",
        "description": (
            "Get the current state and attributes of a Home Assistant entity. "
            "Use to check device status, temperature, weather, sensor readings, "
            "whether a light is on/off, vacuum status, etc."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "The entity ID (e.g. weather.forecast_home, light.switcher_light_3708, vacuum.x40_ultra)",
                },
            },
            "required": ["entity_id"],
        },
    },
}

TOOL_CALL_HA_SERVICE = {
    "type": "function",
    "function": {
        "name": "call_ha_service",
        "description": (
            "Call a Home Assistant service. Use to control devices (turn on/off, "
            "set temperature, open/close covers, set brightness), get weather forecasts, "
            "trigger scripts, or any other HA service."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Service domain (e.g. light, climate, weather, cover, vacuum, script, switch)",
                },
                "service": {
                    "type": "string",
                    "description": "Service name (e.g. turn_on, turn_off, toggle, set_temperature, get_forecasts, start)",
                },
                "entity_id": {
                    "type": "string",
                    "description": "Target entity ID",
                },
                "data": {
                    "type": "object",
                    "description": "Additional service data (e.g. {\"brightness_pct\": 50}, {\"temperature\": 23}, {\"type\": \"daily\"})",
                },
            },
            "required": ["domain", "service", "entity_id"],
        },
    },
}

TOOL_SEARCH_WEB = {
    "type": "function",
    "function": {
        "name": "search_web",
        "description": (
            "Search the web for current information. Use ONLY when the information "
            "is not available from Home Assistant entities or services. "
            "Good for: news, exchange rates, traffic, business hours, sports scores, "
            "recipes, general knowledge questions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query. Use Hebrew for Israeli topics, English for international.",
                },
            },
            "required": ["query"],
        },
    },
}


TOOL_HA_CONFIG_API = {
    "type": "function",
    "function": {
        "name": "ha_config_api",
        "description": (
            "Manage Home Assistant configuration: create, update, delete, or list "
            "automations, scenes, and scripts. Use this to schedule future actions, "
            "create recurring automations, define scenes, or build scripts.\n\n"
            "OPERATIONS:\n"
            "- list: Get all items of a resource type\n"
            "- create: Create a new item\n"
            "- update: Update an existing item by id\n"
            "- delete: Delete an item by id\n\n"
            "AUTOMATION EXAMPLE (triggers + actions):\n"
            '{"alias": "Heat at 9am", "trigger": [{"platform": "time", "at": "09:00"}], '
            '"condition": [], '
            '"action": [{"service": "climate.turn_on", "target": {"entity_id": "climate.ac"}}], '
            '"mode": "single"}\n\n'
            "AUTOMATION WITH DATE CONDITION (one-time):\n"
            '{"alias": "Heat tomorrow", "trigger": [{"platform": "time", "at": "09:00"}], '
            '"condition": [{"condition": "template", "value_template": '
            '"{{ now().strftime(\'%Y-%m-%d\') == \'2026-04-10\' }}"}], '
            '"action": [{"service": "climate.turn_on", "target": {"entity_id": "climate.ac"}}], '
            '"mode": "single"}\n\n'
            "SCRIPT EXAMPLE (sequence with delay):\n"
            '{"alias": "TV off in 30min", "sequence": ['
            '{"delay": {"minutes": 30}}, '
            '{"service": "media_player.turn_off", "target": {"entity_id": "media_player.tv"}}], '
            '"mode": "single"}\n\n'
            "SCENE EXAMPLE (device states snapshot):\n"
            '{"name": "Movie Night", "entities": {'
            '"light.living_room": {"state": "on", "brightness": 50}, '
            '"climate.ac": {"state": "cool", "temperature": 24}}}'
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "resource": {
                    "type": "string",
                    "enum": ["automation", "scene", "script"],
                    "description": "The type of resource to manage",
                },
                "operation": {
                    "type": "string",
                    "enum": ["list", "create", "update", "delete"],
                    "description": "The operation to perform",
                },
                "item_id": {
                    "type": "string",
                    "description": "The id of the item (required for update and delete)",
                },
                "config": {
                    "type": "object",
                    "description": "The configuration object (required for create and update)",
                },
            },
            "required": ["resource", "operation"],
        },
    },
}

# ---------------------------------------------------------------------------
# Config API constants
# ---------------------------------------------------------------------------

_CONFIG_FILES = {
    "automation": "automations.yaml",
    "scene": "scenes.yaml",
    "script": "scripts.yaml",
}

_CONFIG_LOCKS: dict[str, asyncio.Lock] = {}


def _get_lock(resource: str) -> asyncio.Lock:
    """Get or create a lock for a resource type."""
    if resource not in _CONFIG_LOCKS:
        _CONFIG_LOCKS[resource] = asyncio.Lock()
    return _CONFIG_LOCKS[resource]


def get_tools(tavily_api_key: str | None = None) -> list[dict]:
    """Return available tools based on configuration."""
    tools = [TOOL_GET_ENTITY_STATE, TOOL_CALL_HA_SERVICE, TOOL_HA_CONFIG_API]
    if tavily_api_key:
        tools.append(TOOL_SEARCH_WEB)
    return tools


# ---------------------------------------------------------------------------
# Tool Execution Handlers
# ---------------------------------------------------------------------------

async def execute_tool(
    hass: HomeAssistant,
    tool_name: str,
    arguments: dict,
    tavily_api_key: str | None = None,
) -> str:
    """Execute a tool and return the result as a string for GPT."""
    try:
        if tool_name == "get_entity_state":
            return await _handle_get_entity_state(hass, arguments)
        elif tool_name == "call_ha_service":
            return await _handle_call_ha_service(hass, arguments)
        elif tool_name == "search_web":
            return await _handle_search_web(hass, arguments, tavily_api_key)
        elif tool_name == "ha_config_api":
            return await _handle_ha_config_api(hass, arguments)
        else:
            return f"Unknown tool: {tool_name}"
    except Exception as e:
        _LOGGER.error("Tool %s failed: %s", tool_name, e)
        return f"Error: {e}"


async def _handle_get_entity_state(hass: HomeAssistant, args: dict) -> str:
    """Get entity state and format for GPT."""
    entity_id = args.get("entity_id", "")
    state = hass.states.get(entity_id)

    if state is None:
        return f"Entity '{entity_id}' not found."

    name = state.attributes.get("friendly_name", entity_id)
    lines = [f"{name} ({entity_id}): {state.state}"]

    # Include useful attributes
    skip = {"friendly_name", "supported_features", "icon", "entity_picture",
            "attribution", "supported_color_modes", "color_mode"}
    for key, value in state.attributes.items():
        if key not in skip and value is not None:
            lines.append(f"  {key}: {value}")

    return "\n".join(lines)


async def _handle_call_ha_service(hass: HomeAssistant, args: dict) -> str:
    """Call an HA service and return result."""
    domain = args.get("domain", "")
    service = args.get("service", "")
    entity_id = args.get("entity_id", "")
    data = args.get("data", {}) or {}

    service_data = {"entity_id": entity_id}
    service_data.update(data)

    # Services that return data (e.g. weather.get_forecasts)
    services_with_response = {
        ("weather", "get_forecasts"),
        ("calendar", "get_events"),
        ("todo", "get_items"),
    }

    try:
        if (domain, service) in services_with_response:
            result = await hass.services.async_call(
                domain, service, service_data,
                blocking=True, return_response=True,
            )
            if result:
                return json.dumps(result, ensure_ascii=False, indent=2, default=str)
            return "Service returned no data."
        else:
            await hass.services.async_call(
                domain, service, service_data, blocking=True,
            )
            return "Success."
    except Exception as e:
        return f"Service call failed: {e}"


async def _handle_search_web(hass: HomeAssistant, args: dict, tavily_api_key: str | None) -> str:
    """Search the web via Tavily."""
    if not tavily_api_key:
        return "Web search is not configured. No Tavily API key."

    query = args.get("query", "")
    if not query:
        return "No search query provided."

    from .web_search import search_web
    return await hass.async_add_executor_job(search_web, tavily_api_key, query)


# ---------------------------------------------------------------------------
# Config API Handler
# ---------------------------------------------------------------------------

def _read_yaml(path: Path, is_list: bool) -> list | dict:
    """Read a YAML config file. Returns [] or {} if missing/empty."""
    if not path.exists():
        return [] if is_list else {}
    try:
        data = load_yaml(str(path))
    except Exception:
        return [] if is_list else {}
    if data is None:
        return [] if is_list else {}
    return data


async def _handle_ha_config_api(hass: HomeAssistant, args: dict) -> str:
    """Manage HA config: automations, scenes, scripts."""
    resource = args.get("resource", "")
    operation = args.get("operation", "")
    item_id = args.get("item_id")
    config = args.get("config", {}) or {}

    if resource not in _CONFIG_FILES:
        return f"Unknown resource: {resource}. Use: automation, scene, script"

    config_dir = Path(hass.config.config_dir)
    filepath = config_dir / _CONFIG_FILES[resource]
    is_list = resource in ("automation", "scene")

    async with _get_lock(resource):
        if operation == "list":
            data = await hass.async_add_executor_job(_read_yaml, filepath, is_list)
            if is_list:
                items = [
                    {"id": item.get("id"), "alias": item.get("alias") or item.get("name", "?")}
                    for item in data
                ]
            else:
                items = [
                    {"id": key, "alias": val.get("alias", key) if isinstance(val, dict) else key}
                    for key, val in data.items()
                ]
            if not items:
                return f"No {resource}s found."
            return json.dumps(items, ensure_ascii=False)

        elif operation == "create":
            if not config:
                return "Error: config is required for create."
            data = await hass.async_add_executor_job(_read_yaml, filepath, is_list)

            if is_list:
                new_id = uuid.uuid4().hex[:12]
                config["id"] = new_id
                data.append(config)
            else:
                # Scripts: key-based
                alias = config.get("alias", "")
                key = item_id or alias.lower().replace(" ", "_").replace("-", "_")[:40]
                if not key:
                    key = uuid.uuid4().hex[:12]
                data[key] = config
                new_id = key

            await hass.async_add_executor_job(save_yaml, str(filepath), data)
            await hass.services.async_call(resource, "reload", blocking=True)
            return f"Created {resource} with id '{new_id}'."

        elif operation == "update":
            if not item_id:
                return "Error: item_id is required for update."
            if not config:
                return "Error: config is required for update."
            data = await hass.async_add_executor_job(_read_yaml, filepath, is_list)

            if is_list:
                found = False
                for i, item in enumerate(data):
                    if item.get("id") == item_id:
                        config["id"] = item_id
                        data[i] = config
                        found = True
                        break
                if not found:
                    return f"Error: {resource} with id '{item_id}' not found."
            else:
                if item_id not in data:
                    return f"Error: {resource} with id '{item_id}' not found."
                data[item_id] = config

            await hass.async_add_executor_job(save_yaml, str(filepath), data)
            await hass.services.async_call(resource, "reload", blocking=True)
            return f"Updated {resource} '{item_id}'."

        elif operation == "delete":
            if not item_id:
                return "Error: item_id is required for delete."
            data = await hass.async_add_executor_job(_read_yaml, filepath, is_list)

            if is_list:
                original_len = len(data)
                data = [item for item in data if item.get("id") != item_id]
                if len(data) == original_len:
                    return f"Error: {resource} with id '{item_id}' not found."
            else:
                if item_id not in data:
                    return f"Error: {resource} with id '{item_id}' not found."
                del data[item_id]

            await hass.async_add_executor_job(save_yaml, str(filepath), data)
            await hass.services.async_call(resource, "reload", blocking=True)
            return f"Deleted {resource} '{item_id}'."

        else:
            return f"Unknown operation: {operation}. Use: list, create, update, delete"
