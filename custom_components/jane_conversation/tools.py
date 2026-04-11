"""Jane Tool Definitions and Execution Handlers."""

import asyncio
import json
import logging
import uuid
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool Definitions (Gemini function calling format)
# ---------------------------------------------------------------------------

TOOL_GET_ENTITY_STATE = {
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
}

TOOL_CALL_HA_SERVICE = {
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
}

TOOL_SEARCH_WEB = {
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
}

TOOL_SET_AUTOMATION = {
    "name": "set_automation",
    "description": (
        "Create or update a Home Assistant automation.\n\n"
        "REQUIRED FIELDS:\n"
        "- alias: Human-readable name\n"
        "- trigger: List of triggers (time, state, event, etc.)\n"
        "- action: List of actions to execute\n\n"
        "OPTIONAL: description, condition, mode (single/restart/queued/parallel)\n\n"
        "EXAMPLE — Time trigger:\n"
        '{"alias": "Heat at 9am", "trigger": [{"platform": "time", "at": "09:00:00"}], '
        '"action": [{"service": "climate.turn_on", "target": {"entity_id": "climate.ac"}}], '
        '"mode": "single"}\n\n'
        "EXAMPLE — One-time with date condition:\n"
        '{"alias": "Heat tomorrow", "trigger": [{"platform": "time", "at": "09:00:00"}], '
        '"condition": [{"condition": "template", "value_template": '
        '"{{ now().strftime(\'%Y-%m-%d\') == \'2026-04-10\' }}"}], '
        '"action": [{"service": "climate.turn_on", "target": {"entity_id": "climate.ac"}}]}\n\n'
        "EXAMPLE — Blueprint:\n"
        '{"alias": "Motion Light", "use_blueprint": {"path": "homeassistant/motion_light.yaml", '
        '"input": {"motion_entity": "binary_sensor.motion", "light_target": {"entity_id": "light.hall"}}}}\n\n'
        "To UPDATE an existing automation, pass its entity_id or unique_id as identifier."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "config": {
                "type": "object",
                "description": "Automation config with alias, trigger, action, etc.",
            },
            "identifier": {
                "type": "string",
                "description": "Entity_id (automation.xxx) or unique_id for updates. Omit to create new.",
            },
        },
        "required": ["config"],
    },
}

TOOL_REMOVE_AUTOMATION = {
    "name": "remove_automation",
    "description": (
        "Delete a Home Assistant automation permanently.\n"
        "Use entity_id (automation.morning_routine) or unique_id."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "identifier": {
                "type": "string",
                "description": "Entity_id (automation.xxx) or unique_id to delete",
            },
        },
        "required": ["identifier"],
    },
}

TOOL_SET_SCRIPT = {
    "name": "set_script",
    "description": (
        "Create or update a Home Assistant script.\n\n"
        "REQUIRED FIELDS:\n"
        "- alias: Human-readable name\n"
        "- sequence: List of actions to execute in order\n\n"
        "EXAMPLE:\n"
        '{"alias": "TV off in 30min", "sequence": ['
        '{"delay": {"minutes": 30}}, '
        '{"service": "media_player.turn_off", "target": {"entity_id": "media_player.tv"}}], '
        '"mode": "single"}\n\n'
        "To UPDATE, pass the script entity_id or unique_id as identifier."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "config": {
                "type": "object",
                "description": "Script config with alias, sequence, mode, etc.",
            },
            "identifier": {
                "type": "string",
                "description": "Script entity_id (script.xxx) or unique_id for updates. Omit to create new.",
            },
        },
        "required": ["config"],
    },
}

TOOL_REMOVE_SCRIPT = {
    "name": "remove_script",
    "description": (
        "Delete a Home Assistant script permanently.\n"
        "Use entity_id (script.xxx) or unique_id."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "identifier": {
                "type": "string",
                "description": "Script entity_id (script.xxx) or unique_id to delete",
            },
        },
        "required": ["identifier"],
    },
}

TOOL_SET_SCENE = {
    "name": "set_scene",
    "description": (
        "Create or update a Home Assistant scene (device states snapshot).\n\n"
        "EXAMPLE:\n"
        '{"name": "Movie Night", "entities": {'
        '"light.living_room": {"state": "on", "brightness": 50}, '
        '"climate.ac": {"state": "cool", "temperature": 24}}}\n\n'
        "To UPDATE, pass the scene entity_id or unique_id as identifier."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "config": {
                "type": "object",
                "description": "Scene config with name and entities.",
            },
            "identifier": {
                "type": "string",
                "description": "Scene entity_id (scene.xxx) or unique_id for updates. Omit to create new.",
            },
        },
        "required": ["config"],
    },
}

