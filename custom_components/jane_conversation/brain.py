"""Jane brain — LLM integration with autonomous tool calling (Gemini 2.5 Pro + Flash)."""

import logging
from datetime import datetime
from google import genai
from google.genai import types

from homeassistant.core import HomeAssistant

from .const import SYSTEM_PROMPT, GEMINI_MODEL_FAST, GEMINI_MODEL_SMART
from .memory import load_home, get_recent_responses
from .tools import get_tools, get_tools_minimal, execute_tool

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

    if len(text) < 40 and not any(kw in text for kw in _COMMAND_KEYWORDS):
        if any(kw in text for kw in _CHAT_PATTERNS):
            return "chat"

    if any(kw in text for kw in _COMPLEX_KEYWORDS):
        return "complex"

    if any(kw in text for kw in _COMMAND_KEYWORDS):
        return "command"

    return "complex"


async def think(
    client: genai.Client,
    user_text: str,
    user_name: str,
    hass: HomeAssistant,
    history: list | None = None,
    tavily_api_key: str | None = None,
) -> str:
    """Send text to Gemini with tools. Gemini decides what to call. Returns final response."""

    request_type = _classify_request(user_text)

    # Choose model
    if request_type == "complex":
        model = GEMINI_MODEL_SMART
        temperature = 0.7
    elif request_type == "command":
        model = GEMINI_MODEL_FAST
        temperature = 0.4
    else:
        model = GEMINI_MODEL_FAST
        temperature = 0.8

    _LOGGER.info("Request type: %s → model: %s", request_type, model)

    # Build context
    home_context = await _build_context(hass)
    home_layout = await hass.async_add_executor_job(load_home)

    # Build system instruction
    now = datetime.now().strftime("%A %H:%M")
    system_parts = [SYSTEM_PROMPT, f"\nCurrent time: {now}"]
    if home_context:
        system_parts.append(f"\nHome status:\n{home_context}")
    if home_layout:
        system_parts.append(f"\nHome layout:\n{home_layout}")

    recent = get_recent_responses()
    if recent:
        system_parts.append(f"\n{recent}")

    system_instruction = "\n".join(system_parts)

    # Build messages — convert history from dict format to Gemini Content objects
    messages = []
    if history:
        for msg in history:
            if isinstance(msg, dict):
                role = "model" if msg.get("role") == "assistant" else msg.get("role", "user")
                messages.append(types.Content(
                    role=role,
                    parts=[types.Part(text=msg.get("content", ""))],
                ))
            else:
                messages.append(msg)  # Already a Content object
    messages.append(types.Content(
        role="user",
        parts=[types.Part(text=user_text)],
    ))

    # Tools
    tools = get_tools_minimal() if request_type == "chat" else get_tools()

    _LOGGER.info("Tools: %s, model: %s", "minimal" if request_type == "chat" else "full", model)

    # Build config
    max_output = 500 if model == GEMINI_MODEL_FAST else 2000
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=tools,
        max_output_tokens=max_output,
        temperature=temperature,
    )

    # Tool calling loop
    for iteration in range(MAX_TOOL_ITERATIONS):
        response = await hass.async_add_executor_job(
            _call_gemini, client, model, messages, config
        )

        if not response.candidates or not response.candidates[0].content or not response.candidates[0].content.parts:
            _LOGGER.warning("Empty response from Gemini")
            return ""

        parts = response.candidates[0].content.parts

        # Check for function calls
        function_calls = [p for p in parts if hasattr(p, "function_call") and p.function_call]

        if not function_calls:
            # No tool calls — extract text response
            text = _extract_text(parts)
            return text

        # Execute tools
        tool_names = [fc.function_call.name for fc in function_calls]
        _LOGGER.info("Jane tool call #%d: %s", iteration + 1, ", ".join(tool_names))

        # Append model response to messages
        messages.append(response.candidates[0].content)

        # Execute each tool and send results back
        function_response_parts = []
        for fc_part in function_calls:
            fc = fc_part.function_call
            args = dict(fc.args) if fc.args else {}

            result = await execute_tool(hass, fc.name, args, tavily_api_key)

            function_response_parts.append(
                types.Part(function_response=types.FunctionResponse(
                    name=fc.name,
                    response={"result": result},
                ))
            )

        messages.append(types.Content(
            role="user",
            parts=function_response_parts,
        ))

    # Max iterations
    _LOGGER.warning("Max tool iterations reached, forcing final response")
    config_no_tools = types.GenerateContentConfig(
        system_instruction=system_instruction,
        max_output_tokens=max_output,
        temperature=temperature,
    )
    response = await hass.async_add_executor_job(
        _call_gemini, client, model, messages, config_no_tools
    )
    return _extract_text(response.candidates[0].content.parts) if response.candidates else ""


def _call_gemini(
    client: genai.Client,
    model: str,
    messages: list,
    config: types.GenerateContentConfig,
) -> object:
    """Synchronous Gemini call (runs in executor)."""
    return client.models.generate_content(
        model=model,
        contents=messages,
        config=config,
    )


def _extract_text(parts) -> str:
    """Extract text from Gemini response parts."""
    for part in parts:
        if hasattr(part, "text") and part.text:
            return part.text
    return ""
