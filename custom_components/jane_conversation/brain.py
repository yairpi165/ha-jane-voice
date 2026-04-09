"""Jane brain — LLM integration with autonomous tool calling."""

import json
import logging
from datetime import datetime
from openai import OpenAI

from homeassistant.core import HomeAssistant

from .const import SYSTEM_PROMPT
from .memory import load_all_memory
from .tools import get_tools, execute_tool

_LOGGER = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 5


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

    # Build messages
    now = datetime.now().strftime("%H:%M")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": f"Current time: {now}"},
        {"role": "system", "content": f"Memory:\n{memory_context}"},
    ]

    # Add conversation history
    if history:
        messages.extend(history)

    messages.append({"role": "user", "content": user_text})

    # Get available tools
    tools = get_tools(tavily_api_key)

    # Tool calling loop
    for iteration in range(MAX_TOOL_ITERATIONS):
        response = await hass.async_add_executor_job(
            _call_gpt, client, messages, tools
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
        _call_gpt, client, messages, None
    )
    return response.choices[0].message.content or ""


def _call_gpt(
    client: OpenAI,
    messages: list[dict],
    tools: list[dict] | None,
) -> object:
    """Synchronous GPT call (runs in executor)."""
    kwargs = {
        "model": "gpt-4o-mini",
        "messages": messages,
        "max_tokens": 500,
        "temperature": 0.7,
    }
    if tools:
        kwargs["tools"] = tools

    return client.chat.completions.create(**kwargs)