TOOL_REMOVE_SCENE = {
    "name": "remove_scene",
    "description": (
        "Delete a Home Assistant scene permanently.\n"
        "Use entity_id (scene.xxx) or unique_id."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "identifier": {
                "type": "string",
                "description": "Scene entity_id (scene.xxx) or unique_id to delete",
            },
        },
        "required": ["identifier"],
    },
}

TOOL_LIST_CONFIG = {
    "name": "list_config",
    "description": (
        "List all automations, scripts, or scenes. "
        "Returns id and alias for each item."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "resource": {
                "type": "string",
                "enum": ["automation", "scene", "script"],
                "description": "The type of resource to list",
            },
        },
        "required": ["resource"],
    },
}

TOOL_SEARCH_ENTITIES = {
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
}

TOOL_GET_HISTORY = {
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
}

TOOL_LIST_AREAS = {
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
}

TOOL_SEND_NOTIFICATION = {
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
}

TOOL_CHECK_PEOPLE = {
    "name": "check_people",
    "description": (
        "Check who is home and where family members are. "
        "Use for 'who is home?', 'is Efrat home?', 'where is Yair?'"
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}

TOOL_SET_TIMER = {
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
}

TOOL_MANAGE_LIST = {
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
}

TOOL_GET_STATISTICS = {
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
}

TOOL_GET_LOGBOOK = {
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
}

TOOL_TTS_ANNOUNCE = {
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
}


TOOL_EVAL_TEMPLATE = {
    "name": "eval_template",
    "description": (
        "Evaluate a Jinja2 template in Home Assistant. Very powerful for calculations and queries. "
        "Examples: count entities ('{{ states.light | selectattr(\"state\",\"eq\",\"on\") | list | count }}'), "
        "date math ('{{ now().strftime(\"%A %d/%m\") }}'), "
        "check conditions ('{{ states(\"sensor.temperature\") | float > 30 }}')."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "template": {
                "type": "string",
                "description": "Jinja2 template string to evaluate",
            },
        },
        "required": ["template"],
    },
}

TOOL_BULK_CONTROL = {
    "name": "bulk_control",
    "description": (
        "Control multiple entities at once with a single command. "
        "Use for 'turn off all lights', 'close all shutters', etc. "
        "Much faster than calling call_ha_service multiple times."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "entity_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of entity IDs to control",
            },
            "domain": {
                "type": "string",
                "description": "Service domain (light, cover, switch, climate, etc.)",
            },
            "service": {
                "type": "string",
                "description": "Service name (turn_on, turn_off, close_cover, etc.)",
            },
            "data": {
                "type": "object",
                "description": "Optional service data (e.g. brightness, temperature)",
            },
        },
        "required": ["entity_ids", "domain", "service"],
    },
}

TOOL_SAVE_MEMORY = {
    "name": "save_memory",
    "description": (
        "Save something important to Jane's memory. Use when you learn about "
        "family members, preferences, corrections, or routines. "
        "Write content in English. This is saved immediately."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": ["user", "family", "habits", "corrections", "routines"],
                "description": "Memory category",
            },
            "content": {
                "type": "string",
                "description": "What to remember (in English)",
            },
            "user_name": {
                "type": "string",
                "description": "User name (required for 'user' category)",
            },
        },
        "required": ["category", "content"],
    },
}

TOOL_READ_MEMORY = {
    "name": "read_memory",
    "description": (
        "Read a specific memory file. Use when you need personal info, family details, "
        "habits, corrections, or routines. The home layout is always available — "
        "use this for everything else.\n"
        "Categories: 'user' (personal preferences), 'family' (family members), "
        "'habits' (behavioral patterns), 'corrections' (past mistakes), "
        "'routines' (goodnight, leaving home, etc.), 'actions' (recent 24h activity)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": ["user", "family", "habits", "corrections", "routines", "actions"],
                "description": "Which memory to read",
            },
            "user_name": {
                "type": "string",
                "description": "User name (required for 'user' category)",
            },
        },
        "required": ["category"],
    },
}

TOOL_GET_DEVICE = {
    "name": "get_device",
    "description": (
        "Get detailed information about a device and all its entities. "
        "Use to understand what a device can do or find all entities belonging to it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Device name or partial name to search for",
            },
        },
        "required": ["query"],
    },
}

TOOL_GET_CALENDAR_EVENTS = {
    "name": "get_calendar_events",
    "description": (
        "Get upcoming calendar events. Use for 'what's on today?', "
        "'do I have anything tomorrow?', 'what's this week?'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "How many days ahead to look (default 1, max 7)",
            },
        },
    },
}

