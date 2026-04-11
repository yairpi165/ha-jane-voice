"""Calendar handlers — get_calendar_events, create_calendar_event."""

import logging
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


async def handle_get_calendar_events(hass: HomeAssistant, args: dict) -> str:
    """Get upcoming calendar events."""
    days = min(args.get("days", 1), 7)

    # Find calendar entities
    calendars = [s.entity_id for s in hass.states.async_all("calendar")]
    if not calendars:
        return "No calendars configured."

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


async def handle_create_calendar_event(hass: HomeAssistant, args: dict) -> str:
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
