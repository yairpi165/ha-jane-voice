"""Family handlers — check_people, send_notification, set_timer, manage_list, tts_announce."""

import asyncio
import logging
import uuid

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Active timers (in-memory, do not survive restart)
# ---------------------------------------------------------------------------

_ACTIVE_TIMERS: dict[str, asyncio.Task] = {}


async def handle_check_people(hass: HomeAssistant, args: dict) -> str:
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


async def handle_send_notification(hass: HomeAssistant, args: dict) -> str:
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


async def handle_set_timer(hass: HomeAssistant, args: dict) -> str:
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


async def handle_manage_list(hass: HomeAssistant, args: dict) -> str:
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


async def handle_tts_announce(hass: HomeAssistant, args: dict) -> str:
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
