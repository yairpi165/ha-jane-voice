"""Jane Tool Definitions and Execution Handlers."""

import asyncio
import json
import logging
import uuid
from datetime import timedelta
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
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
                    "description": (
                        "Additional service data. Examples:\n"
                        "- Brightness: {\"brightness_pct\": 50}\n"
                        "- AC temperature: {\"temperature\": 23}\n"
                        "- Volume: {\"volume_level\": 0.5} (0.0=mute, 1.0=max)\n"
                        "- Cover position: {\"position\": 40} (0=closed, 100=open)\n"
                        "- Weather forecast: {\"type\": \"daily\"}"
                    ),
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

TOOL_SEARCH_ENTITIES = {
    "type": "function",
    "function": {
        "name": "search_entities",
        "description": (
            "Search for Home Assistant entities by name, room, or type. "
            "Use when you don't know the exact entity_id. "
            "Returns matching entities with their current state. "
            "Examples: search for 'bedroom' to find bedroom devices, "
            "'tami' to find the water bar, 'temperature' to find temp sensors."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term — device name, room, or type",
                },
                "domain": {
                    "type": "string",
                    "description": "Optional domain filter (light, sensor, switch, climate, cover, media_player, fan, vacuum, button, etc.)",
                },
            },
            "required": ["query"],
        },
    },
}

TOOL_GET_HISTORY = {
    "type": "function",
    "function": {
        "name": "get_history",
        "description": (
            "Get state change history for an entity over the last hours. "
            "Use to answer: 'when did X last turn on?', 'how long was the AC running?', "
            "'what was the temperature this morning?', 'did someone open the door today?'"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "The entity to get history for",
                },
                "hours": {
                    "type": "integer",
                    "description": "Hours of history to look back (default 24, max 72)",
                },
            },
            "required": ["entity_id"],
        },
    },
}

TOOL_LIST_AREAS = {
    "type": "function",
    "function": {
        "name": "list_areas",
        "description": (
            "List all rooms/areas in the home and the devices in each area. "
            "Use to discover what's available, find which room a device is in, "
            "or get an overview of the smart home."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}

TOOL_SEND_NOTIFICATION = {
    "type": "function",
    "function": {
        "name": "send_notification",
        "description": (
            "Send a push notification to a family member's phone or the home tablet. "
            "Use for reminders, alerts, or messages between family members."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Who to notify — a person's name (e.g. 'yair') or 'all' for everyone",
                },
                "message": {
                    "type": "string",
                    "description": "The notification message",
                },
                "title": {
                    "type": "string",
                    "description": "Optional notification title",
                },
            },
            "required": ["target", "message"],
        },
    },
}

TOOL_CHECK_PEOPLE = {
    "type": "function",
    "function": {
        "name": "check_people",
        "description": (
            "Check who is home and where family members are. "
            "Use for 'who is home?', 'is Efrat home?', 'where is Yair?'"
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}

TOOL_SET_TIMER = {
    "type": "function",
    "function": {
        "name": "set_timer",
        "description": (
            "Set a countdown timer. When it expires, Jane sends a notification. "
            "Use for 'set a timer for 5 minutes', 'remind me in 10 minutes', etc. "
            "Max 120 minutes. For longer delays use ha_config_api to create an automation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "minutes": {
                    "type": "integer",
                    "description": "Timer duration in minutes",
                },
                "message": {
                    "type": "string",
                    "description": "Message when timer expires (default: 'הטיימר הסתיים!')",
                },
            },
            "required": ["minutes"],
        },
    },
}

TOOL_MANAGE_LIST = {
    "type": "function",
    "function": {
        "name": "manage_list",
        "description": (
            "Manage shopping and todo lists — add, remove, or view items. "
            "Available lists: shopping (רשימת קניות), personal lists per family member, family list."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "list_name": {
                    "type": "string",
                    "description": "Which list — 'shopping', 'family', or a person's name (yair, efrat, etc.)",
                },
                "action": {
                    "type": "string",
                    "enum": ["view", "add", "remove"],
                    "description": "What to do with the list",
                },
                "item": {
                    "type": "string",
                    "description": "Item text (required for add/remove)",
                },
            },
            "required": ["list_name", "action"],
        },
    },
}

