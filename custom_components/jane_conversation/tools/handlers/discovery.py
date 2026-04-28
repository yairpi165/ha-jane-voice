"""Discovery handlers — search_entities, get_history, list_areas, get_statistics, get_logbook, get_overview, list_floors, get_zone."""

import json
import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)


async def handle_search_entities(hass: HomeAssistant, args: dict) -> str:
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
            results.append(
                {
                    "entity_id": state.entity_id,
                    "name": state.attributes.get("friendly_name", state.entity_id),
                    "state": state.state,
                    "domain": state.domain,
                }
            )

    if not results:
        return f"No entities found matching '{query}'."
    # Limit to 15 results to keep GPT context manageable
    results = results[:15]
    return json.dumps(results, ensure_ascii=False)


async def handle_get_history(hass: HomeAssistant, args: dict) -> str:
    """Get state change history for an entity."""
    entity_id = args.get("entity_id", "")
    hours = min(args.get("hours", 24), 72)

    try:
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.history import get_significant_states
    except ImportError:
        return "History not available (recorder component not loaded)."

    start = dt_util.utcnow() - timedelta(hours=hours)

    try:
        states = await get_instance(hass).async_add_executor_job(
            get_significant_states,
            hass,
            start,
            None,
            [entity_id],
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


async def handle_list_areas(hass: HomeAssistant, args: dict) -> str:
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
                areas[area_id]["entities"].append(
                    {
                        "entity_id": entity.entity_id,
                        "name": state.attributes.get("friendly_name", entity.entity_id),
                        "domain": entity.domain,
                        "state": state.state,
                    }
                )

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
            "light",
            "climate",
            "cover",
            "media_player",
            "fan",
            "vacuum",
            "switch",
            "water_heater",
            "button",
        ):
            unassigned.append(f"- {state.attributes.get('friendly_name', state.entity_id)} ({state.entity_id})")

    if unassigned:
        lines.append("\n### Unassigned Devices")
        lines.extend(unassigned[:20])

    if not lines:
        return "No areas configured in Home Assistant."
    return "\n".join(lines)


async def handle_get_statistics(hass: HomeAssistant, args: dict) -> str:
    """Get min/max/avg statistics for a numeric sensor."""
    entity_id = args.get("entity_id", "")
    hours = min(args.get("hours", 24), 168)

    try:
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.history import get_significant_states
    except ImportError:
        return "Statistics not available (recorder not loaded)."

    start = dt_util.utcnow() - timedelta(hours=hours)

    try:
        states = await get_instance(hass).async_add_executor_job(
            get_significant_states,
            hass,
            start,
            None,
            [entity_id],
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


async def handle_get_logbook(hass: HomeAssistant, args: dict) -> str:
    """Get recent events/state changes in the home."""
    hours = min(args.get("hours", 4), 24)
    entity_id = args.get("entity_id")

    try:
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.history import get_significant_states
    except ImportError:
        return "Logbook not available (recorder not loaded)."

    start = dt_util.utcnow() - timedelta(hours=hours)
    interesting_domains = {
        "light",
        "climate",
        "cover",
        "media_player",
        "switch",
        "vacuum",
        "lock",
        "person",
        "fan",
        "water_heater",
    }

    # Get entity IDs to query
    if entity_id:
        entity_ids = [entity_id]
    else:
        entity_ids = [s.entity_id for s in hass.states.async_all() if s.domain in interesting_domains]

    try:
        states = await get_instance(hass).async_add_executor_job(
            get_significant_states,
            hass,
            start,
            None,
            entity_ids,
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


async def handle_get_overview(hass: HomeAssistant, args: dict) -> str:
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


async def handle_list_floors(hass: HomeAssistant, args: dict) -> str:
    """List all floors and their areas."""
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import floor_registry as fr

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


async def handle_get_zone(hass: HomeAssistant, args: dict) -> str:
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
