"""Tests for PolicyStore (S1.5 — Policy Memory)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from jane_conversation.memory.policy import PolicyStore


@pytest.fixture
def mock_pool():
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn


@pytest.fixture
def store(mock_pool):
    pool, _ = mock_pool
    return PolicyStore(pool)


class TestSavePolicy:
    @pytest.mark.asyncio
    async def test_upserts_with_on_conflict(self, store, mock_pool):
        _, conn = mock_pool
        await store.save_policy("יאיר", "role", "admin")

        sql = conn.execute.call_args[0][0]
        assert "INSERT INTO policies" in sql
        assert "ON CONFLICT (person_name, key) DO UPDATE" in sql
        assert conn.execute.call_args[0][1] == "יאיר"
        assert conn.execute.call_args[0][2] == "role"
        assert conn.execute.call_args[0][3] == "admin"


class TestLoadPolicies:
    @pytest.mark.asyncio
    async def test_returns_dict(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = [
            {"key": "role", "value": "admin"},
            {"key": "quiet_hours_start", "value": "23:00"},
        ]

        result = await store.load_policies("יאיר")
        assert result == {"role": "admin", "quiet_hours_start": "23:00"}

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_policies(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = []

        result = await store.load_policies("unknown")
        assert result == {}


class TestCheckPermission:
    @pytest.mark.asyncio
    async def test_admin_allowed(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = [{"key": "role", "value": "admin"}]

        result = await store.check_permission("יאיר", "set_automation")
        assert result is None  # allowed

    @pytest.mark.asyncio
    async def test_child_denied_sensitive(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = [{"key": "role", "value": "child"}]

        result = await store.check_permission("אלון", "set_automation")
        assert result is not None
        assert "אישור" in result

    @pytest.mark.asyncio
    async def test_child_allowed_non_sensitive(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = [{"key": "role", "value": "child"}]

        result = await store.check_permission("אלון", "get_entity_state")
        assert result is None  # allowed

    @pytest.mark.asyncio
    async def test_quiet_hours_denied(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = [
            {"key": "role", "value": "admin"},
            {"key": "quiet_hours_start", "value": "00:00"},
            {"key": "quiet_hours_end", "value": "23:59"},
        ]

        result = await store.check_permission("יאיר", "tts")
        assert result is not None
        assert "שקט" in result

    @pytest.mark.asyncio
    async def test_no_policies_defaults_to_admin(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = []

        result = await store.check_permission("new_user", "set_automation")
        assert result is None  # admin by default


class TestBuildPolicyContext:
    @pytest.mark.asyncio
    async def test_formats_policies(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = [
            {"key": "role", "value": "admin"},
            {"key": "quiet_hours_start", "value": "23:00"},
            {"key": "quiet_hours_end", "value": "07:00"},
        ]

        result = await store.build_policy_context("יאיר")
        assert "admin" in result
        assert "23:00–07:00" in result

    @pytest.mark.asyncio
    async def test_empty_when_no_policies(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = []

        result = await store.build_policy_context("unknown")
        assert result == ""


class TestSeedDefaults:
    @pytest.mark.asyncio
    async def test_seeds_admin_for_adults(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = []  # no existing policies

        persons = [{"name": "יאיר", "role": "parent"}, {"name": "אלון", "role": "child"}]
        count = await store.seed_defaults(persons)
        assert count == 2

        # Check the calls — should save role for each
        calls = conn.execute.call_args_list
        # First person: admin
        assert calls[0][0][2] == "role"
        assert calls[0][0][3] == "admin"
        # Second person: child
        assert calls[1][0][2] == "role"
        assert calls[1][0][3] == "child"

    @pytest.mark.asyncio
    async def test_skips_existing_policies(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = [{"key": "role", "value": "admin"}]

        persons = [{"name": "יאיר", "role": "parent"}]
        count = await store.seed_defaults(persons)
        assert count == 0  # already has role
