"""Jane config module — HA Config Store REST API client."""

from .api import (
    set_config,
    get_config,
    remove_config,
    list_config,
    ha_config_request,
    resolve_config_id,
    poll_for_entity,
)
from .normalize import (
    normalize_config_keys,
    normalize_trigger_keys,
    normalize_config_for_roundtrip,
    strip_empty_config_fields,
)

_CONFIG_API_RESOURCES = {"automation", "scene", "script"}

__all__ = [
    "set_config",
    "get_config",
    "remove_config",
    "list_config",
    "ha_config_request",
    "resolve_config_id",
    "poll_for_entity",
    "normalize_config_keys",
    "normalize_trigger_keys",
    "normalize_config_for_roundtrip",
    "strip_empty_config_fields",
    "_CONFIG_API_RESOURCES",
]
