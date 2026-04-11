"""Config normalization — key mapping, slugification, field cleanup."""

import logging
import re
import time

_LOGGER = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """Convert text to an ASCII-only slug for script IDs. HA rejects non-ASCII."""
    slug = text.lower().strip()
    # Keep only ASCII letters, digits, spaces, hyphens
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s-]+", "_", slug).strip("_")
    # Must have at least 3 chars and start with a letter — otherwise use timestamp
    if len(slug) < 3 or not slug[0].isalpha():
        return f"jane_{int(time.time() * 1000)}"
    return slug[:40]


def normalize_config_keys(config: dict) -> dict:
    """Normalize root-level plural keys to singular (triggers->trigger, etc.).

    Only normalizes at root level — deeper keys like 'conditions' inside
    choose/if blocks must stay plural (HA requires it).
    """
    normalized = config.copy()
    for plural, singular in [
        ("triggers", "trigger"),
        ("actions", "action"),
        ("conditions", "condition"),
    ]:
        if plural in normalized and singular not in normalized:
            normalized[singular] = normalized.pop(plural)
    return normalized


def normalize_trigger_keys(triggers: list) -> list:
    """Normalize trigger objects: 'trigger' key -> 'platform' key.

    HA GET API returns triggers with 'trigger' key for platform type,
    but SET API expects 'platform'. Needed for round-trip compatibility.
    """
    result = []
    for trigger in triggers:
        if not isinstance(trigger, dict):
            result.append(trigger)
            continue
        t = trigger.copy()
        if "trigger" in t and "platform" not in t:
            t["platform"] = t.pop("trigger")
        result.append(t)
    return result


def normalize_config_for_roundtrip(config: dict) -> dict:
    """Normalize config from GET response so it can be used in SET directly."""
    normalized = normalize_config_keys(config)
    if "trigger" in normalized and isinstance(normalized["trigger"], list):
        normalized["trigger"] = normalize_trigger_keys(normalized["trigger"])
    return normalized


def strip_empty_config_fields(config: dict) -> dict:
    """Remove empty trigger/action/condition arrays.

    Blueprint automations should not have these fields — empty arrays
    override the blueprint's own config and break the automation.
    """
    cleaned = config.copy()
    for field in ("trigger", "action", "condition"):
        if field in cleaned and cleaned[field] == []:
            del cleaned[field]
    return cleaned