TOOL_GET_STATISTICS = {
    "type": "function",
    "function": {
        "name": "get_statistics",
        "description": (
            "Get min/max/average statistics for a numeric sensor over a time period. "
            "Use for 'what was the average temperature?', 'how much energy today?', etc."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "The sensor entity to get statistics for",
                },
                "hours": {
                    "type": "integer",
                    "description": "Period in hours (default 24, max 168)",
                },
            },
            "required": ["entity_id"],
        },
    },
}

TOOL_GET_LOGBOOK = {
    "type": "function",
    "function": {
        "name": "get_logbook",
        "description": (
            "Get recent events and state changes in the home. "
            "Use for 'what happened today?', 'what changed in the last hour?', "
            "'show me recent activity'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "hours": {
                    "type": "integer",
                    "description": "How many hours back to look (default 4, max 24)",
                },
                "entity_id": {
                    "type": "string",
                    "description": "Optional: filter to a specific entity",
                },
            },
        },
    },
}

TOOL_TTS_ANNOUNCE = {
    "type": "function",
    "function": {
        "name": "tts_announce",
        "description": (
            "Announce a message through a speaker in the home. "
            "Use for broadcasting: 'tell the kids dinner is ready', "
            "'announce that we are leaving in 5 minutes'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The message to announce (in Hebrew)",
                },
            },
            "required": ["message"],
        },
    },
}


# ---------------------------------------------------------------------------
# Active timers (in-memory, do not survive restart)
# ---------------------------------------------------------------------------

_ACTIVE_TIMERS: dict[str, asyncio.Task] = {}


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
    tools = [
        TOOL_GET_ENTITY_STATE,
        TOOL_CALL_HA_SERVICE,
        TOOL_SEARCH_ENTITIES,
        TOOL_GET_HISTORY,
        TOOL_LIST_AREAS,
        TOOL_SEND_NOTIFICATION,
        TOOL_CHECK_PEOPLE,
        TOOL_SET_TIMER,
        TOOL_MANAGE_LIST,
        TOOL_GET_STATISTICS,
        TOOL_GET_LOGBOOK,
        TOOL_TTS_ANNOUNCE,
        TOOL_HA_CONFIG_API,
    ]
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
        elif tool_name == "search_entities":
            return await _handle_search_entities(hass, arguments)
        elif tool_name == "get_history":
            return await _handle_get_history(hass, arguments)
        elif tool_name == "list_areas":
            return await _handle_list_areas(hass, arguments)
        elif tool_name == "send_notification":
            return await _handle_send_notification(hass, arguments)
        elif tool_name == "check_people":
            return await _handle_check_people(hass, arguments)
        elif tool_name == "set_timer":
            return await _handle_set_timer(hass, arguments)
        elif tool_name == "manage_list":
            return await _handle_manage_list(hass, arguments)
        elif tool_name == "get_statistics":
            return await _handle_get_statistics(hass, arguments)
        elif tool_name == "get_logbook":
            return await _handle_get_logbook(hass, arguments)
        elif tool_name == "tts_announce":
            return await _handle_tts_announce(hass, arguments)
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
# Search Entities Handler
# ---------------------------------------------------------------------------

async def _handle_search_entities(hass: HomeAssistant, args: dict) -> str:
    """Search for entities by name or domain."""
    query = args.get("query", "").lower()
    domain_filter = args.get("domain")
    results = []

    for state in hass.states.async_all():
        if domain_filter and state.domain != domain_filter:
            continue
        name = (state.attributes.get("friendly_name") or "").lower()
        eid = state.entity_id.lower()
        if query in name or query in eid:
            results.append({
                "entity_id": state.entity_id,
                "name": state.attributes.get("friendly_name", state.entity_id),
                "state": state.state,
                "domain": state.domain,
            })

    if not results:
        return f"No entities found matching '{query}'."
    # Limit to 15 results to keep GPT context manageable
    results = results[:15]
    return json.dumps(results, ensure_ascii=False)


# ---------------------------------------------------------------------------
# History Handler
# ---------------------------------------------------------------------------