TOOL_CREATE_CALENDAR_EVENT = {
    "name": "create_calendar_event",
    "description": (
        "Create a new calendar event. Use for 'add to calendar', "
        "'remind me about the meeting', 'schedule dinner'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "Event title/summary",
            },
            "start": {
                "type": "string",
                "description": "Start datetime (ISO format: 2026-04-10T09:00:00)",
            },
            "end": {
                "type": "string",
                "description": "End datetime (ISO format: 2026-04-10T10:00:00)",
            },
            "description": {
                "type": "string",
                "description": "Optional event description",
            },
        },
        "required": ["summary", "start", "end"],
    },
}

TOOL_CREATE_HELPER = {
    "name": "create_helper",
    "description": (
        "Create a Home Assistant helper entity (input_boolean, input_number, timer, counter, input_text). "
        "Use for toggles, counters, timers, or text inputs that persist across restarts."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "helper_type": {
                "type": "string",
                "enum": ["input_boolean", "input_number", "timer", "counter", "input_text"],
                "description": "Type of helper to create",
            },
            "name": {
                "type": "string",
                "description": "Friendly name for the helper",
            },
            "icon": {
                "type": "string",
                "description": "Optional icon (e.g. mdi:timer, mdi:counter)",
            },
            "options": {
                "type": "object",
                "description": "Type-specific options: input_number needs min/max/step, timer needs duration",
            },
        },
        "required": ["helper_type", "name"],
    },
}

TOOL_LIST_HELPERS = {
    "name": "list_helpers",
    "description": (
        "List all helper entities (input_boolean, input_number, timer, counter, input_text). "
        "Use to discover what helpers exist."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}

TOOL_LIST_SERVICES = {
    "name": "list_services",
    "description": (
        "List available services for a domain. Use when you're not sure what services "
        "a device type supports (e.g. what can I do with vacuum? with climate?)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "The domain to list services for (e.g. light, climate, vacuum, automation)",
            },
        },
        "required": ["domain"],
    },
}

TOOL_DEEP_SEARCH = {
    "name": "deep_search",
    "description": (
        "Search inside automations, scripts, and scenes for a keyword. "
        "Use for 'is there an automation that uses sensor X?', "
        "'which automations control the living room?'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search term to find inside automation/script/scene configs",
            },
        },
        "required": ["query"],
    },
}

TOOL_RENAME_ENTITY = {
    "name": "rename_entity",
    "description": (
        "Rename an entity's friendly name. Use when the user wants to rename a device "
        "('call the bedroom light Night Light')."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "The entity to rename",
            },
            "new_name": {
                "type": "string",
                "description": "The new friendly name",
            },
        },
        "required": ["entity_id", "new_name"],
    },
}

TOOL_GET_AUTOMATION_CONFIG = {
    "name": "get_automation_config",
    "description": (
        "Read the full configuration of an automation by its id. "
        "Use for 'what does this automation do?', 'show me the automation config'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "The automation id",
            },
        },
        "required": ["item_id"],
    },
}

TOOL_GET_SCRIPT_CONFIG = {
    "name": "get_script_config",
    "description": (
        "Read the full configuration of a script by its id. "
        "Use for 'what does this script do?', 'show me the script'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "item_id": {
                "type": "string",
                "description": "The script id",
            },
        },
        "required": ["item_id"],
    },
}

TOOL_GET_AUTOMATION_TRACES = {
    "name": "get_automation_traces",
    "description": (
        "Get recent execution traces of an automation. Use for debugging: "
        "'why didn't the automation run?', 'did the automation trigger today?'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "automation_id": {
                "type": "string",
                "description": "The automation entity_id (e.g. automation.heat_at_9am)",
            },
        },
        "required": ["automation_id"],
    },
}

TOOL_GET_OVERVIEW = {
    "name": "get_overview",
    "description": (
        "Get a high-level overview of the smart home: total entities, domains, "
        "areas, and entity counts per domain. Use for 'how big is my smart home?', "
        "'how many lights do I have?'."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}

TOOL_LIST_FLOORS = {
    "name": "list_floors",
    "description": (
        "List all floors in the home and which areas belong to each floor."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}

TOOL_GET_ZONE = {
    "name": "get_zone",
    "description": (
        "Get information about a zone (home, work, school, etc.) including "
        "GPS coordinates and radius. Use with check_people to understand locations."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "zone_name": {
                "type": "string",
                "description": "Zone name or entity_id (e.g. 'home', 'zone.work')",
            },
        },
        "required": ["zone_name"],
    },
}

TOOL_UPDATE_DEVICE = {
    "name": "update_device",
    "description": (
        "Update a device's name or area assignment. "
        "Use for 'move the heater to the bedroom', 'rename the vacuum'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "device_query": {
                "type": "string",
                "description": "Device name to search for",
            },
            "new_name": {
                "type": "string",
                "description": "Optional: new device name",
            },
            "area_name": {
                "type": "string",
                "description": "Optional: area/room to assign the device to",
            },
        },
        "required": ["device_query"],
    },
}


