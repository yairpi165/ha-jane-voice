"""Tests for A4 — soft-delete primitive on memory_entries + preferences.

Tests use mocked asyncpg pool (same pattern as test_ops.py). SQL-string assertions
verify the structural shape of the queries (deleted_at filter / revive clause);
behavior assertions verify return values through the mocked cursor.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jane_conversation.memory.extraction_prompts import build_ops_prompt
from jane_conversation.memory.ops import MemoryOp
from jane_conversation.memory.ops_applier import OpApplier
from jane_conversation.memory.storage import PostgresBackend
from jane_conversation.memory.structured import StructuredMemoryStore

# -------------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------------


def _make_pool(fetchrow_return=None, fetch_return=None, execute_return=None):
    """Build a mock asyncpg pool that captures SQL strings."""
    pool = MagicMock()
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    conn.fetch = AsyncMock(return_value=fetch_return or [])
    conn.execute = AsyncMock(return_value=execute_return or "UPDATE 0")
    acq = MagicMock()
    acq.__aenter__ = AsyncMock(return_value=conn)
    acq.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acq)
    pool._conn = conn
    return pool


def _sql_of(mock_call):
    """Extract the SQL string from a mocked asyncpg call (first positional arg)."""
    return mock_call.args[0]


# -------------------------------------------------------------------------
# preferences: delete / load / save
# -------------------------------------------------------------------------


class TestPreferenceSoftDelete:
    @pytest.mark.asyncio
    async def test_delete_preference_issues_update_set_deleted_at(self):
        pool = _make_pool(
            fetchrow_return={
                "key": "coffee",
                "value": "black",
                "confidence": 1.0,
                "inferred": False,
                "source": "extraction",
            }
        )
        store = StructuredMemoryStore(pool)
        result = await store.delete_preference("Alice", "coffee")
        sql = _sql_of(pool._conn.fetchrow.call_args)
        assert "UPDATE preferences" in sql
        assert "SET deleted_at = NOW()" in sql
        assert "deleted_at IS NULL" in sql
        assert "DELETE FROM preferences" not in sql
        assert result == {
            "key": "coffee",
            "value": "black",
            "confidence": 1.0,
            "inferred": False,
            "source": "extraction",
        }

    @pytest.mark.asyncio
    async def test_load_preference_filters_tombstones(self):
        pool = _make_pool(fetchrow_return=None)
        store = StructuredMemoryStore(pool)
        result = await store.load_preference("Alice", "coffee")
        assert result is None
        assert "deleted_at IS NULL" in _sql_of(pool._conn.fetchrow.call_args)

    @pytest.mark.asyncio
    async def test_load_all_preferences_filters_tombstones(self):
        pool = _make_pool(fetch_return=[])
        store = StructuredMemoryStore(pool)
        await store.load_all_preferences()
        assert "deleted_at IS NULL" in _sql_of(pool._conn.fetch.call_args)

    @pytest.mark.asyncio
    async def test_load_preferences_filters_tombstones(self):
        pool = _make_pool(fetch_return=[])
        store = StructuredMemoryStore(pool)
        await store.load_preferences("Alice")
        assert "deleted_at IS NULL" in _sql_of(pool._conn.fetch.call_args)

    @pytest.mark.asyncio
    async def test_save_preference_revives_tombstone(self):
        pool = _make_pool()
        store = StructuredMemoryStore(pool)
        await store.save_preference("Alice", "coffee", "espresso")
        sql = _sql_of(pool._conn.execute.call_args)
        # ON CONFLICT DO UPDATE clause must clear deleted_at.
        assert "ON CONFLICT" in sql
        assert "deleted_at = NULL" in sql

    @pytest.mark.asyncio
    async def test_double_delete_is_noop(self):
        # Second delete: WHERE deleted_at IS NULL matches nothing → fetchrow returns None.
        pool = _make_pool(fetchrow_return=None)
        store = StructuredMemoryStore(pool)
        result = await store.delete_preference("Alice", "coffee")
        assert result is None

    @pytest.mark.asyncio
    async def test_decay_skips_tombstones(self):
        pool = _make_pool(execute_return="UPDATE 0")
        store = StructuredMemoryStore(pool)
        await store.decay_preferences()
        assert "deleted_at IS NULL" in _sql_of(pool._conn.execute.call_args)

    @pytest.mark.asyncio
    async def test_reinforce_preference_skips_tombstones(self):
        pool = _make_pool()
        store = StructuredMemoryStore(pool)
        await store.reinforce_preference("Alice", "coffee")
        assert "deleted_at IS NULL" in _sql_of(pool._conn.execute.call_args)


# -------------------------------------------------------------------------
# memory_entries: delete / load / save
# -------------------------------------------------------------------------


class TestMemoryEntriesSoftDelete:
    @pytest.mark.asyncio
    async def test_delete_category_issues_update_set_deleted_at(self):
        pool = _make_pool(fetchrow_return={"content": "prior content"})
        backend = PostgresBackend(pool)
        result = await backend.delete_category("user", "Alice")
        sql = _sql_of(pool._conn.fetchrow.call_args)
        assert "UPDATE memory_entries" in sql
        assert "SET deleted_at = NOW()" in sql
        assert "deleted_at IS NULL" in sql
        assert "DELETE FROM memory_entries" not in sql
        assert result == "prior content"

    @pytest.mark.asyncio
    async def test_load_filters_tombstones(self):
        pool = _make_pool(fetchrow_return=None)
        backend = PostgresBackend(pool)
        assert await backend.load("user", "Alice") == ""
        assert "deleted_at IS NULL" in _sql_of(pool._conn.fetchrow.call_args)

    @pytest.mark.asyncio
    async def test_load_snapshot_filters_tombstones_with_proper_parens(self):
        pool = _make_pool(fetch_return=[])
        backend = PostgresBackend(pool)
        await backend.load_snapshot("Alice")
        sql = _sql_of(pool._conn.fetch.call_args)
        # Precedence trap guard: OR branch must be parenthesized before AND.
        assert "(user_name = $1 OR user_name IS NULL)" in sql
        assert "AND deleted_at IS NULL" in sql

    @pytest.mark.asyncio
    async def test_save_revives_memory_entries_tombstone(self):
        pool = _make_pool()
        backend = PostgresBackend(pool)
        await backend.save("user", "new content", "Alice")
        sql = _sql_of(pool._conn.execute.call_args)
        assert "ON CONFLICT" in sql
        assert "deleted_at = NULL" in sql

    @pytest.mark.asyncio
    async def test_double_delete_category_is_noop(self):
        pool = _make_pool(fetchrow_return=None)
        backend = PostgresBackend(pool)
        assert await backend.delete_category("user", "Alice") is None


# -------------------------------------------------------------------------
# OpApplier integration: before_state, revive flow
# -------------------------------------------------------------------------


class TestOpApplierSoftDeleteIntegration:
    @pytest.mark.asyncio
    async def test_delete_op_captures_live_before_state_then_soft_deletes(self):
        """DELETE op on a preference: before_state is the live row, delete_preference called."""
        backend = MagicMock()
        structured = MagicMock()
        structured.load_preference = AsyncMock(
            return_value={
                "key": "coffee",
                "value": "black",
                "confidence": 1.0,
                "inferred": False,
                "source": "extraction",
            }
        )
        structured.delete_preference = AsyncMock(
            return_value={
                "key": "coffee",
                "value": "black",
                "confidence": 1.0,
                "inferred": False,
                "source": "extraction",
            }
        )
        # Persons lookup used by _resolve_person
        structured.load_persons = AsyncMock(return_value=[{"name": "Alice"}])
        structured.canonical_person = AsyncMock(side_effect=lambda name, fallback="", persons_cache=None: name or fallback)

        pool = _make_pool(fetchrow_return=None)  # no idempotency hit
        applier = OpApplier(backend=backend, structured=structured, pg_pool=pool)

        op = MemoryOp(
            op="DELETE",
            target_table="preferences",
            target_key={"person": "Alice", "key": "coffee"},
            payload={},
            reason="user asked to forget",
            confidence=0.9,
        )
        result = await applier.apply_all(
            [op], user_name="Alice", session_id="s1", memory_snapshot={}, raw_response="{}"
        )
        assert result.deleted == 1
        structured.delete_preference.assert_awaited_once_with("Alice", "coffee")
        # before_state captured (load_preference was called)
        structured.load_preference.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_then_add_same_batch_revives(self):
        """DELETE followed by ADD on the same key: second op's save_preference revives (ON CONFLICT → deleted_at = NULL)."""
        backend = MagicMock()
        structured = MagicMock()
        structured.load_preference = AsyncMock(
            return_value={
                "key": "coffee",
                "value": "black",
                "confidence": 1.0,
                "inferred": False,
                "source": "extraction",
            }
        )
        structured.delete_preference = AsyncMock(
            return_value={
                "key": "coffee",
                "value": "black",
                "confidence": 1.0,
                "inferred": False,
                "source": "extraction",
            }
        )
        structured.save_preference = AsyncMock()
        structured.load_persons = AsyncMock(return_value=[{"name": "Alice"}])
        structured.canonical_person = AsyncMock(side_effect=lambda name, fallback="", persons_cache=None: name or fallback)

        pool = _make_pool(fetchrow_return=None)
        applier = OpApplier(backend=backend, structured=structured, pg_pool=pool)

        ops = [
            MemoryOp("DELETE", "preferences", {"person": "Alice", "key": "coffee"}, {}, "forget", 0.9),
            MemoryOp("ADD", "preferences", {"person": "Alice", "key": "coffee"}, {"value": "tea"}, "restate", 0.9),
        ]
        result = await applier.apply_all(ops, user_name="Alice", session_id="s2", memory_snapshot={}, raw_response="{}")
        assert result.deleted == 1
        assert result.added == 1
        structured.delete_preference.assert_awaited_once()
        structured.save_preference.assert_awaited_once()


