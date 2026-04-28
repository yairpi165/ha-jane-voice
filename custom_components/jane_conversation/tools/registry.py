"""Jane Tool Registry — declarations, get_tools, and execute_tool dispatcher."""

import logging

from homeassistant.core import HomeAssistant

from ..const import DOMAIN, PERSONAL_DATA_ACTIONS, SENSITIVE_ACTIONS
from .definitions import (
    TOOL_BULK_CONTROL,
    TOOL_CALL_HA_SERVICE,
    TOOL_CHECK_PEOPLE,
    TOOL_CREATE_CALENDAR_EVENT,
    TOOL_CREATE_HELPER,
    TOOL_DEEP_SEARCH,
    TOOL_EVAL_TEMPLATE,
    TOOL_FORGET_MEMORY,
    TOOL_GET_AUTOMATION_CONFIG,
    TOOL_GET_AUTOMATION_TRACES,
    TOOL_GET_CALENDAR_EVENTS,
    TOOL_GET_DEVICE,
    TOOL_GET_ENTITY_STATE,
    TOOL_GET_HISTORY,
    TOOL_GET_LOGBOOK,
    TOOL_GET_OVERVIEW,
    TOOL_GET_SCRIPT_CONFIG,
    TOOL_GET_STATISTICS,
    TOOL_GET_ZONE,
    TOOL_LIST_AREAS,
    TOOL_LIST_CONFIG,
    TOOL_LIST_FLOORS,
    TOOL_LIST_HELPERS,
    TOOL_LIST_SERVICES,
    TOOL_MANAGE_LIST,
    TOOL_QUERY_HISTORY,
    TOOL_READ_MEMORY,
    TOOL_REMOVE_AUTOMATION,
    TOOL_REMOVE_SCENE,
    TOOL_REMOVE_SCRIPT,
    TOOL_RENAME_ENTITY,
    TOOL_SAVE_MEMORY,
    TOOL_SEARCH_ENTITIES,
    TOOL_SEARCH_WEB,
    TOOL_SEND_NOTIFICATION,
    TOOL_SET_AUTOMATION,
    TOOL_SET_SCENE,
    TOOL_SET_SCRIPT,
    TOOL_SET_TIMER,
    TOOL_TTS_ANNOUNCE,
    TOOL_UPDATE_DEVICE,
)
from .handlers import (
    calendar,
    core,
    device,
    discovery,
    family,
    memory_tools,
    power,
)
from .handlers import (
    config as config_handlers,
)

_LOGGER = logging.getLogger(__name__)

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
    TOOL_FORGET_MEMORY,
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
    TOOL_QUERY_HISTORY,
]


def get_tools(tavily_api_key: str | None = None) -> list[dict]:
    """Return tools in Gemini format."""
    from google.genai import types

    declarations = list(_ALL_FUNCTION_DECLARATIONS)
    declarations.append(TOOL_SEARCH_WEB)
    return [types.Tool(function_declarations=declarations)]


def get_tools_minimal() -> list[dict]:
    """Return minimal tools for chat mode."""
    from google.genai import types

    return [types.Tool(function_declarations=[TOOL_SAVE_MEMORY, TOOL_READ_MEMORY, TOOL_SEARCH_WEB])]


# ---------------------------------------------------------------------------
# Handler dispatch map
# ---------------------------------------------------------------------------

_HANDLER_MAP = {
    "get_entity_state": core.handle_get_entity_state,
    "call_ha_service": core.handle_call_ha_service,
    "search_entities": discovery.handle_search_entities,
    "get_history": discovery.handle_get_history,
    "list_areas": discovery.handle_list_areas,
    "get_statistics": discovery.handle_get_statistics,
    "get_logbook": discovery.handle_get_logbook,
    "get_overview": discovery.handle_get_overview,
    "list_floors": discovery.handle_list_floors,
    "get_zone": discovery.handle_get_zone,
    "check_people": family.handle_check_people,
    "send_notification": family.handle_send_notification,
    "set_timer": family.handle_set_timer,
    "manage_list": family.handle_manage_list,
    "tts_announce": family.handle_tts_announce,
    "get_automation_traces": config_handlers.handle_get_automation_traces,
    "deep_search": config_handlers.handle_deep_search,
    "get_calendar_events": calendar.handle_get_calendar_events,
    "create_calendar_event": calendar.handle_create_calendar_event,
    "get_device": device.handle_get_device,
    "rename_entity": device.handle_rename_entity,
    "update_device": device.handle_update_device,
    "list_helpers": device.handle_list_helpers,
    "list_services": device.handle_list_services,
    "create_helper": device.handle_create_helper,
    "save_memory": memory_tools.handle_save_memory,
    "forget_memory": memory_tools.handle_forget_memory,
    "read_memory": memory_tools.handle_read_memory,
    "query_history": memory_tools.handle_query_history,
    "eval_template": power.handle_eval_template,
    "bulk_control": power.handle_bulk_control,
}


