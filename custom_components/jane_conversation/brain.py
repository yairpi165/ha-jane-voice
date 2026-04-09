"""Jane brain — LLM integration with autonomous tool calling."""

import json
import logging
from datetime import datetime
from openai import OpenAI

from homeassistant.core import HomeAssistant

from .const import SYSTEM_PROMPT
from .memory import load_all_memory, get_recent_responses
from .tools import get_tools, execute_tool

_LOGGER = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 10

# Hebrew keywords for dynamic temperature selection
_COMMAND_KEYWORDS = {"הדלק", "כבה", "פתח", "סגור", "הפעל", "כבי", "הדליק", "תדליק", "תכבה", "תפתח", "תסגור"}
_CHAT_KEYWORDS = {"מה שלומך", "ספרי", "ספר", "מה אתה", "מה את", "בדיחה", "שיחה", "בוקר טוב", "ערב טוב", "לילה טוב"}


async def _build_context(hass: HomeAssistant) -> str:
    """Build concise home awareness context (~50-100 tokens)."""
    parts = []

    # Weather
    weather = hass.states.get("weather.forecast_home")
    if weather:
        temp = weather.attributes.get("temperature", "?")
        parts.append(f"Weather: {weather.state}, {temp}°C")

    # People
    people_lines = []
    for state in hass.states.async_all("person"):
        name = state.attributes.get("friendly_name", "?")
        status = "home" if state.state == "home" else "away"
        people_lines.append(f"{name}: {status}")
    if people_lines:
        parts.append("People: " + ", ".join(people_lines))

    # Active devices (lights/climate/media that are ON)
    active = []
    for state in hass.states.async_all():
        if state.domain in ("light", "climate", "media_player") and state.state not in ("off", "unavailable", "idle", "unknown"):
            active.append(state.attributes.get("friendly_name", state.entity_id))
    if active:
        parts.append(f"Active: {', '.join(active[:10])}")

    return "\n".join(parts) if parts else ""


def _get_model_params(user_text: str) -> dict:
    """Choose temperature and penalties based on request type."""
    text_lower = user_text.lower().strip()

    # Commands → precise
    if any(kw in text_lower for kw in _COMMAND_KEYWORDS):
        return {"temperature": 0.4}

    # Conversation → varied and warm
    if any(kw in text_lower for kw in _CHAT_KEYWORDS):
        return {"temperature": 0.9, "frequency_penalty": 1.5, "presence_penalty": 0.6}

    # Default → balanced
    return {"temperature": 0.7, "frequency_penalty": 0.5}


async def think(
    client: OpenAI,
    user_text: str,
    user_name: str,
    hass: HomeAssistant,
    history: list[dict] | None = None,
    tavily_api_key: str | None = None,
) -> str:
    """Send text to GPT with tools. GPT decides what to call. Returns final response."""

    # Load context
    memory_context = await hass.async_add_executor_job(load_all_memory, user_name)

    # Build real-time home awareness
    home_context = await _build_context(hass)

    # Build messages
    now = datetime.now().strftime("%A %H:%M")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": f"Current time: {now}"},
    ]

    # Inject home context (weather, people, active devices)
    if home_context:
        messages.append({"role": "system", "content": f"Home status:\n{home_context}"})

    messages.append({"role": "system", "content": f"Memory:\n{memory_context}"})

    # Anti-repetition: inject recent response openings
    recent = get_recent_responses()
    if recent:
        messages.append({"role": "system", "content": recent})

    # Add conversation history
    if history:
        messages.extend(history)

    messages.append({"role": "user", "content": user_text})

    # Get available tools and model parameters
    tools = get_tools(tavily_api_key)
    model_params = _get_model_params(user_text)

    # Tool calling loop
    for iteration in range(MAX_TOOL_ITERATIONS):
        response = await hass.async_add_executor_job(
            _call_gpt, client, messages, tools, model_params
        )

        message = response.choices[0].message

        # No tool calls — GPT is done, return the text response
        if not message.tool_calls:
            return message.content or ""

        # GPT wants to call tools — execute them
        _LOGGER.info(
            "Jane tool call #%d: %s",
            iteration + 1,
            ", ".join(tc.function.name for tc in message.tool_calls),
        )

        # Append assistant message with tool calls
        messages.append(message)

        # Execute each tool and append results
        for tool_call in message.tool_calls:
            try:
                arguments = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                arguments = {}

            result = await execute_tool(
                hass, tool_call.function.name, arguments, tavily_api_key
            )

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })

    # Max iterations reached — get final response without tools
    _LOGGER.warning("Max tool iterations reached, forcing final response")
    response = await hass.async_add_executor_job(
        _call_gpt, client, messages, None, model_params
    )
    return response.choices[0].message.content or ""


def _call_gpt(
    client: OpenAI,
    messages: list[dict],
    tools: list[dict] | None,
    model_params: dict | None = None,
) -> object:
    """Synchronous GPT call (runs in executor)."""
    kwargs = {
        "model": "gpt-5.4-mini",
        "messages": messages,
        "max_completion_tokens": 2000,
    }
    if model_params:
        kwargs.update(model_params)
    else:
        kwargs["temperature"] = 0.7
    if tools:
        kwargs["tools"] = tools

    return client.chat.completions.create(**kwargs)
