"""Tests for Household Modes — S3.1 (JANE-42).

Covers:

- ``MODE_PRIORITY`` priority-stack semantics (D2).
- Per-mode TTS/notification hard-gate matrix at the registry layer.
- Gate-order assertion (D16): mode → confidence → permission, first deny wins.
- Transition logging (audit row) on ``set_active_mode``.
- ``handle_set_household_mode`` happy path / invalid mode / PG-down.
- ``build_mode_context`` Hebrew block contents.
- ``routines.scope`` / ``user_overrides`` schema-level invariants (idempotent
  IF NOT EXISTS DDL — tests assert the SQL surface, not a real PG).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jane_conversation.const import SENSITIVE_ACTIONS
from jane_conversation.memory.household_mode import (
    build_mode_context,
    get_active_mode,
    log_transition,
    mode_gate_deny,
    set_active_mode,
)
from jane_conversation.modes import (
    HELPER_ENTITY_ID,
    HOUSEHOLD_MODES,
    MODE_AWAY,
    MODE_GUESTS,
    MODE_KIDS_SLEEPING,
    MODE_NIGHT,
    MODE_NORMAL,
    MODE_PRIORITY,
    MODE_RULES,
    MODE_TRAVEL,
    MODE_WORK,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hass_with_mode(mode: str | None) -> MagicMock:
    """Mock hass whose state-store returns ``mode`` for HELPER_ENTITY_ID."""
    hass = MagicMock()
    if mode is None:
        hass.states.get = MagicMock(return_value=None)
    else:
        state = MagicMock()
        state.state = mode
        hass.states.get = MagicMock(return_value=state)
    hass.services.async_call = AsyncMock(return_value=None)
    return hass


def _mock_pool() -> tuple[MagicMock, AsyncMock]:
    """Standard asyncpg pool mock — same shape as the test_policy.py fixture."""
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn


# ---------------------------------------------------------------------------
# 1. Mode priority stack (D2)
# ---------------------------------------------------------------------------


class TestModePriorityStack:
    """``MODE_PRIORITY`` is leftmost-wins. The resolver isn't shipped as code in
    this ticket (S3.2 wires per-condition triggers); the constant itself is the
    contract — these tests pin it so a future re-order doesn't silently change
    safety semantics (kids-sleeping must outrank night, etc.).
    """

    def test_kids_sleeping_outranks_night(self):
        # Per D2: kids-sleeping wins over plain night so a goodnight routine
        # while children are asleep doesn't relax kids-room rules.
        assert MODE_PRIORITY.index(MODE_KIDS_SLEEPING) < MODE_PRIORITY.index(MODE_NIGHT)

    def test_night_outranks_travel(self):
        assert MODE_PRIORITY.index(MODE_NIGHT) < MODE_PRIORITY.index(MODE_TRAVEL)

    def test_travel_outranks_away(self):
        # Travel (multi-day) is a stricter superset of Away (single-trip).
        assert MODE_PRIORITY.index(MODE_TRAVEL) < MODE_PRIORITY.index(MODE_AWAY)

    def test_normal_is_last_resort(self):
        assert MODE_PRIORITY[-1] == MODE_NORMAL

    def test_priority_covers_all_modes(self):
        # Every named mode must appear exactly once. A mode missing from the
        # priority would fall through to NORMAL silently — bad bug surface.
        assert sorted(MODE_PRIORITY) == sorted(HOUSEHOLD_MODES)
        assert len(MODE_PRIORITY) == len(set(MODE_PRIORITY))


# ---------------------------------------------------------------------------
# 2. Hard-gate matrix (D16 step 1) — 7 modes × 2 gated tools
# ---------------------------------------------------------------------------


class TestHardGate:
    """For every mode × {tts_announce, send_notification}, the gate's deny
    decision must match ``MODE_RULES[mode]['tts']``. Two-dimensional matrix
    catches silent regressions when a future contributor flips a single rule
    flag without updating the gate.
    """

    @pytest.mark.parametrize("mode", HOUSEHOLD_MODES)
    @pytest.mark.parametrize("tool", ["tts_announce", "send_notification"])
    def test_gate_matches_mode_rules(self, mode: str, tool: str):
        hass = _hass_with_mode(mode)
        deny = mode_gate_deny(hass, tool)
        if MODE_RULES[mode]["tts"]:
            assert deny is None, f"{tool} should pass in {mode}"
        else:
            assert deny is not None, f"{tool} should be denied in {mode}"
            assert mode in deny, "deny string must name the active mode (Hebrew UX)"

    def test_non_gated_tool_passes_in_silent_mode(self):
        # Only TTS/notification are gated; everything else (e.g. get_history)
        # ignores the mode. This is the "contextual no" boundary.
        hass = _hass_with_mode(MODE_NIGHT)
        assert mode_gate_deny(hass, "get_history") is None
        assert mode_gate_deny(hass, "set_automation") is None

    def test_missing_helper_falls_back_to_normal(self):
        # No helper state ⇒ get_active_mode returns NORMAL ⇒ gate allows.
        # A fresh install before the auto-create runs must not block TTS.
        hass = _hass_with_mode(None)
        assert mode_gate_deny(hass, "tts_announce") is None

    def test_unknown_mode_falls_back_to_normal(self):
        # Foreign value (manual edit of input_select) must degrade to NORMAL,
        # never raise — this is the failure-closed contract.
        hass = _hass_with_mode("ערפילי")
        assert mode_gate_deny(hass, "tts_announce") is None


# ---------------------------------------------------------------------------
# 3. Gate order (D16): mode → confidence → permission
# ---------------------------------------------------------------------------


class TestGateOrderD16:
    """When multiple gates would fire, the user must hear the *most useful*
    Hebrew message — the contextual one (mode) rather than the identity one
    (confidence). Asserting first-deny-wins for the three relevant cases.
    """

    @pytest.mark.asyncio
    async def test_mode_wins_over_confidence_and_permission(self):
        """Child + low confidence + night mode + tts_announce → mode deny."""
        from jane_conversation.tools import execute_tool

        hass = _hass_with_mode(MODE_NIGHT)
        # Even if policies would deny, the mode deny must short-circuit first.
        policies = MagicMock()
        policies.check_permission = AsyncMock(return_value="role denial — should not surface")
        jane_data = MagicMock()
        jane_data.policies = policies
        jane_data.redis = AsyncMock()
        hass.data = {"jane_conversation": jane_data}

        result = await execute_tool(
            hass,
            "tts_announce",
            {"message": "x"},
            user_name="default",
            confidence=0.4,
            device_id="device-X",
        )
        # Mode deny — names the active mode in Hebrew.
        assert MODE_NIGHT in result
        assert "role denial" not in result

    @pytest.mark.asyncio
    async def test_confidence_wins_when_mode_allows(self):
        """tts_announce in NORMAL + child + low confidence → confidence/role gate fires."""
        from jane_conversation.brain.speaker_pending_ask import SpeakerAskRequired
        from jane_conversation.tools import execute_tool

        # tts_announce is in SENSITIVE_ACTIONS (verified by another test below)
        # so the Step-4 trigger fires when conf < 0.7 + device_id is known.
        hass = _hass_with_mode(MODE_NORMAL)
        redis = AsyncMock()
        redis.set = AsyncMock()
        jane_data = MagicMock()
        jane_data.policies = MagicMock()
        jane_data.policies.check_permission = AsyncMock(return_value=None)
        jane_data.redis = redis
        hass.data = {"jane_conversation": jane_data}

        with pytest.raises(SpeakerAskRequired):
            await execute_tool(
                hass,
                "tts_announce",
                {"message": "x"},
                user_name="default",
                confidence=0.4,
                device_id="device-X",
                conversation_id="conv-1",
                original_request="הכריזי",
            )

    @pytest.mark.asyncio
    async def test_permission_wins_when_mode_and_confidence_allow(self):
        """tts_announce in NORMAL + high conf + role-deny from policies → role deny."""
        from jane_conversation.tools import execute_tool

        hass = _hass_with_mode(MODE_NORMAL)
        role_deny = "פעולה זו דורשת אישור מהורה"
        policies = MagicMock()
        policies.check_permission = AsyncMock(return_value=role_deny)
        jane_data = MagicMock()
        jane_data.policies = policies
        jane_data.redis = AsyncMock()
        hass.data = {"jane_conversation": jane_data}

        result = await execute_tool(
            hass,
            "tts_announce",
            {"message": "x"},
            user_name="Charlie",
            confidence=1.0,
            device_id="device-X",
        )
        assert result == role_deny


# ---------------------------------------------------------------------------
# 4. Transition logging (audit row)
# ---------------------------------------------------------------------------


class TestTransitionLogging:
    @pytest.mark.asyncio
    async def test_set_active_mode_inserts_row_with_all_fields(self):
        pool, conn = _mock_pool()
        hass = _hass_with_mode(MODE_NORMAL)

        result = await set_active_mode(
            hass,
            pool,
            new_mode=MODE_NIGHT,
            trigger="voice",
            triggered_by="Alice",
            reason="ג'יין לילה טוב",
        )
        assert result is None  # success

        # Service call to flip the entity. The household-mode entity is
        # owned by this integration as a `select` platform entity, so the
        # canonical flip service is `select.select_option`.
        hass.services.async_call.assert_awaited_once()
        args = hass.services.async_call.await_args
        assert args[0][0] == "select"
        assert args[0][1] == "select_option"
        assert args[0][2]["entity_id"] == HELPER_ENTITY_ID
        assert args[0][2]["option"] == MODE_NIGHT

        # Audit row inserted with from / to / trigger / triggered_by / reason.
        sql = conn.execute.call_args[0][0]
        assert "INSERT INTO household_mode_transitions" in sql
        params = conn.execute.call_args[0][1:]
        assert params == (MODE_NORMAL, MODE_NIGHT, "voice", "Alice", "ג'יין לילה טוב")

    @pytest.mark.asyncio
    async def test_no_op_flip_still_logs(self):
        """Same-mode flip writes an audit row anyway — captures redundant
        requests for Phase 4 Decision Log analysis.
        """
        pool, conn = _mock_pool()
        hass = _hass_with_mode(MODE_NIGHT)

        result = await set_active_mode(
            hass,
            pool,
            new_mode=MODE_NIGHT,
            trigger="voice",
            triggered_by="Alice",
            reason="redundant",
        )
        assert result is None
        # No service call — already in the right mode.
        hass.services.async_call.assert_not_awaited()
        # But audit row was still written.
        conn.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pg_failure_swallowed(self):
        """A logging miss must not break a working mode change. The user-visible
        flip is what matters; the audit row is best-effort.
        """
        pool, conn = _mock_pool()
        conn.execute.side_effect = Exception("pg down")
        hass = _hass_with_mode(MODE_NORMAL)

        result = await set_active_mode(
            hass,
            pool,
            new_mode=MODE_NIGHT,
            trigger="voice",
            triggered_by=None,
            reason=None,
        )
        # Helper still flipped, no exception bubbled up.
        assert result is None
        hass.services.async_call.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_log_transition_no_pool_is_noop(self):
        # No PG yet (very early setup) — must not raise.
        await log_transition(
            None,
            from_mode=None,
            to_mode=MODE_NIGHT,
            trigger="voice",
            triggered_by=None,
            reason=None,
        )

    @pytest.mark.asyncio
    async def test_invalid_mode_returns_deny(self):
        pool, _ = _mock_pool()
        hass = _hass_with_mode(MODE_NORMAL)
        result = await set_active_mode(
            hass,
            pool,
            new_mode="ערפילי",
            trigger="voice",
            triggered_by=None,
            reason=None,
        )
        assert result is not None
        assert "ערפילי" in result
        # Helper not flipped on validation failure.
        hass.services.async_call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_service_call_failure_returns_deny(self):
        pool, _ = _mock_pool()
        hass = _hass_with_mode(MODE_NORMAL)
        hass.services.async_call.side_effect = Exception("select unavailable")

        # Stub jane data so we can assert the flag is cleared even on failure.
        jane_data = MagicMock()
        hass.data = {"jane_conversation": jane_data}

        result = await set_active_mode(
            hass,
            pool,
            new_mode=MODE_NIGHT,
            trigger="voice",
            triggered_by=None,
            reason=None,
        )
        assert result is not None
        assert MODE_NIGHT in result
        # Flag must be cleared even on exception (try/finally) — otherwise
        # subsequent UI-direct flips would silently bypass audit logging.
        assert jane_data._mode_flip_owned_by_caller is False

    @pytest.mark.asyncio
    async def test_voice_flip_clears_ownership_flag_on_success(self):
        """After a successful voice flip, the ownership flag must be False
        so the next UI-direct flip is correctly audited by the entity.
        """
        pool, _ = _mock_pool()
        hass = _hass_with_mode(MODE_NORMAL)
        jane_data = MagicMock()
        hass.data = {"jane_conversation": jane_data}

        await set_active_mode(
            hass,
            pool,
            new_mode=MODE_NIGHT,
            trigger="voice",
            triggered_by=None,
            reason="x",
        )
        assert jane_data._mode_flip_owned_by_caller is False


# ---------------------------------------------------------------------------
# 4b. Select-entity audit-row policy (PR #56 review C2)
# ---------------------------------------------------------------------------


class TestSelectEntityAuditRow:
    """``JaneHouseholdModeSelect.async_select_option`` is called from THREE
    paths: (a) ``set_active_mode`` via the ``select.select_option`` service,
    (b) HA UI flip, (c) external automation. Only (b) and (c) should write
    an audit row from the entity itself — (a) is owned by ``set_active_mode``
    and double-logging would corrupt the table.

    The differentiator is the ``_mode_flip_owned_by_caller`` flag on
    ``hass.data[DOMAIN]``: True iff (a) is in progress.
    """

    def _make_entity(self, hass):
        # JaneHouseholdModeSelect inherits SelectEntity + RestoreEntity which
        # conftest provides as plain-class stubs. We bind the ``hass``
        # attribute the way HA would after entity registration.
        from jane_conversation.select import JaneHouseholdModeSelect

        entity = JaneHouseholdModeSelect("config-entry-id-X")
        entity.hass = hass
        entity.async_write_ha_state = MagicMock()
        return entity

    @pytest.mark.asyncio
    async def test_ui_direct_flip_logs_with_trigger_ui(self):
        """No flag set → entity treats the call as UI-direct and logs."""
        from types import SimpleNamespace

        pool, conn = _mock_pool()
        hass = MagicMock()
        # SimpleNamespace (not MagicMock) so missing attributes really are
        # missing — ``getattr(..., False)`` then returns the real default.
        jane_data = SimpleNamespace(pg_pool=pool)
        hass.data = {"jane_conversation": jane_data}

        entity = self._make_entity(hass)
        entity._attr_current_option = MODE_NORMAL  # known starting state

        await entity.async_select_option(MODE_NIGHT)

        # State updated.
        assert entity._attr_current_option == MODE_NIGHT
        # Audit row written with trigger='ui', no triggered_by, no reason.
        sql = conn.execute.call_args[0][0]
        assert "INSERT INTO household_mode_transitions" in sql
        params = conn.execute.call_args[0][1:]
        assert params == (MODE_NORMAL, MODE_NIGHT, "ui", None, None)

    @pytest.mark.asyncio
    async def test_voice_path_does_not_double_log(self):
        """Flag=True → entity skips logging; set_active_mode owns the row."""
        from types import SimpleNamespace

        pool, conn = _mock_pool()
        hass = MagicMock()
        jane_data = SimpleNamespace(pg_pool=pool, _mode_flip_owned_by_caller=True)
        hass.data = {"jane_conversation": jane_data}

        entity = self._make_entity(hass)
        entity._attr_current_option = MODE_NORMAL

        await entity.async_select_option(MODE_NIGHT)

        # State still updated — only the audit-row write is skipped.
        assert entity._attr_current_option == MODE_NIGHT
        # Crucially: no INSERT happened from the entity.
        conn.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_mode_no_state_change_no_log(self):
        from types import SimpleNamespace

        pool, conn = _mock_pool()
        hass = MagicMock()
        jane_data = SimpleNamespace(pg_pool=pool)
        hass.data = {"jane_conversation": jane_data}

        entity = self._make_entity(hass)
        entity._attr_current_option = MODE_NORMAL

        await entity.async_select_option("ערפילי")

        # No state change.
        assert entity._attr_current_option == MODE_NORMAL
        # No audit row.
        conn.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_jane_data_does_not_crash(self):
        """Pre-config-entry state shouldn't crash the entity write."""
        pool, conn = _mock_pool()
        hass = MagicMock()
        hass.data = {}  # no jane_conversation entry yet

        entity = self._make_entity(hass)
        entity._attr_current_option = MODE_NORMAL

        await entity.async_select_option(MODE_NIGHT)
        assert entity._attr_current_option == MODE_NIGHT
        # No log attempted (no pool reachable).
        conn.execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# 5. Handler — set_household_mode tool surface
