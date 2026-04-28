"""Tests for tools.py — Config Store API, tool format, routing."""

import pytest

from jane_conversation.config import normalize_config_keys
from jane_conversation.tools import (
    _ALL_FUNCTION_DECLARATIONS,
    execute_tool,
    get_tools,
    get_tools_minimal,
)

# ---------------------------------------------------------------------------
# Config Key Normalization (plural → singular)
# ---------------------------------------------------------------------------


class TestNormalizeConfigKeys:
    """Test normalize_config_keys for REST API format."""

    def test_triggers_to_trigger(self):
        config = {"alias": "Test", "triggers": [{"platform": "time"}]}
        result = normalize_config_keys(config)
        assert "trigger" in result
        assert "triggers" not in result

    def test_actions_to_action(self):
        config = {"alias": "Test", "actions": [{"service": "light.turn_on"}]}
        result = normalize_config_keys(config)
        assert "action" in result
        assert "actions" not in result

    def test_conditions_to_condition(self):
        config = {"alias": "Test", "conditions": [{"condition": "time"}]}
        result = normalize_config_keys(config)
        assert "condition" in result
        assert "conditions" not in result

    def test_singular_keys_unchanged(self):
        config = {"alias": "Test", "trigger": [{"platform": "time"}], "action": []}
        result = normalize_config_keys(config)
        assert result["trigger"] == [{"platform": "time"}]
        assert result["action"] == []

    def test_does_not_overwrite_existing_singular(self):
        """If both plural and singular exist, keep the singular."""
        config = {"trigger": [{"platform": "time"}], "triggers": [{"platform": "state"}]}
        result = normalize_config_keys(config)
        assert result["trigger"] == [{"platform": "time"}]
        assert "triggers" in result  # Not removed if singular already exists

    def test_preserves_other_keys(self):
        config = {"alias": "Test", "mode": "single", "description": "A test"}
        result = normalize_config_keys(config)
        assert result["alias"] == "Test"
        assert result["mode"] == "single"
        assert result["description"] == "A test"


# ---------------------------------------------------------------------------
# Tool Format Validation
# ---------------------------------------------------------------------------


class TestToolFormat:
    def test_all_tools_have_name(self):
        for tool in _ALL_FUNCTION_DECLARATIONS:
            assert "name" in tool, f"Tool missing 'name': {tool}"

    def test_all_tools_have_description(self):
        for tool in _ALL_FUNCTION_DECLARATIONS:
            assert "description" in tool, f"Tool {tool.get('name')} missing 'description'"

    def test_all_tools_have_parameters(self):
        for tool in _ALL_FUNCTION_DECLARATIONS:
            assert "parameters" in tool, f"Tool {tool.get('name')} has 'input_schema' instead of 'parameters'"

    def test_no_tool_has_input_schema(self):
        """Ensure no leftover Anthropic format."""
        for tool in _ALL_FUNCTION_DECLARATIONS:
            assert "input_schema" not in tool, f"Tool {tool.get('name')} still has 'input_schema'"

    def test_no_tool_has_type_function(self):
        """Ensure no leftover OpenAI format."""
        for tool in _ALL_FUNCTION_DECLARATIONS:
            assert "type" not in tool or tool.get("type") != "function", (
                f"Tool {tool.get('name')} still has OpenAI 'type: function'"
            )

    def test_tool_count(self):
        assert len(_ALL_FUNCTION_DECLARATIONS) >= 32

    def test_unique_tool_names(self):
        names = [t["name"] for t in _ALL_FUNCTION_DECLARATIONS]
        assert len(names) == len(set(names)), f"Duplicate names: {[n for n in names if names.count(n) > 1]}"

    def test_get_tools_returns_gemini_tool_objects(self):
        tools = get_tools()
        from google.genai import types

        assert all(isinstance(t, types.Tool) for t in tools)

    def test_get_tools_minimal_has_3_declarations(self):
        tools = get_tools_minimal()
        declarations = tools[0].function_declarations
        names = [d.name if hasattr(d, "name") else d["name"] for d in declarations]
        assert "save_memory" in names
        assert "read_memory" in names
        assert "search_web" in names