# ---------------------------------------------------------------------------
# Active timers (in-memory, do not survive restart)
# ---------------------------------------------------------------------------

_ACTIVE_TIMERS: dict[str, asyncio.Task] = {}


# Config Store API functions
from .config import (  # noqa: E402
    set_config,
    get_config,
    remove_config,
    list_config,
    ha_config_request,
    resolve_config_id,
    normalize_config_for_roundtrip,
    _CONFIG_API_RESOURCES,
)


_ALL_FUNCTION_DECLARATIONS = [
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
    TOOL_EVAL_TEMPLATE,
    TOOL_BULK_CONTROL,
    TOOL_SAVE_MEMORY,
    TOOL_READ_MEMORY,
    TOOL_GET_DEVICE,
    TOOL_GET_CALENDAR_EVENTS,
    TOOL_CREATE_CALENDAR_EVENT,
    TOOL_CREATE_HELPER,
    TOOL_LIST_HELPERS,
    TOOL_LIST_SERVICES,
    TOOL_DEEP_SEARCH,
    TOOL_RENAME_ENTITY,
    TOOL_GET_AUTOMATION_CONFIG,
    TOOL_GET_SCRIPT_CONFIG,
    TOOL_GET_AUTOMATION_TRACES,
    TOOL_GET_OVERVIEW,
    TOOL_LIST_FLOORS,
    TOOL_GET_ZONE,
    TOOL_UPDATE_DEVICE,
    TOOL_SET_AUTOMATION,
    TOOL_REMOVE_AUTOMATION,
    TOOL_SET_SCRIPT,
    TOOL_REMOVE_SCRIPT,
    TOOL_SET_SCENE,
    TOOL_REMOVE_SCENE,
    TOOL_LIST_CONFIG,
]


def get_tools(tavily_api_key: str | None = None) -> list[dict]:
    """Return tools in Gemini format."""
    from google.genai import types

    declarations = list(_ALL_FUNCTION_DECLARATIONS)
    # search_web is always available (uses Google Search internally)
    declarations.append(TOOL_SEARCH_WEB)
    return [types.Tool(function_declarations=declarations)]


def get_tools_minimal() -> list[dict]:
    """Return minimal tools for chat mode."""
    from google.genai import types

    return [types.Tool(function_declarations=[TOOL_SAVE_MEMORY, TOOL_READ_MEMORY, TOOL_SEARCH_WEB])]


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
        elif tool_name == "eval_template":
            return await _handle_eval_template(hass, arguments)
        elif tool_name == "bulk_control":
            return await _handle_bulk_control(hass, arguments)
        elif tool_name == "save_memory":
            return await _handle_save_memory(hass, arguments)
        elif tool_name == "read_memory":
            return await _handle_read_memory(hass, arguments)
        elif tool_name == "get_device":
            return await _handle_get_device(hass, arguments)
        elif tool_name == "get_calendar_events":
            return await _handle_get_calendar_events(hass, arguments)
        elif tool_name == "create_calendar_event":
            return await _handle_create_calendar_event(hass, arguments)
        elif tool_name == "create_helper":
            return await _handle_create_helper(hass, arguments)
        elif tool_name == "list_helpers":
            return await _handle_list_helpers(hass, arguments)
        elif tool_name == "list_services":
            return await _handle_list_services(hass, arguments)
        elif tool_name == "deep_search":
            return await _handle_deep_search(hass, arguments)
        elif tool_name == "rename_entity":
            return await _handle_rename_entity(hass, arguments)
        elif tool_name == "get_automation_config":
            return await _handle_get_config(hass, arguments, "automation")
        elif tool_name == "get_script_config":
            return await _handle_get_config(hass, arguments, "script")
        elif tool_name == "get_automation_traces":
            return await _handle_get_automation_traces(hass, arguments)
        elif tool_name == "get_overview":
            return await _handle_get_overview(hass, arguments)
        elif tool_name == "list_floors":
            return await _handle_list_floors(hass, arguments)
        elif tool_name == "get_zone":
            return await _handle_get_zone(hass, arguments)
        elif tool_name == "update_device":
            return await _handle_update_device(hass, arguments)
        elif tool_name == "search_web":
            return await _handle_search_web(hass, arguments, tavily_api_key)
        elif tool_name == "set_automation":
            return await _handle_set_config(hass, arguments, "automation")
        elif tool_name == "remove_automation":
            return await _handle_remove_config(hass, arguments, "automation")
        elif tool_name == "set_script":
            return await _handle_set_config(hass, arguments, "script")
        elif tool_name == "remove_script":
            return await _handle_remove_config(hass, arguments, "script")
        elif tool_name == "set_scene":
            return await _handle_set_config(hass, arguments, "scene")
        elif tool_name == "remove_scene":
            return await _handle_remove_config(hass, arguments, "scene")
        elif tool_name == "list_config":
            return await _handle_list_config(hass, arguments)
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
    """Search the web using Gemini + Google Search grounding."""
    query = args.get("query", "")
    if not query:
        return "No search query provided."

    try:
        from google import genai
        from google.genai import types
        from .const import CONF_GEMINI_API_KEY

        # Get Gemini client from hass data
        from .const import DOMAIN
        entry = list(hass.data.get(DOMAIN, {}).values())[0]
        client = genai.Client(api_key=entry.data[CONF_GEMINI_API_KEY])

        response = await hass.async_add_executor_job(
            lambda: client.models.generate_content(
                model="gemini-2.5-flash",
                contents=query,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    max_output_tokens=500,
                ),
            )
        )

        if response.candidates and response.candidates[0].content.parts:
            return response.candidates[0].content.parts[0].text
        return "No search results found."
    except Exception as e:
        _LOGGER.error("Google Search failed: %s", e)
        return f"Search failed: {e}"


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
# Eval Template Handler
# ---------------------------------------------------------------------------

