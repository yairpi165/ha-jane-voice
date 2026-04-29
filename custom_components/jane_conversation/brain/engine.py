"""Brain engine — main think() loop, LLM calls, tool execution."""

import logging
from datetime import datetime

from google import genai
from google.genai import types
from homeassistant.core import HomeAssistant

from ..const import DOMAIN, GEMINI_MODEL_FAST, GEMINI_MODEL_SMART, SYSTEM_PROMPT
from ..memory import get_backend, get_recent_responses
from ..memory.context_builder import build_episodic_context, build_memory_context
from ..memory.household_mode import build_mode_context, get_active_mode
from ..tools import execute_tool, get_tools, get_tools_minimal
from .classifier import classify_request
from .context import build_context, load_routines_index
from .speaker_pending_ask import ASK_RESPONSE_HEBREW, SpeakerAskRequired

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
    confidence: float = 1.0,
    device_id: str | None = None,
    conversation_id: str | None = None,
    is_proactive: bool = False,
) -> str:
    """Send text to Gemini with tools. Gemini decides what to call. Returns final response.

    `confidence` is the speaker-resolution confidence (S3.0). It's threaded into
    `build_memory_context` and `build_episodic_context` to apply per-field tier
    gating (Half B of JANE-62 absorption).

    `device_id` + `conversation_id` are forwarded to `execute_tool` so that
    the Step 4 pending-ask trigger can persist `{conversation_id, original_request}`
    in Redis when the gate would deny. If a sensitive call hits the gate at low
    confidence, `execute_tool` raises `SpeakerAskRequired`; this loop catches it
    and returns "מי מדבר?".
    """

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
        temperature = 0.95

    _LOGGER.info("Request type: %s -> model: %s", request_type, model)

    # Build context
    home_context = await build_context(hass, working_memory)
    try:
        home_layout = await get_backend().load("home")
    except Exception:
        home_layout = ""
    routines_context = await load_routines_index(hass)

    # Build system instruction
    now = datetime.now().strftime("%A %d/%m/%Y %H:%M")
    system_parts = [SYSTEM_PROMPT, f"\nCurrent time: {now}"]
    if home_context:
        system_parts.append(f"\nHome status:\n{home_context}")
    if home_layout:
        system_parts.append(f"\nHome layout:\n{home_layout}")
    if routines_context:
        system_parts.append(f"\nKnown routines:\n{routines_context}")

    # Inject user memory (preferences, family) — confidence-aware tiers (S3.0).
    memory_context = await build_memory_context(hass, user_name, confidence=confidence)
    if memory_context:
        system_parts.append(f"\nMemory:\n{memory_context}")

    # Inject episodic context — only at confidence ≥ 0.7 (D11).
    episodic_context = await build_episodic_context(hass, confidence=confidence)
    if episodic_context:
        system_parts.append(f"\nRecent Activity:\n{episodic_context}")

    # Inject user policies (role, quiet hours)
    policy_store = getattr(hass.data.get(DOMAIN), "policies", None)
    if policy_store:
        try:
            policy_context = await policy_store.build_policy_context(user_name)
            if policy_context:
                system_parts.append(f"\nUser Policy:\n{policy_context}")
        except Exception as e:
            _LOGGER.debug("Policy context failed: %s", e)

    # S3.1 (JANE-42): inject the active household mode + behaviour rules so
    # Gemini prompts its phrasing accordingly. Hard enforcement of TTS/
    # notifications still happens in tools/registry.execute_tool — this is
    # the prompt layer of the hybrid (D4).
    mode_context = build_mode_context(get_active_mode(hass))
    system_parts.append(f"\nHousehold mode:\n{mode_context}")

    # S3.2 (JANE-45): the [PROACTIVE] handling section is appended ONLY when
    # this turn is a proactive event — keeps the per-turn token cost off
    # normal user turns. The is_proactive flag is set by conversation.py
    # immediately after [PROACTIVE] detection.
    if is_proactive:
        from .proactive_prompts import PROACTIVE_SYSTEM_INSTRUCTIONS

        system_parts.append(PROACTIVE_SYSTEM_INSTRUCTIONS)

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
            try:
                result = await execute_tool(
                    hass,
                    fc.name,
                    args,
                    tavily_api_key,
                    user_name=user_name,
                    confidence=confidence,
                    device_id=device_id,
                    conversation_id=conversation_id,
                    original_request=user_text,
                )
            except SpeakerAskRequired:
                # Step 4 — gate deflected the sensitive call into a "מי מדבר?"
                # turn. Pending-ask is already in Redis; abandon the tool loop.
                _LOGGER.info("Step 4 ask triggered for %s (device=%s)", fc.name, device_id)
                return ASK_RESPONSE_HEBREW
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
            model=model,
            contents=messages,
            config=config,
        )
    except Exception as e:
        if "503" not in str(e) and "429" not in str(e) and "UNAVAILABLE" not in str(e):
            raise
        _LOGGER.warning("Gemini %s unavailable, retrying in 3s: %s", model, e)

    time.sleep(3)  # Blocking sleep OK — runs in executor thread
    try:
        return client.models.generate_content(
            model=model,
            contents=messages,
            config=config,
        )
    except Exception as e:
        if model == GEMINI_MODEL_FAST:
            raise  # Flash failed too, nothing to fall back to
        if "503" not in str(e) and "429" not in str(e) and "UNAVAILABLE" not in str(e):
            raise
        _LOGGER.warning("Gemini %s retry failed, falling back to Flash: %s", model, e)

    # Fallback to Flash — reuses Pro config (tools, max_tokens) for graceful degradation
    return client.models.generate_content(
        model=GEMINI_MODEL_FAST,
        contents=messages,
        config=config,
    )


def _extract_text(parts) -> str:
    """Extract text from Gemini response parts."""
    for part in parts:
        if hasattr(part, "text") and part.text:
            return part.text
    return ""
