"""Jane Tool Definitions (Gemini function calling format)."""

import logging

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
                    '- Brightness: {"brightness_pct": 50}\n'
                    '- AC temperature: {"temperature": 23}\n'
                    '- Volume: {"volume_level": 0.5} (0.0=mute, 1.0=max)\n'
                    '- Cover position: {"position": 40} (0=closed, 100=open)\n'
                    '- Weather forecast: {"type": "daily"}'
                ),
            },
        },
        "required": ["domain", "service", "entity_id"],
    },
}

TOOL_QUERY_HISTORY = {
    "name": "query_history",
    "description": (
        "Query household history — what happened in the house. "
        "Use for questions like 'what happened last night?', 'when did I come home?', "
        "'what happened on Thursday?', 'when was the last time the AC stayed on all night?'. "
        "Supports both time-based and semantic search over episodes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "hours_back": {
                "type": "integer",
                "description": "How many hours back to search. Default 24. Max 168 (7 days).",
            },
            "query": {
                "type": "string",
                "description": "Natural language search query for semantic matching over past episodes.",
            },
        },
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
        "\"{{ now().strftime('%Y-%m-%d') == '2026-04-10' }}\"}], "
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
        "Delete a Home Assistant automation permanently.\nUse entity_id (automation.morning_routine) or unique_id."
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
    "description": ("Delete a Home Assistant script permanently.\nUse entity_id (script.xxx) or unique_id."),
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
    "description": ("Delete a Home Assistant scene permanently.\nUse entity_id (scene.xxx) or unique_id."),
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
    "description": ("List all automations, scripts, or scenes. Returns id and alias for each item."),
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
                "description": "Who to notify — a person's name or 'all' for everyone",
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
        "Check who is home and where family members are. Use for 'who is home?', 'is <person> home?', 'where is <person>?'"
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
                "description": "Which list — 'shopping', 'family', or a person's name",
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
        'Examples: count entities (\'{{ states.light | selectattr("state","eq","on") | list | count }}\'), '
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
        "Get upcoming calendar events. Use for 'what's on today?', 'do I have anything tomorrow?', 'what's this week?'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "Calendar entity ID (e.g. calendar.family). If not specified, searches all calendars.",
            },
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
        "'remind me about the meeting', 'schedule dinner'. "
        "For all-day events like birthdays, use date-only format (2026-12-08) without time."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "Calendar entity ID (e.g. calendar.family). If not specified, uses the first available calendar.",
            },
            "summary": {
                "type": "string",
                "description": "Event title/summary",
            },
            "start": {
                "type": "string",
                "description": "Start date or datetime. Use date-only (2026-12-08) for all-day events, or ISO datetime (2026-04-10T09:00:00) for timed events.",
            },
            "end": {
                "type": "string",
                "description": "End date or datetime. For all-day events use the next day (2026-12-09). For timed events use ISO datetime.",
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
        "Read the full configuration of a script by its id. Use for 'what does this script do?', 'show me the script'."
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
    "description": ("List all floors in the home and which areas belong to each floor."),
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
        "Update a device's name or area assignment. Use for 'move the heater to the bedroom', 'rename the vacuum'."
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
