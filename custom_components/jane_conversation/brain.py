"""Jane brain — LLM integration with autonomous tool calling (Claude Sonnet 4)."""

import logging
from datetime import datetime
from anthropic import Anthropic

from homeassistant.core import HomeAssistant

from .const import SYSTEM_PROMPT, CLAUDE_MODEL
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

    # Active devices (lights/climate/media that are ON, skip cameras/internal)
    skip_keywords = {"camera", "motion", "microphone", "speaker", "rtsp", "recording", "detection"}
    active = []
    for state in hass.states.async_all():
        if state.domain in ("light", "climate", "media_player", "fan") and state.state not in ("off", "unavailable", "idle", "unknown", "standby"):
            eid = state.entity_id.lower()
            if any(kw in eid for kw in skip_keywords):
                continue
            active.append(state.attributes.get("friendly_name", state.entity_id))
    if active:
        parts.append(f"Active: {', '.join(active[:10])}")

    return "\n".join(parts) if parts else ""


def _get_temperature(user_text: str) -> float:
    """Choose temperature based on request type."""
    text_lower = user_text.lower().strip()

    # Commands → precise
    if any(kw in text_lower for kw in _COMMAND_KEYWORDS):
        return 0.4

    # Conversation → varied
    if any(kw in text_lower for kw in _CHAT_KEYWORDS):
        return 0.8

    # Default → balanced
    return 0.7


async def think(
    client: Anthropic,
    user_text: str,
    user_name: str,
    hass: HomeAssistant,
    history: list[dict] | None = None,
    tavily_api_key: str | None = None,
) -> str:
    """Send text to Claude with tools. Claude decides what to call. Returns final response."""

    # Load context
    memory_context = await hass.async_add_executor_job(load_all_memory, user_name)

    # Build real-time home awareness
    home_context = await _build_context(hass)

    # Build system prompt (Anthropic: separate parameter, not in messages)
    now = datetime.now().strftime("%A %H:%M")
    system_parts = [
        SYSTEM_PROMPT,
        f"\nCurrent time: {now}",
    ]
    if home_context:
        system_parts.append(f"\nHome status:\n{home_context}")
    system_parts.append(f"\nMemory:\n{memory_context}")

    # Anti-repetition
    recent = get_recent_responses()
    if recent:
        system_parts.append(f"\n{recent}")

    system = "\n".join(system_parts)

    # Build messages (Anthropic: only user/assistant, no system messages)
    messages = []
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_text})

    # Get available tools and temperature
    tools = get_tools(tavily_api_key)
    temperature = _get_temperature(user_text)

    # Tool calling loop
    for iteration in range(MAX_TOOL_ITERATIONS):
        response = await hass.async_add_executor_job(
            _call_claude, client, system, messages, tools, temperature
        )

        # Check if Claude is done (no tool calls)
        if response.stop_reason == "end_turn":
            return _extract_text(response)

        # Claude wants to call tools
        if response.stop_reason == "tool_use":
            tool_names = [b.name for b in response.content if b.type == "tool_use"]
            _LOGGER.info("Jane tool call #%d: %s", iteration + 1, ", ".join(tool_names))

            # Append assistant response to messages
            messages.append({"role": "assistant", "content": _serialize_content(response.content)})

            # Execute each tool and collect results
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = await execute_tool(
                        hass, block.name, block.input, tavily_api_key
                    )
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            # Send tool results back as user message
            messages.append({"role": "user", "content": tool_results})
            continue

        # max_tokens or other stop reason — extract whatever text we got
        text = _extract_text(response)
        if text:
            return text

    # Max iterations reached — get final response without tools
    _LOGGER.warning("Max tool iterations reached, forcing final response")
    response = await hass.async_add_executor_job(
        _call_claude, client, system, messages, None, temperature
    )
    return _extract_text(response)


def _call_claude(
    client: Anthropic,
    system: str,
    messages: list[dict],
    tools: list[dict] | None,
    temperature: float = 0.7,
) -> object:
    """Synchronous Claude call (runs in executor)."""
    kwargs = {
        "model": CLAUDE_MODEL,
        "system": system,
        "messages": messages,
        "max_tokens": 2000,
        "temperature": temperature,
    }
    if tools:
        kwargs["tools"] = tools

    return client.messages.create(**kwargs)


def _extract_text(response) -> str:
    """Extract text from Claude response content blocks."""
    for block in response.content:
        if hasattr(block, "text"):
            return block.text
    return ""


def _serialize_content(content) -> list[dict]:
    """Serialize Anthropic content blocks to dicts for message history."""
    result = []
    for block in content:
        if block.type == "text":
            result.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            result.append({
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            })
    return result
