"""Jane Tool Definitions and Execution Handlers."""

import json
import logging

from homeassistant.core import HomeAssistant

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


def get_tools(tavily_api_key: str | None = None) -> list[dict]:
    """Return available tools based on configuration."""
    tools = [TOOL_GET_ENTITY_STATE, TOOL_CALL_HA_SERVICE]
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
