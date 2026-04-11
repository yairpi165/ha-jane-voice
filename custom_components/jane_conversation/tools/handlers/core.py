"""Core HA handlers — get_entity_state, call_ha_service."""

import json
import logging

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


async def handle_get_entity_state(hass: HomeAssistant, args: dict) -> str:
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


async def handle_call_ha_service(hass: HomeAssistant, args: dict) -> str:
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