# ---------------------------------------------------------------------------


class TestSetHouseholdModeHandler:
    @pytest.mark.asyncio
    async def test_happy_path_returns_hebrew_confirmation(self):
        from jane_conversation.tools.handlers.mode_tools import handle_set_household_mode

        pool, _ = _mock_pool()
        hass = _hass_with_mode(MODE_NORMAL)
        jane_data = MagicMock()
        jane_data.pg_pool = pool
        hass.data = {"jane_conversation": jane_data}

        result = await handle_set_household_mode(
            hass,
            {"mode": MODE_NIGHT, "trigger": "voice", "reason": "ג'יין לילה טוב"},
        )
        assert result == f"עברתי למצב {MODE_NIGHT}."

    @pytest.mark.asyncio
    async def test_invalid_mode_lists_valid_options(self):
        from jane_conversation.tools.handlers.mode_tools import handle_set_household_mode

        hass = _hass_with_mode(MODE_NORMAL)
        jane_data = MagicMock()
        jane_data.pg_pool = None
        hass.data = {"jane_conversation": jane_data}

        result = await handle_set_household_mode(hass, {"mode": "ערפילי"})
        # Hebrew error mentions the bad value plus the valid set.
        assert "ערפילי" in result
        for m in HOUSEHOLD_MODES:
            assert m in result

    @pytest.mark.asyncio
    async def test_pg_pool_missing_still_flips(self):
        """Mode change must work even before pg_pool is wired (early setup
        race). The helper flip is the user-visible truth; audit is best-effort.
        """
        from jane_conversation.tools.handlers.mode_tools import handle_set_household_mode

        hass = _hass_with_mode(MODE_NORMAL)
        jane_data = MagicMock()
        jane_data.pg_pool = None
        hass.data = {"jane_conversation": jane_data}

        result = await handle_set_household_mode(hass, {"mode": MODE_GUESTS})
        assert result == f"עברתי למצב {MODE_GUESTS}."
        hass.services.async_call.assert_awaited_once()

    def test_set_household_mode_is_sensitive(self):
        """D17 enforcement — mode change is a household-state mutation, child
        role on its own should not flip Jane to 'אורחים' silently.
        """
        assert "set_household_mode" in SENSITIVE_ACTIONS