# ---------------------------------------------------------------------------
# Tool Execution
# ---------------------------------------------------------------------------


async def execute_tool(
    hass: HomeAssistant,
    tool_name: str,
    arguments: dict,
    tavily_api_key: str | None = None,
    user_name: str = "default",
    confidence: float = 1.0,
    device_id: str | None = None,
    conversation_id: str | None = None,
    original_request: str = "",
) -> str:
    """Execute a tool and return the result as a string for GPT.

    `user_name` + `confidence` are the resolved-speaker context (S3.0).

    For tools in SENSITIVE_ACTIONS / PERSONAL_DATA_ACTIONS:

    1. **Step 4 trigger** fires iff the deny would be *recoverable by knowing
       who is speaking* — i.e., confidence < the per-set threshold AND
       `device_id` is known. In that case we persist a pending-ask payload
       and raise `SpeakerAskRequired`; the engine catches and emits "מי מדבר?".

    2. The full `check_permission` runs after the trigger check. Anything it
       denies for non-confidence reasons (role, quiet-hours, or confidence
       without a device_id we can replay through) returns the deny string
       to the LLM so it can phrase a Hebrew response. Asking "מי מדבר?"
       wouldn't unlock those — distinguishing them is essential to avoid
       deny-loops on child users or quiet-hours bypasses.
    """
    if tool_name in SENSITIVE_ACTIONS or tool_name in PERSONAL_DATA_ACTIONS:
        # Step 4 trigger — recoverable denies only. Threshold logic mirrors
        # `policy.check_permission`; we duplicate it here because the trigger
        # decision is structurally different from the deny decision: only
        # confidence-based denies become asks.
        needs_ask = (confidence < 0.5 and tool_name in PERSONAL_DATA_ACTIONS) or (
            confidence < 0.7 and tool_name in SENSITIVE_ACTIONS
        )
        if needs_ask and device_id:
            from ..brain.speaker_pending_ask import (
                SpeakerAskRequired,
                set_pending_ask,
            )

            await set_pending_ask(hass, device_id, conversation_id, original_request)
            _LOGGER.info(
                "Step 4 ask triggered for %s (conf=%.2f, device=%s)",
                tool_name,
                confidence,
                device_id,
            )
            raise SpeakerAskRequired()

        # Full policy check — handles role, quiet-hours, and the
        # confidence-low-without-device_id case (no replay path possible).
        # Failure-closed: any exception in the gate path is treated as
        # "allow" so a buggy policy store can't brick every tool call.
        policy_store = getattr(hass.data.get(DOMAIN), "policies", None)
        if policy_store is not None:
            try:
                deny = await policy_store.check_permission(user_name, tool_name, confidence=confidence)
            except Exception as e:  # noqa: BLE001
                _LOGGER.debug("Policy gate errored for %s — allowing: %s", tool_name, e)
                deny = None
            if deny is not None:
                _LOGGER.info(
                    "Policy gate denied %s for %s (conf=%.2f): %s",
                    tool_name,
                    user_name,
                    confidence,
                    deny,
                )
                return deny

    try:
        # Config-resource tools that need a resource type parameter
        if tool_name == "get_automation_config":
            return await config_handlers.handle_get_config(hass, arguments, "automation")
        elif tool_name == "get_script_config":
            return await config_handlers.handle_get_config(hass, arguments, "script")
        elif tool_name == "set_automation":
            return await config_handlers.handle_set_config(hass, arguments, "automation")
        elif tool_name == "remove_automation":
            return await config_handlers.handle_remove_config(hass, arguments, "automation")
        elif tool_name == "set_script":
            return await config_handlers.handle_set_config(hass, arguments, "script")
        elif tool_name == "remove_script":
            return await config_handlers.handle_remove_config(hass, arguments, "script")
        elif tool_name == "set_scene":
            return await config_handlers.handle_set_config(hass, arguments, "scene")
        elif tool_name == "remove_scene":
            return await config_handlers.handle_remove_config(hass, arguments, "scene")
        elif tool_name == "list_config":
            return await config_handlers.handle_list_config(hass, arguments)
        elif tool_name == "search_web":
            return await power.handle_search_web(hass, arguments, tavily_api_key)

        # Dict-based dispatch for simple handlers
        handler = _HANDLER_MAP.get(tool_name)
        if handler:
            return await handler(hass, arguments)

        return f"Unknown tool: {tool_name}"
    except Exception as e:
        _LOGGER.error("Tool %s failed: %s", tool_name, e)
        return f"Error: {e}"
