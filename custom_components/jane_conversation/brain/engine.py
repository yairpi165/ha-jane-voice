"""Brain engine — main think() loop, LLM calls, tool execution."""

import logging
from datetime import datetime

from google import genai
from google.genai import types
from homeassistant.core import HomeAssistant

from ..const import DOMAIN, GEMINI_MODEL_FAST, GEMINI_MODEL_SMART, SYSTEM_PROMPT
from ..memory import get_recent_responses, load_home
from ..memory.context_builder import build_episodic_context, build_memory_context
from ..tools import execute_tool, get_tools, get_tools_minimal
from .classifier import classify_request
from .context import build_context, load_routines_index

_LOGGER = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 10


async def think(
    client: genai.Client,
    user_text: str,
    user_name: str,
    hass: HomeAssistant,
    history: list | None = None,
    tavily_api_key: str | None = None,
    working_memory=None,
) -> str:
    """Send text to Gemini with tools. Gemini decides what to call. Returns final response."""

    request_type = classify_request(user_text)

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

    _LOGGER.info("Request type: %s -> model: %s", request_type, model)

    # Build context
    home_context = await build_context(hass, working_memory)
    home_layout = await hass.async_add_executor_job(load_home)
    routines_context = await load_routines_index(hass)

    # Build system instruction
    now = datetime.now().strftime("%A %H:%M")
    system_parts = [SYSTEM_PROMPT, f"\nCurrent time: {now}"]
    if home_context:
        system_parts.append(f"\nHome status:\n{home_context}")
    if home_layout:
        system_parts.append(f"\nHome layout:\n{home_layout}")
    if routines_context:
        system_parts.append(f"\nKnown routines:\n{routines_context}")

    # Inject user memory (preferences, family) from structured store
    memory_context = await build_memory_context(hass, user_name)
    if memory_context:
        system_parts.append(f"\nMemory:\n{memory_context}")

    # Inject episodic context (recent episodes + yesterday's summary)
    episodic_context = await build_episodic_context(hass)
    if episodic_context:
        system_parts.append(f"\nRecent Activity:\n{episodic_context}")

    # Inject user policies (role, quiet hours)
    policy_store = getattr(hass.data.get(DOMAIN), "policies", None)
    if policy_store:
        try:
            policy_context = await policy_store.build_policy_context(user_name)
            if policy_context:
                system_parts.append(f"\nUser Policy:\n{policy_context}")
        except Exception:
            pass

    recent = get_recent_responses()
    if recent:
        system_parts.append(f"\n{recent}")

    system_instruction = "\n".join(system_parts)

    # Build messages
    messages = []
    if history:
        for msg in history:
            if isinstance(msg, dict):
                role = "model" if msg.get("role") == "assistant" else msg.get("role", "user")
                messages.append(
                    types.Content(
                        role=role,
                        parts=[types.Part(text=msg.get("content", ""))],
                    )
                )
            else:
                messages.append(msg)
    messages.append(
        types.Content(
            role="user",
            parts=[types.Part(text=user_text)],
        )
    )

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
        response = await hass.async_add_executor_job(_call_gemini, client, model, messages, config)

        if not response.candidates or not response.candidates[0].content or not response.candidates[0].content.parts:
            # Check if blocked by safety filter
            if response.candidates and hasattr(response.candidates[0], "finish_reason"):
                reason = response.candidates[0].finish_reason
                _LOGGER.warning("Empty response from Gemini, finish_reason=%s", reason)
            else:
                _LOGGER.warning("Empty response from Gemini (no candidates)")

            # Retry once without tools (safety filters sometimes block tool responses)
            if iteration == 0:
                _LOGGER.info("Retrying without tools...")
                retry_config = types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    max_output_tokens=max_output,
                    temperature=temperature,
                )
                response = await hass.async_add_executor_job(_call_gemini, client, model, messages, retry_config)
                if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                    return _extract_text(response.candidates[0].content.parts)

            return ""

        parts = response.candidates[0].content.parts
        function_calls = [p for p in parts if hasattr(p, "function_call") and p.function_call]

        if not function_calls:
            return _extract_text(parts)

        # Execute tools
        tool_names = [fc.function_call.name for fc in function_calls]
        _LOGGER.info("Jane tool call #%d: %s", iteration + 1, ", ".join(tool_names))

        messages.append(response.candidates[0].content)

        function_response_parts = []
        for fc_part in function_calls:
            fc = fc_part.function_call
            args = dict(fc.args) if fc.args else {}
            result = await execute_tool(hass, fc.name, args, tavily_api_key)
            function_response_parts.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        name=fc.name,
                        response={"result": result},
                    )
                )
            )

        messages.append(
            types.Content(
                role="user",
                parts=function_response_parts,
            )
        )

    # Max iterations
    _LOGGER.warning("Max tool iterations reached, forcing final response")
    config_no_tools = types.GenerateContentConfig(
        system_instruction=system_instruction,
        max_output_tokens=max_output,
        temperature=temperature,
    )
    response = await hass.async_add_executor_job(_call_gemini, client, model, messages, config_no_tools)
    return _extract_text(response.candidates[0].content.parts) if response.candidates else ""


def _call_gemini(
    client: genai.Client,
    model: str,
    messages: list,
    config: types.GenerateContentConfig,
) -> object:
    """Synchronous Gemini call (runs in executor) with retry + fallback."""
    import time

    from ..const import GEMINI_MODEL_FAST

    try:
        return client.models.generate_content(
            model=model, contents=messages, config=config,
        )
    except Exception as e:
        if "503" not in str(e) and "429" not in str(e) and "UNAVAILABLE" not in str(e):
            raise
        _LOGGER.warning("Gemini %s unavailable, retrying in 3s: %s", model, e)

    time.sleep(3)  # Blocking sleep OK — runs in executor thread
    try:
        return client.models.generate_content(
            model=model, contents=messages, config=config,
        )
    except Exception as e:
        if model == GEMINI_MODEL_FAST:
            raise  # Flash failed too, nothing to fall back to
        if "503" not in str(e) and "429" not in str(e) and "UNAVAILABLE" not in str(e):
            raise
        _LOGGER.warning("Gemini %s retry failed, falling back to Flash: %s", model, e)

    # Fallback to Flash — reuses Pro config (tools, max_tokens) for graceful degradation
    return client.models.generate_content(
        model=GEMINI_MODEL_FAST, contents=messages, config=config,
    )


def _extract_text(parts) -> str:
    """Extract text from Gemini response parts."""
    for part in parts:
        if hasattr(part, "text") and part.text:
            return part.text
    return ""