# ---------------------------------------------------------------------------
# 6. build_mode_context
# ---------------------------------------------------------------------------


class TestModeContextBuilder:
    @pytest.mark.parametrize("mode", HOUSEHOLD_MODES)
    def test_block_names_mode_and_flags(self, mode: str):
        block = build_mode_context(mode)
        assert mode in block
        rules = MODE_RULES[mode]
        # The Hebrew "yes/no" labels must reflect the rule flags.
        if rules["tts"]:
            assert "מותר להכריז בקול: כן" in block
        else:
            assert "מותר להכריז בקול: לא" in block
        if rules["proactive"]:
            assert "יזימה: כן" in block
        else:
            assert "יזימה: לא" in block

    def test_unknown_mode_falls_back_to_normal_block(self):
        # Defensive: an out-of-set mode (shouldn't happen, but…) renders
        # NORMAL's behavior so the LLM doesn't see an empty/blank block.
        block = build_mode_context("ערפילי")
        assert MODE_RULES[MODE_NORMAL]["behavior"] in block


# ---------------------------------------------------------------------------
# 7. get_active_mode fallbacks
# ---------------------------------------------------------------------------


class TestGetActiveMode:
    def test_returns_state_value_when_valid(self):
        hass = _hass_with_mode(MODE_WORK)
        assert get_active_mode(hass) == MODE_WORK

    def test_missing_state_returns_normal(self):
        hass = _hass_with_mode(None)
        assert get_active_mode(hass) == MODE_NORMAL

    @pytest.mark.parametrize("bad_value", ["unknown", "unavailable", "", "ערפילי"])
    def test_invalid_state_returns_normal(self, bad_value: str):
        hass = _hass_with_mode(bad_value)
        assert get_active_mode(hass) == MODE_NORMAL


