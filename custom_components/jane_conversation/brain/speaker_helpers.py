"""S3.0 (JANE-71) — internal helpers for `speaker.py`.

Split out from speaker.py to keep both files under the 300-line cap.
Functions here are package-private (`_`-prefixed by convention but exported
for `speaker.py` to import).
"""

from __future__ import annotations

import json
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from ..const import DOMAIN, normalize_person_state

_LOGGER = logging.getLogger(__name__)


def get_redis(hass: HomeAssistant):
    """Pull the Redis client off JaneData; None if not initialized."""
    return getattr(hass.data.get(DOMAIN), "redis", None)


async def is_exactly_one_home(hass: HomeAssistant) -> tuple[str | None, int]:
    """Return (sole-resident-name, home-count). Redis-first, hass.states fallback."""
    redis = get_redis(hass)
    if redis is not None:
        try:
            presence = await redis.hgetall("jane:presence")
        except Exception:  # noqa: BLE001
            presence = None
        if presence:
            home = [name for name, status in presence.items() if status == "home"]
            return (home[0], len(home)) if len(home) == 1 else (None, len(home))
    # Fallback: read hass.states directly (matches `check_people`).
    home_names: list[str] = []
    for state in hass.states.async_all("person"):
        if normalize_person_state(state.state) == "home":
            name = state.attributes.get("friendly_name") or state.entity_id
            home_names.append(name)
    if len(home_names) == 1:
        return home_names[0], 1
    return None, len(home_names)


async def resolve_sole_resident_in_area(hass: HomeAssistant, area_id: str) -> str | None:
    """Return the sole resident of an area, or None if 0 or >1 residents.

    A "resident of area X" is a `person.*` entity whose linked device or the
    entity itself sits in area X. v1: check the `person.*` entry's area_id
    (entity-level first, else linked device's area).
    """
    try:
        if ar.async_get(hass).async_get_area(area_id) is None:
            return None
    except Exception:  # noqa: BLE001
        return None
    dev_reg = dr.async_get(hass)
    residents: list[str] = []
    for state in hass.states.async_all("person"):
        person_area = _entity_area(hass, dev_reg, state.entity_id)
        if person_area == area_id:
            name = state.attributes.get("friendly_name") or state.entity_id
            residents.append(name)
    return residents[0] if len(residents) == 1 else None


def _entity_area(hass: HomeAssistant, dev_reg, entity_id: str) -> str | None:
    """Resolve an entity's area: prefer entity-level area_id, else device area."""
    try:
        ent_reg = er.async_get(hass)
    except Exception:  # noqa: BLE001
        return None
    entry = ent_reg.async_get(entity_id)
    if entry is None:
        return None
    if entry.area_id:
        return entry.area_id
    if entry.device_id:
        device = dev_reg.async_get(entry.device_id)
        if device is not None:
            return device.area_id
    return None


async def get_primary_user(hass: HomeAssistant) -> str | None:
    """Look up the primary user from `persons.metadata.is_primary = true` (D8)."""
    structured = getattr(hass.data.get(DOMAIN), "structured", None)
    if structured is None:
        return None
    try:
        persons = await structured.load_persons()
    except Exception:  # noqa: BLE001
        return None
    for person in persons:
        meta = person.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (json.JSONDecodeError, TypeError):
                meta = {}
        if isinstance(meta, dict) and meta.get("is_primary") is True:
            return person.get("name") or None
    return None