async def _handle_eval_template(hass: HomeAssistant, args: dict) -> str:
    """Evaluate a Jinja2 template."""
    template_str = args.get("template", "")
    if not template_str:
        return "Error: template is required."

    try:
        from homeassistant.helpers.template import Template
        tpl = Template(template_str, hass)
        result = tpl.async_render()
        return str(result)
    except Exception as e:
        return f"Template error: {e}"


# ---------------------------------------------------------------------------
# Bulk Control Handler
# ---------------------------------------------------------------------------

async def _handle_bulk_control(hass: HomeAssistant, args: dict) -> str:
    """Control multiple entities at once."""
    entity_ids = args.get("entity_ids", [])
    domain = args.get("domain", "")
    service = args.get("service", "")
    data = args.get("data", {}) or {}

    if not entity_ids:
        return "Error: entity_ids list is required."

    results = []
    for eid in entity_ids:
        try:
            service_data = {"entity_id": eid}
            service_data.update(data)
            await hass.services.async_call(domain, service, service_data, blocking=True)
            results.append(f"{eid}: OK")
        except Exception as e:
            results.append(f"{eid}: failed ({e})")

    return f"Bulk {domain}.{service} on {len(entity_ids)} entities:\n" + "\n".join(results)


# ---------------------------------------------------------------------------
# Save Memory Handler
# ---------------------------------------------------------------------------

async def _handle_save_memory(hass: HomeAssistant, args: dict) -> str:
    """Explicitly save to Jane's memory."""
    from .memory import (
        save_user_memory, save_family_memory, save_habits_memory,
        save_corrections, save_routines, load_user_memory, load_family_memory,
        load_habits_memory, load_corrections, load_routines,
    )

    category = args.get("category", "")
    content = args.get("content", "")
    user_name = args.get("user_name", "default")

    if not content:
        return "Error: content is required."

    # Load existing content and append
    loaders = {
        "user": lambda: load_user_memory(user_name),
        "family": load_family_memory,
        "habits": load_habits_memory,
        "corrections": load_corrections,
        "routines": load_routines,
    }
    savers = {
        "user": lambda c: save_user_memory(user_name, c),
        "family": save_family_memory,
        "habits": save_habits_memory,
        "corrections": save_corrections,
        "routines": save_routines,
    }

    if category not in loaders:
        return f"Unknown category: {category}. Use: user, family, habits, corrections, routines"

    existing = await hass.async_add_executor_job(loaders[category])
    if existing:
        new_content = existing + "\n" + content
    else:
        new_content = content

    await hass.async_add_executor_job(savers[category], new_content)
    _LOGGER.info("Memory saved: category=%s, length=%d", category, len(new_content))
    return f"Saved to {category} memory."


# ---------------------------------------------------------------------------
# Read Memory Handler
# ---------------------------------------------------------------------------

