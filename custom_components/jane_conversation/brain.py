"""Jane brain — LLM integration with autonomous tool calling (Claude Sonnet 4 + Haiku 4.5)."""

import logging
from datetime import datetime
from anthropic import Anthropic

from homeassistant.core import HomeAssistant

from .const import SYSTEM_PROMPT, CLAUDE_MODEL_FAST, CLAUDE_MODEL_SMART
from .memory import load_home, get_recent_responses
from .tools import get_tools, execute_tool, TOOL_SAVE_MEMORY, TOOL_READ_MEMORY

_LOGGER = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 10

# Hebrew keywords for request classification
_COMMAND_KEYWORDS = {"הדלק", "כבה", "פתח", "סגור", "הפעל", "כבי", "הדליק", "תדליק", "תכבה",
                     "תפתח", "תסגור", "תפעיל", "תכבי", "תדליקי", "הנמיך", "הגביר", "הגבר",
                     "תעלה", "תוריד", "שנה", "שני", "הרתיח", "תרתיח", "תרתיחי",
                     "לילה טוב", "בוקר טוב", "ערב טוב"}
_CHAT_PATTERNS = {"מה שלומך", "שלום", "היי",
                  "ספרי", "ספר לי", "בדיחה", "תודה", "יופי", "סבבה", "מה קורה",
                  "מה נשמע", "אני בסדר", "מה העניינים", "איך את"}
# NOTE: "בוקר טוב", "ערב טוב", "לילה טוב" are NOT chat — they may trigger routines
_COMPLEX_KEYWORDS = {"אוטומציה", "סצנה", "סקריפט", "automation", "תיצרי", "תמחקי",
                     "תשנה", "למה", "תסביר", "מתי", "כמה זמן", "היסטוריה",
                     "רשימה", "קניות", "יומן", "תזכורת", "הודעה"}


async def _build_context(hass: HomeAssistant) -> str:
    """Build concise home awareness context (~50-100 tokens)."""
    parts = []

    weather = hass.states.get("weather.forecast_home")
    if weather:
        temp = weather.attributes.get("temperature", "?")
        parts.append(f"Weather: {weather.state}, {temp}°C")

    people_lines = []
    for state in hass.states.async_all("person"):
        name = state.attributes.get("friendly_name", "?")
        status = "home" if state.state == "home" else "away"
        people_lines.append(f"{name}: {status}")
    if people_lines:
        parts.append("People: " + ", ".join(people_lines))

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


def _classify_request(user_text: str) -> str:
    """Classify request as 'chat', 'command', or 'complex'."""
    text = user_text.lower().strip().rstrip("?!.,")

    # Chat — short, no action words
    if len(text) < 40 and not any(kw in text for kw in _COMMAND_KEYWORDS):
        if any(kw in text for kw in _CHAT_PATTERNS):
            return "chat"

    # Complex — needs thinking
    if any(kw in text for kw in _COMPLEX_KEYWORDS):
        return "complex"

    # Command — action words
    if any(kw in text for kw in _COMMAND_KEYWORDS):
        return "command"

    # Default — treat as complex (safer)
    return "complex"


async def think(
    client: Anthropic,
    user_text: str,
    user_name: str,
    hass: HomeAssistant,
    history: list[dict] | None = None,
    tavily_api_key: str | None = None,
) -> str:
    """Send text to Claude with tools. Claude decides what to call. Returns final response."""

    # Classify request
    request_type = _classify_request(user_text)

    # Choose model based on complexity
    if request_type == "complex":
        model = CLAUDE_MODEL_SMART
        temperature = 0.7
    elif request_type == "command":
        model = CLAUDE_MODEL_FAST
        temperature = 0.4
    else:  # chat
        model = CLAUDE_MODEL_FAST
        temperature = 0.8

    _LOGGER.info("Request type: %s → model: %s", request_type, model.split("-")[1])

    # Build home context (always fast — reads HA state directly)
    home_context = await _build_context(hass)

    # Smart memory loading:
    # - Always: home.md (device map, essential for commands)
    # - Chat/command: nothing else (use read_memory tool if needed)
    # - Complex: nothing else (use read_memory tool if needed)
    # Jane has read_memory tool to load specific memory on demand.
    home_layout = await hass.async_add_executor_job(load_home)

    # Build system prompt with caching
    now = datetime.now().strftime("%A %H:%M")
    dynamic_parts = [f"Current time: {now}"]
    if home_context:
        dynamic_parts.append(f"Home status:\n{home_context}")
    if home_layout:
        dynamic_parts.append(f"Home layout:\n{home_layout}")

    # Anti-repetition
    recent = get_recent_responses()
    if recent:
        dynamic_parts.append(recent)

    system = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": "\n".join(dynamic_parts),
        },
    ]

    # Build messages
    messages = []
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_text})

    # Smart tool filtering
    if request_type == "chat":
        tools = [TOOL_SAVE_MEMORY, TOOL_READ_MEMORY]
    else:
        tools = get_tools(tavily_api_key)

    _LOGGER.info("Tools: %d, Memory: home.md only (read_memory available)", len(tools))

    # Tool calling loop
    for iteration in range(MAX_TOOL_ITERATIONS):
        response = await hass.async_add_executor_job(
            _call_claude, client, model, system, messages, tools, temperature
        )

        if response.stop_reason == "end_turn":
            return _extract_text(response)

        if response.stop_reason == "tool_use":
            tool_names = [b.name for b in response.content if b.type == "tool_use"]
            _LOGGER.info("Jane tool call #%d: %s", iteration + 1, ", ".join(tool_names))

            messages.append({"role": "assistant", "content": _serialize_content(response.content)})

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

            messages.append({"role": "user", "content": tool_results})
            continue

        text = _extract_text(response)
        if text:
            return text

    _LOGGER.warning("Max tool iterations reached, forcing final response")
    response = await hass.async_add_executor_job(
        _call_claude, client, model, system, messages, None, temperature
    )
    return _extract_text(response)


def _call_claude(
    client: Anthropic,
    model: str,
    system: list[dict] | str,
    messages: list[dict],
    tools: list[dict] | None,
    temperature: float = 0.7,
) -> object:
    """Synchronous Claude call (runs in executor)."""
    # Dynamic max_tokens based on model
    max_tokens = 500 if model == CLAUDE_MODEL_FAST else 2000

    kwargs = {
        "model": model,
        "system": system,
        "messages": messages,
        "max_tokens": max_tokens,
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
