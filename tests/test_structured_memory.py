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
        assert args[2] == "food_preferences"
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
    @pytest.mark.asyncio
    async def test_decay_returns_count(self, store, mock_pool):
        _, conn = mock_pool
        conn.execute.return_value = "UPDATE 3"

        count = await store.decay_preferences()
        assert count == 3

    @pytest.mark.asyncio
    async def test_decay_applies_correct_formula(self, store, mock_pool):
        _, conn = mock_pool
        conn.execute.return_value = "UPDATE 0"
        await store.decay_preferences()
        sql = conn.execute.call_args[0][0]
        assert "confidence - 0.05" in sql
        assert "1.0 -" not in sql  # guard against the reset formula

    @pytest.mark.asyncio
    async def test_decay_sql_filters_inferred_and_old(self, store, mock_pool):
        _, conn = mock_pool
        conn.execute.return_value = "UPDATE 0"

        await store.decay_preferences()
        sql = conn.execute.call_args[0][0]
        assert "inferred = TRUE" in sql
        assert "INTERVAL '7 days'" in sql
        assert "GREATEST(0.0" in sql


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