async def _handle_read_memory(hass: HomeAssistant, args: dict) -> str:
    """Read a specific memory file on demand."""
    from .memory import (
        load_user_memory, load_family_memory, load_habits_memory,
        load_corrections, load_routines, load_actions,
    )

    category = args.get("category", "")
    user_name = args.get("user_name", "default")

    loaders = {
        "user": lambda: load_user_memory(user_name),
        "family": load_family_memory,
        "habits": load_habits_memory,
        "corrections": load_corrections,
        "routines": load_routines,
        "actions": load_actions,
    }

    if category not in loaders:
        return f"Unknown category: {category}. Available: {', '.join(loaders.keys())}"

    content = await hass.async_add_executor_job(loaders[category])
    if not content:
        return f"No {category} memory saved yet."
    return content


# ---------------------------------------------------------------------------
# Get Device Handler
# ---------------------------------------------------------------------------

async def _handle_get_device(hass: HomeAssistant, args: dict) -> str:
    """Get device info with all its entities."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    query = args.get("query", "").lower()
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    # Find matching device
    matched_device = None
    for device in dev_reg.devices.values():
        name = (device.name or "").lower()
        if query in name:
            matched_device = device
            break

    if not matched_device:
        return f"No device found matching '{query}'."

    # Find all entities for this device
    entities = []
    for entity in ent_reg.entities.values():
        if entity.device_id == matched_device.id:
            state = hass.states.get(entity.entity_id)
            state_val = state.state if state else "unknown"
            entities.append(f"- {entity.entity_id} ({state_val})")

    lines = [
        f"Device: {matched_device.name}",
        f"Manufacturer: {matched_device.manufacturer or 'unknown'}",
        f"Model: {matched_device.model or 'unknown'}",
        f"Entities ({len(entities)}):",
    ]
    lines.extend(entities[:30])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Calendar Events Handler
# ---------------------------------------------------------------------------

async def _handle_get_calendar_events(hass: HomeAssistant, args: dict) -> str:
    """Get upcoming calendar events."""
    days = min(args.get("days", 1), 7)

    # Find calendar entities
    calendars = [s.entity_id for s in hass.states.async_all("calendar")]
    if not calendars:
        return "No calendars configured."

    from datetime import datetime, timedelta
    now = datetime.now()
    start = now.isoformat()
    end = (now + timedelta(days=days)).isoformat()

    all_events = []
    for cal_id in calendars:
        try:
            result = await hass.services.async_call(
                "calendar", "get_events",
                {"entity_id": cal_id, "start_date_time": start, "end_date_time": end},
                blocking=True, return_response=True,
            )
            if result and cal_id in result:
                events = result[cal_id].get("events", [])
                cal_name = hass.states.get(cal_id).attributes.get("friendly_name", cal_id)
                for ev in events:
                    all_events.append(f"- {ev.get('summary', '?')} ({cal_name}) — {ev.get('start', '?')}")
        except Exception as e:
            _LOGGER.warning("Calendar %s failed: %s", cal_id, e)

    if not all_events:
        return f"No events in the next {days} day(s)."
    return f"Events (next {days} day(s)):\n" + "\n".join(all_events)


# ---------------------------------------------------------------------------
# Create Calendar Event Handler
# ---------------------------------------------------------------------------

async def _handle_create_calendar_event(hass: HomeAssistant, args: dict) -> str:
    """Create a calendar event."""
    summary = args.get("summary", "")
    start = args.get("start", "")
    end = args.get("end", "")
    description = args.get("description", "")

    if not summary or not start or not end:
        return "Error: summary, start, and end are required."

    # Find first calendar
    calendars = [s.entity_id for s in hass.states.async_all("calendar")]
    if not calendars:
        return "No calendars configured."

    cal_id = calendars[0]
    service_data = {
        "entity_id": cal_id,
        "summary": summary,
        "start_date_time": start,
        "end_date_time": end,
    }
    if description:
        service_data["description"] = description

    try:
        await hass.services.async_call("calendar", "create_event", service_data, blocking=True)
        return f"Created event '{summary}' on {start}."
    except Exception as e:
        return f"Failed to create event: {e}"


# ---------------------------------------------------------------------------
# Create Helper Handler
# ---------------------------------------------------------------------------

async def _handle_create_helper(hass: HomeAssistant, args: dict) -> str:
    """Create a HA helper entity."""
    helper_type = args.get("helper_type", "")
    name = args.get("name", "")
    icon = args.get("icon", "")
    options = args.get("options", {}) or {}

    if not helper_type or not name:
        return "Error: helper_type and name are required."

    config = {"name": name}
    if icon:
        config["icon"] = icon

    if helper_type == "input_boolean":
        config.update(options)
    elif helper_type == "input_number":
        config["min"] = options.get("min", 0)
        config["max"] = options.get("max", 100)
        config["step"] = options.get("step", 1)
        config["mode"] = options.get("mode", "slider")
    elif helper_type == "timer":
        config["duration"] = options.get("duration", "00:05:00")
    elif helper_type == "counter":
        config["initial"] = options.get("initial", 0)
        config["step"] = options.get("step", 1)
    elif helper_type == "input_text":
        config["min"] = options.get("min", 0)
        config["max"] = options.get("max", 255)

    try:
        await hass.services.async_call(helper_type, "create", config, blocking=True)
        return f"Created {helper_type} helper: {name}"
    except Exception as e:
        # Fallback: try via config entry
        return f"Failed to create helper: {e}. Try creating it manually in Settings → Helpers."


# ---------------------------------------------------------------------------
# List Helpers Handler
# ---------------------------------------------------------------------------

async def _handle_list_helpers(hass: HomeAssistant, args: dict) -> str:
    """List all helper entities."""
    helper_domains = {"input_boolean", "input_number", "input_text", "timer", "counter", "input_datetime", "input_select"}
    helpers = []

    for state in hass.states.async_all():
        if state.domain in helper_domains:
            name = state.attributes.get("friendly_name", state.entity_id)
            helpers.append(f"- {name} ({state.entity_id}) — {state.state}")

    if not helpers:
        return "No helper entities found."
    return f"Helpers ({len(helpers)}):\n" + "\n".join(helpers)


# ---------------------------------------------------------------------------
# List Services Handler
# ---------------------------------------------------------------------------

async def _handle_list_services(hass: HomeAssistant, args: dict) -> str:
    """List available services for a domain."""
    domain = args.get("domain", "")
    if not domain:
        return "Error: domain is required."

    all_services = hass.services.async_services()
    if domain not in all_services:
        available = sorted(all_services.keys())[:20]
        return f"Domain '{domain}' not found. Available: {', '.join(available)}"

    services = all_services[domain]
    lines = [f"Services for {domain} ({len(services)}):"]
    for svc_name, svc_info in sorted(services.items()):
        desc = ""
        if hasattr(svc_info, "get"):
            desc = svc_info.get("description", "")
        lines.append(f"- {domain}.{svc_name}" + (f" — {desc}" if desc else ""))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Deep Search Handler
# ---------------------------------------------------------------------------

async def _handle_deep_search(hass: HomeAssistant, args: dict) -> str:
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


# ---------------------------------------------------------------------------
# Rename Entity Handler
# ---------------------------------------------------------------------------

async def _handle_rename_entity(hass: HomeAssistant, args: dict) -> str:
    """Rename an entity's friendly name."""
    entity_id = args.get("entity_id", "")
    new_name = args.get("new_name", "")

    if not entity_id or not new_name:
        return "Error: entity_id and new_name are required."

    from homeassistant.helpers import entity_registry as er
    ent_reg = er.async_get(hass)

    entry = ent_reg.async_get(entity_id)
    if not entry:
        return f"Entity '{entity_id}' not found in registry."

    try:
        ent_reg.async_update_entity(entity_id, name=new_name)
        return f"Renamed {entity_id} to '{new_name}'."
    except Exception as e:
        return f"Failed to rename: {e}"


