"""Tests for StructuredMemoryStore (S1.3 — Semantic + Preference Memory)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from jane_conversation.memory.structured import StructuredMemoryStore


@pytest.fixture
def mock_pool():
    """Mock asyncpg pool with async context manager."""
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn


@pytest.fixture
def store(mock_pool):
    """StructuredMemoryStore with mocked pool."""
    pool, _ = mock_pool
    return StructuredMemoryStore(pool)


class TestSavePreference:
    @pytest.mark.asyncio
    async def test_explicit_preference_confidence_1(self, store, mock_pool):
        _, conn = mock_pool
        await store.save_preference("Alice", "food_preferences", "pizza with olives")

        conn.execute.assert_called_once()
        args = conn.execute.call_args[0]
        assert args[1] == "Alice"
        assert args[2] == "food preferences"  # B1 Stage 1: key normalized at write-time
        assert args[3] == "pizza with olives"
        assert args[4] == 1.0  # confidence
        assert args[5] is False  # inferred

    @pytest.mark.asyncio
    async def test_inferred_preference_confidence_07(self, store, mock_pool):
        _, conn = mock_pool
        await store.save_preference("Alice", "bedtime_routine", "stays up late", inferred=True)

        args = conn.execute.call_args[0]
        assert args[4] == 0.7  # inferred default confidence
        assert args[5] is True  # inferred

    @pytest.mark.asyncio
    async def test_custom_confidence(self, store, mock_pool):
        _, conn = mock_pool
        await store.save_preference("Alice", "hobbies", "chess", confidence=0.9)

        args = conn.execute.call_args[0]
        assert args[4] == 0.9

    @pytest.mark.asyncio
    async def test_upsert_sql_has_on_conflict(self, store, mock_pool):
        _, conn = mock_pool
        await store.save_preference("Alice", "food_preferences", "pizza")

        sql = conn.execute.call_args[0][0]
        assert "ON CONFLICT (person_name, key) DO UPDATE" in sql
        assert "GREATEST(preferences.confidence, EXCLUDED.confidence)" in sql

    @pytest.mark.asyncio
    async def test_family_pseudo_person(self, store, mock_pool):
        """_family person_name works without a corresponding persons row."""
        _, conn = mock_pool
        await store.save_preference("_family", "screen_time_rules", "limited daily")

        args = conn.execute.call_args[0]
        assert args[1] == "_family"


class TestLoadPreferences:
    @pytest.mark.asyncio
    async def test_load_with_min_confidence(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = [
            {"key": "food_preferences", "value": "pizza", "confidence": 1.0, "inferred": False},
            {"key": "hobbies", "value": "chess", "confidence": 0.8, "inferred": True},
        ]

        result = await store.load_preferences("Alice", min_confidence=0.5)
        assert len(result) == 2
        assert result[0]["key"] == "food_preferences"

        # Verify SQL uses confidence filter
        sql = conn.fetch.call_args[0][0]
        assert "confidence >= $2" in sql

    @pytest.mark.asyncio
    async def test_load_excludes_low_confidence(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = []

        result = await store.load_preferences("Alice", min_confidence=0.5)
        assert result == []
        # Verify the min_confidence param was passed
        assert conn.fetch.call_args[0][2] == 0.5


class TestLoadAllPreferences:
    @pytest.mark.asyncio
    async def test_groups_by_person(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = [
            {"person_name": "Alice", "key": "food", "value": "pizza", "confidence": 1.0, "inferred": False},
            {"person_name": "Alice", "key": "hobby", "value": "chess", "confidence": 0.8, "inferred": True},
            {"person_name": "_family", "key": "rules", "value": "no screens", "confidence": 1.0, "inferred": False},
        ]

        result = await store.load_all_preferences()
        assert "Alice" in result
        assert "_family" in result
        assert len(result["Alice"]) == 2
        assert len(result["_family"]) == 1


class TestDecayPreferences:
    """B3 / JANE-83 — category-aware multiplicative decay.

    Three disjoint UPDATEs per call: volatile (catch-all), stable, permanent.
    Each row decays multiplicatively (``confidence × (1 − rate)``) with a
    ``confidence > 0.05`` floor. Tests assert SQL shape + rate + grace per
    category; the actual decay arithmetic is exercised in the dev VM E2E.
    """

    @pytest.mark.asyncio
    async def test_decay_runs_three_updates(self, store, mock_pool):
        _, conn = mock_pool
        conn.execute.return_value = "UPDATE 0"
        await store.decay_preferences()
        # One UPDATE per category — volatile, stable, permanent.
        assert conn.execute.call_count == 3

    @pytest.mark.asyncio
    async def test_decay_sums_counts_across_categories(self, store, mock_pool):
        _, conn = mock_pool
        conn.execute.side_effect = ["UPDATE 5", "UPDATE 2", "UPDATE 1"]
        total = await store.decay_preferences()
        assert total == 8

    @pytest.mark.asyncio
    async def test_decay_volatile_uses_3pct_rate_and_7d_grace(self, store, mock_pool):
        _, conn = mock_pool
        conn.execute.return_value = "UPDATE 0"
        await store.decay_preferences()
        # First UPDATE = volatile (catch-all).
        sql_v = conn.execute.call_args_list[0][0][0]
        assert "1 - 0.03" in sql_v
        assert "INTERVAL '7 days'" in sql_v
        assert "key != ALL($1::text[])" in sql_v
        assert "confidence > 0.05" in sql_v

    @pytest.mark.asyncio
    async def test_decay_stable_uses_1pct_rate_and_14d_grace(self, store, mock_pool):
        _, conn = mock_pool
        conn.execute.return_value = "UPDATE 0"
        await store.decay_preferences()
        sql_s = conn.execute.call_args_list[1][0][0]
        assert "1 - 0.01" in sql_s
        assert "INTERVAL '14 days'" in sql_s
        assert "key = ANY($1::text[])" in sql_s

    @pytest.mark.asyncio
    async def test_decay_permanent_uses_0_2pct_rate_and_30d_grace(self, store, mock_pool):
        _, conn = mock_pool
        conn.execute.return_value = "UPDATE 0"
        await store.decay_preferences()
        sql_p = conn.execute.call_args_list[2][0][0]
        assert "1 - 0.002" in sql_p
        assert "INTERVAL '30 days'" in sql_p
        assert "key = ANY($1::text[])" in sql_p

    @pytest.mark.asyncio
    async def test_decay_sql_filters_inferred_and_live(self, store, mock_pool):
        _, conn = mock_pool
        conn.execute.return_value = "UPDATE 0"
        await store.decay_preferences()
        # Each of the three UPDATEs must constrain to inferred + non-deleted.
        for call in conn.execute.call_args_list:
            sql = call[0][0]
            assert "inferred = TRUE" in sql
            assert "deleted_at IS NULL" in sql

    @pytest.mark.asyncio
    async def test_decay_volatile_excludes_stable_and_permanent_keys(self, store, mock_pool):
        from jane_conversation.const import PERMANENT_KEYS, STABLE_KEYS

        _, conn = mock_pool
        conn.execute.return_value = "UPDATE 0"
        await store.decay_preferences()
        # Volatile UPDATE's $1 param = STABLE ∪ PERMANENT (excluded list).
        excluded = conn.execute.call_args_list[0][0][1]
        assert set(excluded) == set(STABLE_KEYS) | set(PERMANENT_KEYS)
        # Stable UPDATE's $1 = STABLE_KEYS.
        assert list(conn.execute.call_args_list[1][0][1]) == list(STABLE_KEYS)
        # Permanent UPDATE's $1 = PERMANENT_KEYS.
        assert list(conn.execute.call_args_list[2][0][1]) == list(PERMANENT_KEYS)


class TestSavePerson:
    @pytest.mark.asyncio
    async def test_save_person_basic(self, store, mock_pool):
        _, conn = mock_pool
        await store.save_person("Alice", role="parent")

        args = conn.execute.call_args[0]
        assert args[1] == "Alice"
        assert args[2] == "parent"

    @pytest.mark.asyncio
    async def test_save_person_with_metadata(self, store, mock_pool):
        _, conn = mock_pool
        await store.save_person("Charlie", role="child", metadata={"school": "first grade"})

        sql = conn.execute.call_args[0][0]
        assert "ON CONFLICT (name) DO UPDATE" in sql


class TestSaveRelationship:
    @pytest.mark.asyncio
    async def test_creates_persons_if_needed(self, store, mock_pool):
        _, conn = mock_pool
        await store.save_relationship("Alice", "Bob", "spouse")

        # Should have 3 execute calls: 2 person inserts + 1 relationship
        assert conn.execute.call_count == 3
