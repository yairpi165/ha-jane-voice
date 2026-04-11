"""Device handlers — get_device, rename_entity, update_device, list_helpers, list_services, create_helper."""

import logging

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


async def handle_get_device(hass: HomeAssistant, args: dict) -> str:
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


async def handle_rename_entity(hass: HomeAssistant, args: dict) -> str:
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


async def handle_update_device(hass: HomeAssistant, args: dict) -> str:
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


async def handle_list_helpers(hass: HomeAssistant, args: dict) -> str:
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


async def handle_list_services(hass: HomeAssistant, args: dict) -> str:
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


async def handle_create_helper(hass: HomeAssistant, args: dict) -> str:
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