# ---------------------------------------------------------------------------
# Get Automation/Script Config Handler
# ---------------------------------------------------------------------------

async def _handle_get_config(hass: HomeAssistant, args: dict, resource: str) -> str:
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


# ---------------------------------------------------------------------------
# Get Automation Traces Handler
# ---------------------------------------------------------------------------

async def _handle_get_automation_traces(hass: HomeAssistant, args: dict) -> str:
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

    # Try to get traces via websocket API
    try:
        from homeassistant.components.trace import async_get_trace
        # Traces might not be accessible this way in all versions
        lines.append("(Detailed traces available in HA UI → Automations → Traces)")
    except ImportError:
        pass

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Get Overview Handler
# ---------------------------------------------------------------------------

async def _handle_get_overview(hass: HomeAssistant, args: dict) -> str:
    """Get a high-level overview of the smart home."""
    from homeassistant.helpers import area_registry as ar

    all_states = hass.states.async_all()
    area_reg = ar.async_get(hass)

    # Count by domain
    domain_counts: dict[str, int] = {}
    for state in all_states:
        domain_counts[state.domain] = domain_counts.get(state.domain, 0) + 1

    # Sort by count
    sorted_domains = sorted(domain_counts.items(), key=lambda x: -x[1])

    areas = [a.name for a in area_reg.async_list_areas()]

    lines = [
        f"Total entities: {len(all_states)}",
        f"Total areas: {len(areas)}",
        f"Areas: {', '.join(areas) if areas else 'none configured'}",
        "",
        "Entities by domain:",
    ]
    for domain, count in sorted_domains[:20]:
        lines.append(f"  {domain}: {count}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# List Floors Handler
# ---------------------------------------------------------------------------

async def _handle_list_floors(hass: HomeAssistant, args: dict) -> str:
    """List all floors and their areas."""
    from homeassistant.helpers import floor_registry as fr
    from homeassistant.helpers import area_registry as ar

    floor_reg = fr.async_get(hass)
    area_reg = ar.async_get(hass)

    floors = list(floor_reg.async_list_floors())
    if not floors:
        return "No floors configured."

    # Map areas to floors
    lines = []
    for floor in floors:
        floor_areas = [a.name for a in area_reg.async_list_areas() if a.floor_id == floor.floor_id]
        lines.append(f"### {floor.name}")
        if floor_areas:
            for area_name in floor_areas:
                lines.append(f"  - {area_name}")
        else:
            lines.append("  (no areas assigned)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Get Zone Handler
# ---------------------------------------------------------------------------

async def _handle_get_zone(hass: HomeAssistant, args: dict) -> str:
    """Get zone information."""
    zone_name = args.get("zone_name", "").lower()

    for state in hass.states.async_all("zone"):
        name = state.attributes.get("friendly_name", "").lower()
        eid = state.entity_id.lower()
        if zone_name in name or zone_name in eid:
            lat = state.attributes.get("latitude", "?")
            lon = state.attributes.get("longitude", "?")
            radius = state.attributes.get("radius", "?")
            return (
                f"Zone: {state.attributes.get('friendly_name', state.entity_id)}\n"
                f"  Location: {lat}, {lon}\n"
                f"  Radius: {radius}m\n"
                f"  People in zone: {state.state}"
            )

    return f"Zone '{zone_name}' not found."


# ---------------------------------------------------------------------------
# Update Device Handler
# ---------------------------------------------------------------------------

async def _handle_update_device(hass: HomeAssistant, args: dict) -> str:
    """Update a device's name or area."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import area_registry as ar

    query = args.get("device_query", "").lower()
    new_name = args.get("new_name")
    area_name = args.get("area_name")

    if not query:
        return "Error: device_query is required."

    dev_reg = dr.async_get(hass)

    # Find device
    device = None
    for d in dev_reg.devices.values():
        if query in (d.name or "").lower():
            device = d
            break

    if not device:
        return f"Device '{query}' not found."

    updates = {}
    if new_name:
        updates["name"] = new_name
    if area_name:
        area_reg = ar.async_get(hass)
        # Find or create area
        area = None
        for a in area_reg.async_list_areas():
            if area_name.lower() in a.name.lower():
                area = a
                break
        if area:
            updates["area_id"] = area.id
        else:
            return f"Area '{area_name}' not found."

    if not updates:
        return "Nothing to update. Provide new_name or area_name."

    try:
        dev_reg.async_update_device(device.id, **updates)
        parts = []
        if new_name:
            parts.append(f"renamed to '{new_name}'")
        if area_name:
            parts.append(f"moved to '{area_name}'")
        return f"Device '{device.name}' {' and '.join(parts)}."
    except Exception as e:
        return f"Failed to update device: {e}"


# ---------------------------------------------------------------------------
# Config API Handler — uses HA Config Store REST API (no YAML file writing)
# ---------------------------------------------------------------------------

async def _handle_set_config(hass: HomeAssistant, args: dict, resource: str) -> str:
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


async def _handle_remove_config(hass: HomeAssistant, args: dict, resource: str) -> str:
    """Delete an automation/script/scene (like MCP's ha_config_remove_*)."""
    identifier = args.get("identifier", "")
    if not identifier:
        return "Error: identifier is required."

    _LOGGER.info("remove_%s called: identifier=%s", resource, identifier)

    try:
        result = await remove_config(hass, resource, identifier)
        return f"Deleted {resource} '{identifier}'."
    except RuntimeError as e:
        if "404" in str(e):
            return f"{resource} '{identifier}' not found."
        return f"Error deleting {resource}: {e}"
    except Exception as e:
        _LOGGER.error("remove_%s failed: %s", resource, e)
        return f"Error deleting {resource}: {e}"


async def _handle_list_config(hass: HomeAssistant, args: dict) -> str:
    """List all automations/scripts/scenes."""
    resource = args.get("resource", "")
    if resource not in _CONFIG_API_RESOURCES:
        return f"Unknown resource: {resource}. Use: automation, scene, script"

    items = await list_config(hass, resource)
    if not items:
        return f"No {resource}s found."
    return json.dumps(items, ensure_ascii=False)
