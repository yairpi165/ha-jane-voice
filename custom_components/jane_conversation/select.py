"""Jane-owned ``select`` platform — household mode (S3.1 / JANE-42).

The active household mode lives in a SelectEntity owned by this integration
rather than an `input_select` helper, because:

- ``input_select.create`` is **not** a real HA service (it exists only as a
  WebSocket command bound to the storage collection). Auto-creating an
  ``input_select`` from inside ``async_setup_entry`` therefore can't be done
  through the public service layer.
- Owning the entity ourselves gives us a clean place to react to user-driven
  state changes (UI flip, automation, voice tool) and to persist the active
  mode across restarts via ``RestoreEntity``.

The entity_id is ``select.jane_household_mode`` (matches ``HELPER_ENTITY_ID``
in ``modes.py``). Users can flip it from the standard HA "Settings →
Devices & Services → Entities" UI just like any other select entity.
"""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .modes import HOUSEHOLD_MODES, MODE_NORMAL

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Register the household-mode select entity (one per config entry)."""
    async_add_entities([JaneHouseholdModeSelect(entry.entry_id)], update_before_add=False)


class JaneHouseholdModeSelect(SelectEntity, RestoreEntity):
    """Single-select entity exposing Jane's active household mode.

    Internal state changes (mode flip from anywhere — UI, automation, the
    ``set_household_mode`` tool) all flow through ``async_select_option``,
    which writes the new state and emits a ``state_changed`` event. The
    audit-row (``household_mode_transitions``) is written by the caller of
    ``memory.household_mode.set_active_mode`` so trigger / triggered_by /
    reason are captured. UI-direct flips currently produce no audit row;
    S3.2 will close that gap with a state listener.
    """

    _attr_has_entity_name = False
    _attr_name = "Jane Household Mode"
    _attr_icon = "mdi:home-account"
    _attr_should_poll = False
    _attr_options = list(HOUSEHOLD_MODES)

    def __init__(self, entry_id: str) -> None:
        # Stable unique_id so HA preserves entity_id across restarts and lets
        # the user rename the entity from the UI without us re-registering.
        self._attr_unique_id = "jane_household_mode"
        self._entry_id = entry_id
        self._attr_current_option = MODE_NORMAL

    @property
    def suggested_object_id(self) -> str:
        # Force entity_id to "select.jane_household_mode" so HELPER_ENTITY_ID
        # in modes.py matches without the user having to rename anything.
        return "jane_household_mode"

    async def async_added_to_hass(self) -> None:
        """Restore the previously-selected mode after HA restart."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is None:
            return
        if last.state in HOUSEHOLD_MODES:
            self._attr_current_option = last.state
        else:
            # Don't propagate "unknown" / stale value — degrade to NORMAL so
            # the gate path always reads a valid mode.
            _LOGGER.debug("RestoreState %r not in HOUSEHOLD_MODES — defaulting to NORMAL", last.state)
            self._attr_current_option = MODE_NORMAL

    async def async_select_option(self, option: str) -> None:
        """Handle a flip request from any source (UI, service, automation).

        Audit-row policy (closes the gap called out in PR #56 review):

        - If ``set_active_mode`` (voice / automation / time / presence path)
          initiated this flip, it owns the ``household_mode_transitions``
          row — only the caller knows ``trigger`` / ``triggered_by`` /
          ``reason``. We detect this via the ownership flag stashed on
          ``hass.data[DOMAIN]`` and skip logging here to avoid duplicates.
        - Otherwise this is a UI-direct flip (HA Settings → Entities → the
          dropdown) or an external automation calling ``select.select_option``
          without going through ``set_active_mode``. We log it ourselves
          with ``trigger='ui'`` so the table stays an honest source of
          truth instead of silently partial.
        """
        if option not in HOUSEHOLD_MODES:
            _LOGGER.warning("Refusing to set unknown mode: %r", option)
            return
        from_mode = self._attr_current_option
        self._attr_current_option = option
        self.async_write_ha_state()

        from .const import DOMAIN
        from .memory.household_mode import log_transition

        jane = self.hass.data.get(DOMAIN)
        if jane is None or getattr(jane, "_mode_flip_owned_by_caller", False):
            # Voice / automation / time / presence path — set_active_mode
            # is the audit-row owner, leave it alone.
            return
        # UI-direct flip — log it ourselves. log_transition is failure-soft
        # (swallows PG errors), so this can never crash the entity write.
        await log_transition(
            getattr(jane, "pg_pool", None),
            from_mode=from_mode,
            to_mode=option,
            trigger="ui",
            triggered_by=None,
            reason=None,
        )
