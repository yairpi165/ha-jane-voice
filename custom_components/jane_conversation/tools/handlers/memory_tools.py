"""Memory handlers — save_memory, read_memory."""

import logging

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


async def handle_save_memory(hass: HomeAssistant, args: dict) -> str:
    """Explicitly save to Jane's memory."""
    from ...memory import (
        save_corrections,
        save_family_memory,
        save_habits_memory,
        save_routines,
        save_user_memory,
    )
    from ...memory.manager import (
        load_corrections,
        load_family_memory,
        load_habits_memory,
        load_routines,
        load_user_memory,
    )

    category = args.get("category", "")
    content = args.get("content", "")
    user_name = args.get("user_name", "default")

    if not content:
        return "Error: content is required."

    # Load existing content and append
    loaders = {
        "user": lambda: load_user_memory(user_name),
        "family": load_family_memory,
        "habits": load_habits_memory,
        "corrections": load_corrections,
        "routines": load_routines,
    }
    savers = {
        "user": lambda c: save_user_memory(user_name, c),
        "family": save_family_memory,
        "habits": save_habits_memory,
        "corrections": save_corrections,
        "routines": save_routines,
    }

    if category not in loaders:
        return f"Unknown category: {category}. Use: user, family, habits, corrections, routines"

    existing = await hass.async_add_executor_job(loaders[category])
    if existing:
        new_content = existing + "\n" + content
    else:
        new_content = content

    await hass.async_add_executor_job(savers[category], new_content)
    _LOGGER.info("Memory saved: category=%s, length=%d", category, len(new_content))
    return f"Saved to {category} memory."


async def handle_read_memory(hass: HomeAssistant, args: dict) -> str:
    """Read a specific memory file on demand."""
    from ...memory.manager import (
        load_actions,
        load_corrections,
        load_family_memory,
        load_habits_memory,
        load_routines,
        load_user_memory,
    )

    category = args.get("category", "")
    user_name = args.get("user_name", "default")

    loaders = {
        "user": lambda: load_user_memory(user_name),
        "family": load_family_memory,
        "habits": load_habits_memory,
        "corrections": load_corrections,
        "routines": load_routines,
        "actions": load_actions,
    }

    if category not in loaders:
        return f"Unknown category: {category}. Available: {', '.join(loaders.keys())}"

    content = await hass.async_add_executor_job(loaders[category])
    if not content:
        return f"No {category} memory saved yet."
    return content