# ---------------------------------------------------------------------------
# 8. Schema-surface assertions (idempotent DDL — read-side only)
# ---------------------------------------------------------------------------


class TestSchemaSurface:
    """We don't run a real PG in CI; the contract here is that scripts/schema.sql
    contains the right idempotent DDL for the new tables/columns. A grep-style
    assertion is enough to catch accidental deletion or rename in code review.
    """

    @pytest.fixture
    def schema_sql(self) -> str:
        from pathlib import Path

        return Path(__file__).resolve().parents[1].joinpath("scripts", "schema.sql").read_text(encoding="utf-8")

    def test_household_mode_transitions_idempotent(self, schema_sql: str):
        assert "CREATE TABLE IF NOT EXISTS household_mode_transitions" in schema_sql
        # The reason TEXT column is what makes Phase 4 Decision Log richer —
        # a previous draft omitted it; pin it here.
        assert "reason TEXT" in schema_sql

    def test_user_overrides_schema_only(self, schema_sql: str):
        assert "CREATE TABLE IF NOT EXISTS user_overrides" in schema_sql
        # CHECK on override_type — defending against a future writer using a
        # free-form value instead of the documented enum.
        assert "override_type" in schema_sql
        assert "dismissed" in schema_sql
        assert "reversed" in schema_sql
        assert "corrected" in schema_sql

    def test_routines_scope_added_idempotently(self, schema_sql: str):
        assert "ALTER TABLE routines ADD COLUMN IF NOT EXISTS scope" in schema_sql
        assert "DEFAULT 'shared'" in schema_sql
        assert "CHECK (scope IN ('personal', 'shared'))" in schema_sql