async def _handle_get_history(hass: HomeAssistant, args: dict) -> str:
    """Get state change history for an entity."""
    entity_id = args.get("entity_id", "")
    hours = min(args.get("hours", 24), 72)

    try:
        from homeassistant.components.recorder.history import get_significant_states
        from homeassistant.components.recorder import get_instance
    except ImportError:
        return "History not available (recorder component not loaded)."

    start = dt_util.utcnow() - timedelta(hours=hours)

    try:
        states = await get_instance(hass).async_add_executor_job(
            get_significant_states, hass, start, None, [entity_id],
        )
    except Exception as e:
        return f"Could not retrieve history: {e}"

    if not states or entity_id not in states:
        return f"No history found for {entity_id} in the last {hours} hours."

    entity_states = states[entity_id]
    lines = [f"History for {entity_id} (last {hours}h):"]
    for state in entity_states[-25:]:
        ts = state.last_changed.astimezone(dt_util.DEFAULT_TIME_ZONE).strftime("%H:%M %d/%m")
        attrs = ""
        if "temperature" in state.attributes:
            attrs = f" ({state.attributes['temperature']}°C)"
        elif "brightness" in state.attributes:
            attrs = f" (brightness: {state.attributes['brightness']})"
        lines.append(f"  {ts} — {state.state}{attrs}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# List Areas Handler
# ---------------------------------------------------------------------------

async def _handle_list_areas(hass: HomeAssistant, args: dict) -> str:
    """List all areas and their entities."""
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import entity_registry as er

    area_reg = ar.async_get(hass)
    entity_reg = er.async_get(hass)

    # Build area → entities map
    areas: dict[str, dict] = {}
    for area in area_reg.async_list_areas():
        areas[area.id] = {"name": area.name, "entities": []}

    # Assign entities to areas (via entity or device)
    for entity in entity_reg.entities.values():
        area_id = entity.area_id
        if not area_id:
            # Check device area
            if entity.device_id:
                from homeassistant.helpers import device_registry as dr
                dev_reg = dr.async_get(hass)
                device = dev_reg.async_get(entity.device_id)
                if device:
                    area_id = device.area_id

        if area_id and area_id in areas:
            state = hass.states.get(entity.entity_id)
            if state and not entity.disabled:
                areas[area_id]["entities"].append({
                    "entity_id": entity.entity_id,
                    "name": state.attributes.get("friendly_name", entity.entity_id),
                    "domain": entity.domain,
                    "state": state.state,
                })

    # Format output
    lines = []
    for area_data in sorted(areas.values(), key=lambda a: a["name"]):
        if area_data["entities"]:
            lines.append(f"\n### {area_data['name']}")
            for e in sorted(area_data["entities"], key=lambda x: x["domain"]):
                lines.append(f"- {e['name']} ({e['entity_id']}) — {e['state']}")

    unassigned = []
    for state in hass.states.async_all():
        entity_entry = entity_reg.async_get(state.entity_id)
        has_area = False
        if entity_entry:
            if entity_entry.area_id:
                has_area = True
            elif entity_entry.device_id:
                from homeassistant.helpers import device_registry as dr
                dev_reg = dr.async_get(hass)
                device = dev_reg.async_get(entity_entry.device_id)
                if device and device.area_id:
                    has_area = True
        if not has_area and state.domain in (
            "light", "climate", "cover", "media_player", "fan", "vacuum",
            "switch", "water_heater", "button",
        ):
            unassigned.append(f"- {state.attributes.get('friendly_name', state.entity_id)} ({state.entity_id})")

    if unassigned:
        lines.append("\n### Unassigned Devices")
        lines.extend(unassigned[:20])

    if not lines:
        return "No areas configured in Home Assistant."
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Notification Handler
# ---------------------------------------------------------------------------

async def _handle_send_notification(hass: HomeAssistant, args: dict) -> str:
    """Send push notification to a family member."""
    target = args.get("target", "all").lower().strip()
    message = args.get("message", "")
    title = args.get("title")

    # Find matching notify service dynamically
    all_services = hass.services.async_services().get("notify", {})
    service_name = None

    for svc_name in all_services:
        if target in svc_name.lower() and svc_name != "persistent_notification":
            service_name = svc_name
            break

    if not service_name:
        if target == "all":
            service_name = "notify"
        else:
            available = [s for s in all_services if s not in ("persistent_notification", "notify")]
            return f"No notification target found for '{target}'. Available: {', '.join(available)}"

    data = {"message": message}
    if title:
        data["title"] = title

    try:
        await hass.services.async_call("notify", service_name, data, blocking=True)
        return f"Notification sent to {target}."
    except Exception as e:
        return f"Failed to send notification: {e}"


# ---------------------------------------------------------------------------
# People Tracker Handler
# ---------------------------------------------------------------------------

async def _handle_check_people(hass: HomeAssistant, args: dict) -> str:
    """Check who is home."""
    people = []
    for state in hass.states.async_all("person"):
        name = state.attributes.get("friendly_name", state.entity_id)
        location = state.state
        gps = ""
        if "latitude" in state.attributes and location not in ("home", "not_home"):
            gps = f" (GPS: {state.attributes['latitude']:.4f}, {state.attributes['longitude']:.4f})"
        if location == "home":
            status = "at home"
        elif location == "not_home":
            status = "away"
        else:
            status = location
        people.append(f"- {name}: {status}{gps}")

    if not people:
        return "No people configured in Home Assistant."
    return "Family members:\n" + "\n".join(people)


# ---------------------------------------------------------------------------
# Timer Handler
# ---------------------------------------------------------------------------

async def _handle_set_timer(hass: HomeAssistant, args: dict) -> str:
    """Set a countdown timer with notification on expiry."""
    minutes = args.get("minutes", 0)
    message = args.get("message", "הטיימר הסתיים!")

    if minutes <= 0:
        return "Error: minutes must be positive."
    if minutes > 120:
        return "Error: max 120 minutes. For longer, use ha_config_api to create an automation."

    timer_id = f"jane_timer_{uuid.uuid4().hex[:6]}"

    async def _timer_callback():
        try:
            await asyncio.sleep(minutes * 60)
            # Send persistent notification (always visible on dashboard)
            await hass.services.async_call("notify", "persistent_notification", {
                "message": message,
                "title": "Jane Timer",
            }, blocking=True)
            # Also try push notification
            try:
                await hass.services.async_call("notify", "notify", {
                    "message": message,
                    "title": "Jane Timer",
                }, blocking=True)
            except Exception:
                pass
            _LOGGER.info("Timer %s completed: %s", timer_id, message)
        except asyncio.CancelledError:
            _LOGGER.info("Timer %s cancelled", timer_id)
        finally:
            _ACTIVE_TIMERS.pop(timer_id, None)

    task = hass.async_create_task(_timer_callback())
    _ACTIVE_TIMERS[timer_id] = task
    return f"Timer set for {minutes} minutes. I'll notify you when it's done."


# ---------------------------------------------------------------------------
# List Management Handler
# ---------------------------------------------------------------------------

async def _handle_manage_list(hass: HomeAssistant, args: dict) -> str:
    """Manage shopping/todo lists."""
    list_name = args.get("list_name", "").lower().strip()
    action = args.get("action", "view")
    item = args.get("item", "")

    # Find matching todo entity dynamically
    entity_id = None
    for state in hass.states.async_all("todo"):
        name = (state.attributes.get("friendly_name") or "").lower()
        eid = state.entity_id.lower()
        if list_name in name or list_name in eid or (
            list_name in ("shopping", "קניות") and "qnyvt" in eid
        ):
            entity_id = state.entity_id
            break

    if not entity_id:
        lists = [
            f"{s.attributes.get('friendly_name', s.entity_id)} ({s.entity_id})"
            for s in hass.states.async_all("todo")
        ]
        return f"List '{list_name}' not found. Available:\n" + "\n".join(lists)

    try:
        if action == "view":
            result = await hass.services.async_call(
                "todo", "get_items",
                {"entity_id": entity_id},
                blocking=True, return_response=True,
            )
            if result and entity_id in result:
                items = result[entity_id].get("items", [])
                if not items:
                    return "The list is empty."
                lines = [f"Items in {list_name}:"]
                for i in items:
                    status = "x" if i.get("status") == "completed" else " "
                    lines.append(f"  [{status}] {i.get('summary', '?')}")
                return "\n".join(lines)
            return "The list is empty."

        elif action == "add":
            if not item:
                return "Error: item text is required for add."
            await hass.services.async_call(
                "todo", "add_item",
                {"entity_id": entity_id, "item": item},
                blocking=True,
            )
            return f"Added '{item}' to {list_name}."

        elif action == "remove":
            if not item:
                return "Error: item text is required for remove."
            await hass.services.async_call(
                "todo", "remove_item",
                {"entity_id": entity_id, "item": item},
                blocking=True,
            )
            return f"Removed '{item}' from {list_name}."

        return f"Unknown action: {action}. Use: view, add, remove"
    except Exception as e:
        return f"List operation failed: {e}"


# ---------------------------------------------------------------------------
# Statistics Handler
# ---------------------------------------------------------------------------

async def _handle_get_statistics(hass: HomeAssistant, args: dict) -> str:
    """Get min/max/avg statistics for a numeric sensor."""
    entity_id = args.get("entity_id", "")
    hours = min(args.get("hours", 24), 168)

    try:
        from homeassistant.components.recorder.history import get_significant_states
        from homeassistant.components.recorder import get_instance
    except ImportError:
        return "Statistics not available (recorder not loaded)."

    start = dt_util.utcnow() - timedelta(hours=hours)

    try:
        states = await get_instance(hass).async_add_executor_job(
            get_significant_states, hass, start, None, [entity_id],
        )
    except Exception as e:
        return f"Could not get statistics: {e}"

    if not states or entity_id not in states:
        return f"No data for {entity_id} in the last {hours} hours."

    values = []
    for state in states[entity_id]:
        try:
            values.append(float(state.state))
        except (ValueError, TypeError):
            continue

    if not values:
        return f"No numeric data for {entity_id}. State values are not numbers."

    avg = sum(values) / len(values)
    unit = ""
    current_state = hass.states.get(entity_id)
    if current_state:
        unit = current_state.attributes.get("unit_of_measurement", "")

    lines = [f"Statistics for {entity_id} (last {hours}h):"]
    lines.append(f"  Average: {avg:.1f} {unit}")
    lines.append(f"  Min: {min(values):.1f} {unit}")
    lines.append(f"  Max: {max(values):.1f} {unit}")
    lines.append(f"  Current: {values[-1]:.1f} {unit}")
    lines.append(f"  Data points: {len(values)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Logbook Handler
# ---------------------------------------------------------------------------

async def _handle_get_logbook(hass: HomeAssistant, args: dict) -> str:
    """Get recent events/state changes in the home."""
    hours = min(args.get("hours", 4), 24)
    entity_id = args.get("entity_id")

    try:
        from homeassistant.components.recorder.history import get_significant_states
        from homeassistant.components.recorder import get_instance
    except ImportError:
        return "Logbook not available (recorder not loaded)."

    start = dt_util.utcnow() - timedelta(hours=hours)
    interesting_domains = {
        "light", "climate", "cover", "media_player", "switch",
        "vacuum", "lock", "person", "fan", "water_heater",
    }

    # Get entity IDs to query
    if entity_id:
        entity_ids = [entity_id]
    else:
        entity_ids = [
            s.entity_id for s in hass.states.async_all()
            if s.domain in interesting_domains
        ]

    try:
        states = await get_instance(hass).async_add_executor_job(
            get_significant_states, hass, start, None, entity_ids,
        )
    except Exception as e:
        return f"Could not get logbook: {e}"

    if not states:
        return f"No events in the last {hours} hours."

    # Flatten and sort by time
    events = []
    for eid, entity_states in states.items():
        for state in entity_states:
            name = state.attributes.get("friendly_name", eid)
            events.append((state.last_changed, name, state.state))

    events.sort(key=lambda e: e[0])
    events = events[-30:]  # Last 30 events

    lines = [f"Logbook (last {hours}h):"]
    for ts, name, state_val in events:
        t = ts.astimezone(dt_util.DEFAULT_TIME_ZONE).strftime("%H:%M")
        lines.append(f"  {t} — {name}: {state_val}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# TTS Announce Handler
# ---------------------------------------------------------------------------

async def _handle_tts_announce(hass: HomeAssistant, args: dict) -> str:
    """Announce a message through a speaker."""
    message = args.get("message", "")
    if not message:
        return "Error: message is required."

    # Find TTS entity dynamically
    tts_entity = None
    for state in hass.states.async_all("tts"):
        tts_entity = state.entity_id
        break

    if not tts_entity:
        return "No TTS engine configured."

    # Find a media_player to use (prefer HomePod, fall back to any)
    target_player = None
    for state in hass.states.async_all("media_player"):
        eid = state.entity_id
        if "homepod" in eid.lower() or "slvn" in eid.lower():
            target_player = eid
            break
    if not target_player:
        # Fall back to first available media player
        for state in hass.states.async_all("media_player"):
            target_player = state.entity_id
            break

    if not target_player:
        return "No speaker found to announce on."

    try:
        await hass.services.async_call("tts", "speak", {
            "entity_id": tts_entity,
            "media_player_entity_id": target_player,
            "message": message,
        }, blocking=True)
        return f"Announced on {target_player}."
    except Exception as e:
        return f"TTS announce failed: {e}"


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
