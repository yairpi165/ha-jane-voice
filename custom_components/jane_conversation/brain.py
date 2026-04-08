"""Jane brain — LLM integration, intent parsing, action execution."""

import json
import logging
from openai import OpenAI

from homeassistant.core import HomeAssistant

from .const import SYSTEM_PROMPT
from .memory import load_all_memory

_LOGGER = logging.getLogger(__name__)


def get_exposed_entities(hass: HomeAssistant) -> str:
    """Get relevant entity states from HA for GPT context."""
    relevant_domains = {"light", "switch", "climate", "cover", "media_player", "fan", "weather", "sensor"}
    skip_patterns = ["battery", "signal", "update", "firmware", "ip_address", "uptime"]
    lines = []
    for state in hass.states.async_all():
        domain = state.domain
        if domain not in relevant_domains:
            continue
        eid = state.entity_id.lower()
        if any(p in eid for p in skip_patterns):
            continue
        name = state.attributes.get("friendly_name", state.entity_id)
        if domain == "weather":
            attrs = state.attributes
            temp = attrs.get("temperature", "?")
            humidity = attrs.get("humidity", "?")
            wind = attrs.get("wind_speed", "?")
            cloud = attrs.get("cloud_coverage", "?")
            lines.append(f"- {name} ({state.entity_id}) — {state.state}, {temp}°C, humidity {humidity}%, wind {wind} km/h, clouds {cloud}%")
        else:
            lines.append(f"- {name} ({state.entity_id}) — {state.state}")
    return "\n".join(lines) if lines else "No devices found"


def think(client: OpenAI, user_text: str, user_name: str, hass: HomeAssistant, history: list[dict] | None = None) -> dict:
    """Send text to GPT with smart home context, memory, and conversation history."""
    entities_context = get_exposed_entities(hass)
    memory_context = load_all_memory(user_name)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": f"Memory:\n{memory_context}"},
        {"role": "system", "content": f"Devices:\n{entities_context}"},
    ]

    # Add conversation history for multi-turn context
    if history:
        messages.extend(history)

    messages.append({"role": "user", "content": user_text})

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        max_tokens=300,
        temperature=0.7,
    )

    raw = response.choices[0].message.content.strip()

    # Strip markdown code fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    if raw.endswith("```"):
        raw = raw[:-3]

    try:
        return json.loads(raw.strip())
    except Exception:
        return {"action": "speak", "response": raw}


async def execute(hass: HomeAssistant, result: dict) -> str:
    """Execute the action GPT returned."""
    action = result.get("action")
    response_text = result.get("response", "")

    if action == "ha_service":
        domain = result.get("domain")
        service = result.get("service")
        entity_id = result.get("entity_id")
        data = result.get("data", {})

        try:
            service_data = {"entity_id": entity_id}
            if data:
                service_data.update(data)
            await hass.services.async_call(domain, service, service_data, blocking=True)
        except Exception as e:
            _LOGGER.error("Failed to call HA service: %s", e)
            response_text = "סליחה, לא הצלחתי לבצע את הפקודה"

    return response_text