# ---------------------------------------------------------------------------
# Tool Routing
# ---------------------------------------------------------------------------


class TestToolRouting:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, hass_mock):
        result = await execute_tool(hass_mock, "nonexistent_tool", {})
        assert "Unknown tool" in result

    @pytest.mark.asyncio
    async def test_get_entity_state_routes(self, hass_mock):
        result = await execute_tool(hass_mock, "get_entity_state", {"entity_id": "light.living_room"})
        assert "אור סלון" in result or "on" in result

    @pytest.mark.asyncio
    async def test_get_entity_state_not_found(self, hass_mock):
        result = await execute_tool(hass_mock, "get_entity_state", {"entity_id": "light.nonexistent"})
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_search_entities_finds_match(self, hass_mock):
        result = await execute_tool(hass_mock, "search_entities", {"query": "סלון"})
        assert "light.living_room" in result

    @pytest.mark.asyncio
    async def test_search_entities_no_match(self, hass_mock):
        result = await execute_tool(hass_mock, "search_entities", {"query": "nonexistent_xyz"})
        assert "No entities found" in result


# ---------------------------------------------------------------------------
# S3.0 (JANE-71) — Confidence gate at the dispatch boundary
# ---------------------------------------------------------------------------


class TestConfidenceGate:
    """Sensitive/personal-data tools must route through `policies.check_permission`
    with the speaker's resolved confidence. A deny string is returned to the LLM
    as the tool result; high confidence passes through to the real handler.
    """

    @pytest.mark.asyncio
    async def test_sensitive_tool_denied_at_low_confidence(self, hass_mock):
        from unittest.mock import AsyncMock, MagicMock

        deny_msg = "זיהוי לא בטוח — אנא אשר את הפעולה"
        policies = MagicMock()
        policies.check_permission = AsyncMock(return_value=deny_msg)
        jane_data = MagicMock()
        jane_data.policies = policies
        hass_mock.data = {"jane_conversation": jane_data}

        result = await execute_tool(
            hass_mock,
            "set_automation",
            {"object_id": "x", "config": {}},
            user_name="Alice",
            confidence=0.3,
        )
        assert result == deny_msg
        policies.check_permission.assert_awaited_once_with("Alice", "set_automation", confidence=0.3)

    @pytest.mark.asyncio
    async def test_personal_data_tool_denied_at_low_confidence(self, hass_mock):
        from unittest.mock import AsyncMock, MagicMock

        deny_msg = "זיהוי לא בטוח — אני לא משתפת מידע אישי כרגע"
        policies = MagicMock()
        policies.check_permission = AsyncMock(return_value=deny_msg)
        jane_data = MagicMock()
        jane_data.policies = policies
        hass_mock.data = {"jane_conversation": jane_data}

        result = await execute_tool(
            hass_mock,
            "tts_announce",
            {"message": "hi"},
            user_name="Alice",
            confidence=0.4,
        )
        assert result == deny_msg

    @pytest.mark.asyncio
    async def test_high_confidence_passes_gate_to_handler(self, hass_mock):
        from unittest.mock import AsyncMock, MagicMock

        policies = MagicMock()
        policies.check_permission = AsyncMock(return_value=None)  # allowed
        jane_data = MagicMock()
        jane_data.policies = policies
        hass_mock.data = {"jane_conversation": jane_data}

        result = await execute_tool(
            hass_mock,
            "forget_memory",
            {"target": "preferences", "key": {"person": "Alice", "key": "music_taste"}},
            user_name="Alice",
            confidence=0.95,
        )
        # Gate passed — handler ran. Result is whatever forget_memory returns
        # (could be noop/error/etc), but it must NOT be a deny string.
        assert "אנא אשר" not in result and "מידע אישי" not in result
        policies.check_permission.assert_awaited_once_with("Alice", "forget_memory", confidence=0.95)

    @pytest.mark.asyncio
    async def test_non_sensitive_tool_skips_gate(self, hass_mock):
        """Tools not in SENSITIVE/PERSONAL_DATA never call the gate, even at low conf."""
        from unittest.mock import AsyncMock, MagicMock

        policies = MagicMock()
        policies.check_permission = AsyncMock(return_value="should never fire")
        jane_data = MagicMock()
        jane_data.policies = policies
        hass_mock.data = {"jane_conversation": jane_data}

        result = await execute_tool(
            hass_mock,
            "get_entity_state",
            {"entity_id": "light.living_room"},
            user_name="Alice",
            confidence=0.1,
        )
        assert "אור סלון" in result or "on" in result
        policies.check_permission.assert_not_called()

    @pytest.mark.asyncio
    async def test_low_conf_blocks_read_memory(self, hass_mock):
        """Lock the gate on `read_memory` — it reads preferences/memory_entries
        keyed by user_name; at low confidence we must not leak personal data.
        """
        from unittest.mock import AsyncMock, MagicMock

        deny_msg = "זיהוי לא בטוח — אני לא משתפת מידע אישי כרגע"
        policies = MagicMock()
        policies.check_permission = AsyncMock(return_value=deny_msg)
        jane_data = MagicMock()
        jane_data.policies = policies
        hass_mock.data = {"jane_conversation": jane_data}

        result = await execute_tool(
            hass_mock,
            "read_memory",
            {"category": "preferences", "user_name": "Alice"},
            user_name="Alice",
            confidence=0.4,
        )
        assert result == deny_msg

    @pytest.mark.asyncio
    async def test_low_conf_blocks_query_history(self, hass_mock):
        """`query_history` reads recent conversation events — same gate."""
        from unittest.mock import AsyncMock, MagicMock

        deny_msg = "זיהוי לא בטוח — אני לא משתפת מידע אישי כרגע"
        policies = MagicMock()
        policies.check_permission = AsyncMock(return_value=deny_msg)
        jane_data = MagicMock()
        jane_data.policies = policies
        hass_mock.data = {"jane_conversation": jane_data}

        result = await execute_tool(
            hass_mock,
            "query_history",
            {"q": "what did Alice say"},
            user_name="Alice",
            confidence=0.4,
        )
        assert result == deny_msg

    @pytest.mark.asyncio
    async def test_low_conf_blocks_save_memory(self, hass_mock):
        """`save_memory` writes memory under user_name — wrong-attribution risk at low conf."""
        from unittest.mock import AsyncMock, MagicMock

        deny_msg = "זיהוי לא בטוח — אני לא משתפת מידע אישי כרגע"
        policies = MagicMock()
        policies.check_permission = AsyncMock(return_value=deny_msg)
        jane_data = MagicMock()
        jane_data.policies = policies
        hass_mock.data = {"jane_conversation": jane_data}

        result = await execute_tool(
            hass_mock,
            "save_memory",
            {"category": "preferences", "content": "loves jazz"},
            user_name="default",
            confidence=0.3,
        )
        assert result == deny_msg

    @pytest.mark.asyncio
    async def test_gate_failure_closed_to_allow(self, hass_mock):
        """If the gate raises, dispatch must allow (we don't brick all tools on a buggy gate)."""
        from unittest.mock import AsyncMock, MagicMock

        policies = MagicMock()
        policies.check_permission = AsyncMock(side_effect=RuntimeError("policy store down"))
        jane_data = MagicMock()
        jane_data.policies = policies
        hass_mock.data = {"jane_conversation": jane_data}

        result = await execute_tool(
            hass_mock,
            "set_automation",
            {"object_id": "x", "config": {}},
            user_name="Alice",
            confidence=0.3,
        )
        # Got past the gate — result is whatever set_automation returns; must NOT be a deny string.
        assert "אנא אשר" not in result
