"""Compatibility shim — imports from config/ module. Will be removed after full refactor."""

from .config import (  # noqa: F401
    _CONFIG_API_RESOURCES,
    get_config,
    ha_config_request,
    list_config,
    normalize_config_for_roundtrip,
    normalize_config_keys,
    normalize_trigger_keys,
    poll_for_entity,
    remove_config,
    resolve_config_id,
    set_config,
    strip_empty_config_fields,
)