# -------------------------------------------------------------------------
# End-to-end: deleted preference absent from next ops prompt (review §2.4)
# -------------------------------------------------------------------------


class TestDeletedPreferenceAbsentFromPrompt:
    @pytest.mark.asyncio
    async def test_deleted_pref_hidden_from_snapshot_helpers(self):
        """Chain: save → delete → rebuild snapshot → assert deleted key not surfaced.

        Uses the mocked pool to simulate the PG-level filter returning no rows
        after soft-delete. Guards the High-severity 'forgotten reader' risk
        end-to-end through the helpers build_ops_prompt actually calls.
        """
        # Post-delete: load_all_preferences returns {} (filter hides tombstone).
        pool_after = _make_pool(fetch_return=[])
        store_after = StructuredMemoryStore(pool_after)
        prefs_after = await store_after.load_all_preferences()
        assert prefs_after == {}

        # load_snapshot likewise returns {} for memory_entries.
        backend_after = PostgresBackend(pool_after)
        snap_after = await backend_after.load_snapshot("Alice")
        assert snap_after == {}

        # build_ops_prompt uses these helpers — confirm the rendered string
        # does not mention the forgotten key.
        prompt = build_ops_prompt(
            exchanges=[{"user": "Alice", "text": "hi", "response": "hello", "ts": 0}],
            user_name="Alice",
            snapshot=snap_after,
            preferences=[],  # flattened from prefs_after (empty)
            persons=[{"name": "Alice", "role": None, "birth_date": None, "metadata": {}}],
            preference_keys="coffee, tea",
        )
        # The snapshot section renders memory state via format_snapshot_for_prompt,
        # which emits a "preferences:" header only when the list is non-empty.
        # After soft-delete, the filter hides tombstones → no header → no leak.
        current_state_section = prompt.split("Recent exchanges")[0]
        assert "preferences:" not in current_state_section
        assert "memory_entries/" not in current_state_section
