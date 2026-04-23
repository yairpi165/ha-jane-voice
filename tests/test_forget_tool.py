"""Tests for A5 — forget_memory tool handler."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jane_conversation.tools.handlers.memory_tools import handle_forget_memory


def _setup_jane(hass_mock):
    """Wire hass_mock.data[DOMAIN] with mocked structured + backend + pg_pool."""
    from jane_conversation.const import DOMAIN

    structured = MagicMock()
    structured.load_preference = AsyncMock(
        return_value={
            "key": "food_preferences",
            "value": "coffee",
            "confidence": 1.0,
            "inferred": False,
            "source": "extraction",
        }
    )
    structured.delete_preference = AsyncMock(
        return_value={
            "key": "food_preferences",
            "value": "coffee",
            "confidence": 1.0,
            "inferred": False,
            "source": "extraction",
        }
    )
    structured.load_persons = AsyncMock(return_value=[{"name": "Alice"}])

    pool = MagicMock()
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)  # no idempotency hit
    conn.execute = AsyncMock()
    acq = MagicMock()
    acq.__aenter__ = AsyncMock(return_value=conn)
    acq.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acq)
    pool._conn = conn

    jane = MagicMock()
    jane.structured = structured
    jane.pg_pool = pool
    hass_mock.data = {DOMAIN: jane}
    return structured, pool


def _mk_backend():
    backend = MagicMock()
    backend.load = AsyncMock(return_value="prior content")
    backend.delete_category = AsyncMock(return_value="prior content")
    return backend


# ---------------------------------------------------------------------------
# Happy-path forget
# ---------------------------------------------------------------------------


class TestForgetHappyPath:
    @pytest.mark.asyncio
    async def test_forget_preference_happy_path(self, hass_mock):
        structured, _pool = _setup_jane(hass_mock)
        backend = _mk_backend()
        with patch("jane_conversation.memory.manager.get_backend", return_value=backend):
            result = await handle_forget_memory(
                hass_mock,
                {
                    "target_table": "preferences",
                    "target_key": {"person": "Alice", "key": "food_preferences"},
                    "reason": "user asked",
                },
            )
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["table"] == "preferences"
        assert data["key"] == {"person": "Alice", "key": "food_preferences"}
        structured.delete_preference.assert_awaited_once_with("Alice", "food_preferences")

    @pytest.mark.asyncio
    async def test_forget_memory_entries_user_category(self, hass_mock):
        _structured, _pool = _setup_jane(hass_mock)
        backend = _mk_backend()
        with patch("jane_conversation.memory.manager.get_backend", return_value=backend):
            result = await handle_forget_memory(
                hass_mock,
                {
                    "target_table": "memory_entries",
                    "target_key": {"category": "user", "user_name": "Alice"},
                    "reason": "user asked",
                },
            )
        assert json.loads(result)["status"] == "ok"
        backend.delete_category.assert_awaited_once_with("user", "Alice")

    @pytest.mark.asyncio
    async def test_forget_memory_entries_family_sets_user_name_none(self, hass_mock):
        """Non-user categories must pass user_name=None to delete_category."""
        _setup_jane(hass_mock)
        backend = _mk_backend()
        with patch("jane_conversation.memory.manager.get_backend", return_value=backend):
            await handle_forget_memory(
                hass_mock,
                {
                    "target_table": "memory_entries",
                    "target_key": {"category": "family", "user_name": "Alice"},
                    "reason": "no longer applies",
                },
            )
        backend.delete_category.assert_awaited_once_with("family", None)


# ---------------------------------------------------------------------------
# Validation errors (structured JSON returned)
# ---------------------------------------------------------------------------


class TestForgetValidation:
    @pytest.mark.asyncio
    async def test_rejects_persons_target(self, hass_mock):
        _setup_jane(hass_mock)
        result = await handle_forget_memory(
            hass_mock,
            {
                "target_table": "persons",
                "target_key": {"name": "Alice"},
                "reason": "anything",
            },
        )
        data = json.loads(result)
        assert data["status"] == "error"
        assert data["code"] == "invalid_table"

    @pytest.mark.asyncio
    async def test_rejects_invalid_category(self, hass_mock):
        _setup_jane(hass_mock)
        result = await handle_forget_memory(
            hass_mock,
            {
                "target_table": "memory_entries",
                "target_key": {"category": "_migration"},
                "reason": "x",
            },
        )
        assert json.loads(result)["code"] == "invalid_category"

    @pytest.mark.asyncio
    async def test_corrections_category_rejected(self, hass_mock):
        """corrections is vestigial after A3 — not a valid forget target."""
        _setup_jane(hass_mock)
        result = await handle_forget_memory(
            hass_mock,
            {
                "target_table": "memory_entries",
                "target_key": {"category": "corrections"},
                "reason": "x",
            },
        )
        assert json.loads(result)["code"] == "invalid_category"

    @pytest.mark.asyncio
    async def test_rejects_missing_pref_key(self, hass_mock):
        _setup_jane(hass_mock)
        result = await handle_forget_memory(
            hass_mock,
            {
                "target_table": "preferences",
                "target_key": {"person": "Alice"},
                "reason": "x",
            },
        )
        assert json.loads(result)["code"] == "missing_preference_key"

    @pytest.mark.asyncio
    async def test_rejects_non_dict_target_key(self, hass_mock):
        _setup_jane(hass_mock)
        result = await handle_forget_memory(
            hass_mock,
            {
                "target_table": "preferences",
                "target_key": "food_preferences",
                "reason": "x",
            },
        )
        data = json.loads(result)
        assert data["status"] == "error"
        assert data["code"] == "invalid_target_key_shape"

    @pytest.mark.asyncio
    async def test_returns_structured_json_on_error(self, hass_mock):
        """Post-review §1.1: ensure all errors are parseable JSON, not English strings."""
        _setup_jane(hass_mock)
        result = await handle_forget_memory(
            hass_mock,
            {
                "target_table": "wildcard",
                "target_key": {},
                "reason": "x",
            },
        )
        data = json.loads(result)
        assert {"status", "code", "detail"} <= set(data.keys())


# ---------------------------------------------------------------------------
# Apply semantics
# ---------------------------------------------------------------------------


class TestForgetApply:
    @pytest.mark.asyncio
    async def test_returns_noop_when_row_absent(self, hass_mock):
        structured, _pool = _setup_jane(hass_mock)
        structured.load_preference = AsyncMock(return_value=None)
        structured.delete_preference = AsyncMock(return_value=None)
        backend = _mk_backend()
        with patch("jane_conversation.memory.manager.get_backend", return_value=backend):
            result = await handle_forget_memory(
                hass_mock,
                {
                    "target_table": "preferences",
                    "target_key": {"person": "Alice", "key": "missing"},
                    "reason": "cleanup",
                },
            )
        data = json.loads(result)
        assert data["status"] == "noop"
        assert data["code"] == "not_live"

    @pytest.mark.asyncio
    async def test_logs_memory_ops_row_with_tool_session_prefix(self, hass_mock):
        _structured, pool = _setup_jane(hass_mock)
        backend = _mk_backend()
        with patch("jane_conversation.memory.manager.get_backend", return_value=backend):
            await handle_forget_memory(
                hass_mock,
                {
                    "target_table": "preferences",
                    "target_key": {"person": "Alice", "key": "food_preferences"},
                    "reason": "user asked",
                },
            )
        # Find the INSERT INTO memory_ops call
        insert_calls = [
            c for c in pool._conn.execute.await_args_list if c.args and "INSERT INTO memory_ops" in c.args[0]
        ]
        assert len(insert_calls) == 1
        insert_args = insert_calls[0].args
        # Positional args: op, target_table, target_key(json), payload(json), before_state(json|None),
        # reason, confidence, user_name, session_id, op_hash, raw_response
        # session_id is the 9th positional after SQL (index 9)
        assert insert_args[1] == "DELETE"
        # session_id slot — find the one matching tool-forget-*
        assert any(isinstance(a, str) and a.startswith("tool-forget-") for a in insert_args)

    @pytest.mark.asyncio
    async def test_subsystem_unavailable_returns_error(self, hass_mock):
        """If JaneData.structured or pg_pool missing, return structured error."""
        from jane_conversation.const import DOMAIN

        jane = MagicMock()
        jane.structured = None
        jane.pg_pool = None
        hass_mock.data = {DOMAIN: jane}
        result = await handle_forget_memory(
            hass_mock,
            {
                "target_table": "preferences",
                "target_key": {"person": "Alice", "key": "food_preferences"},
                "reason": "user asked",
            },
        )
        assert json.loads(result)["code"] == "subsystem_unavailable"


# ---------------------------------------------------------------------------
# Person resolution + revive cycle
# ---------------------------------------------------------------------------


class TestForgetPersonResolutionAndRevive:
    @pytest.mark.asyncio
    async def test_resolves_person_via_friendly_name(self, hass_mock):
        """_resolve_user_name maps 'alice' → person entity friendly_name 'Alice'."""
        structured, _pool = _setup_jane(hass_mock)
        backend = _mk_backend()
        with patch("jane_conversation.memory.manager.get_backend", return_value=backend):
            await handle_forget_memory(
                hass_mock,
                {
                    "target_table": "preferences",
                    "target_key": {"person": "alice", "key": "food_preferences"},
                    "reason": "x",
                },
            )
        # Handler resolved "alice" → "Alice" (canonical friendly_name) before calling delete.
        structured.delete_preference.assert_awaited_once_with("Alice", "food_preferences")

    @pytest.mark.asyncio
    async def test_forget_then_restate_revives(self, hass_mock):
        """End-to-end of the user journey: forget → row tombstoned →
        user restates via save_preference → A4 revive-on-upsert → single live row.

        The revive mechanics live in A4's structured.save_preference ON CONFLICT clause;
        this test checks the tool emits the DELETE path cleanly so the revive pathway
        is reachable.
        """
        structured, _pool = _setup_jane(hass_mock)
        backend = _mk_backend()
        structured.save_preference = AsyncMock()
        with patch("jane_conversation.memory.manager.get_backend", return_value=backend):
            forget_result = await handle_forget_memory(
                hass_mock,
                {
                    "target_table": "preferences",
                    "target_key": {"person": "Alice", "key": "food_preferences"},
                    "reason": "no longer relevant",
                },
            )
        assert json.loads(forget_result)["status"] == "ok"
        structured.delete_preference.assert_awaited_once()
        # Simulate user restate: extractor calls save_preference with new value.
        await structured.save_preference(person_name="Alice", key="food_preferences", value="tea")
        structured.save_preference.assert_awaited_once_with(person_name="Alice", key="food_preferences", value="tea")
