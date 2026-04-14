"""Tests for RoutineStore (S1.5 — Routine Memory)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from jane_conversation.memory.routine_store import RoutineStore


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
    pool, _ = mock_pool
    return RoutineStore(pool)


class TestSaveRoutine:
    @pytest.mark.asyncio
    async def test_upserts_with_on_conflict(self, store, mock_pool):
        _, conn = mock_pool
        await store.save_routine(
            name="goodnight",
            trigger_phrase="לילה טוב",
            steps=[{"service": "light.turn_off"}],
            script_id="script.jane_goodnight",
        )

        sql = conn.execute.call_args[0][0]
        assert "INSERT INTO routines" in sql
        assert "ON CONFLICT (name) DO UPDATE" in sql
        assert "occurrence_count" not in sql  # Only increment_occurrence bumps count

    @pytest.mark.asyncio
    async def test_passes_correct_params(self, store, mock_pool):
        _, conn = mock_pool
        await store.save_routine(
            name="morning",
            trigger_phrase="בוקר טוב",
            steps=[{"service": "cover.open_cover"}],
        )

        args = conn.execute.call_args[0]
        assert args[1] == "morning"
        assert args[2] == "בוקר טוב"
        assert "cover.open_cover" in args[3]  # JSON string


class TestFindRoutine:
    @pytest.mark.asyncio
    async def test_returns_matching_routine(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {
            "name": "goodnight",
            "trigger_phrase": "לילה טוב",
            "steps": [],
            "script_id": "script.jane_goodnight",
            "confidence": 1.0,
            "occurrence_count": 5,
        }

        result = await store.find_routine("לילה טוב ג'יין")
        assert result is not None
        assert result["name"] == "goodnight"

        # Verify case-insensitive LIKE query
        sql = conn.fetchrow.call_args[0][0]
        assert "LOWER($1)" in sql
        assert "LOWER(trigger_phrase)" in sql

    @pytest.mark.asyncio
    async def test_returns_none_when_no_match(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = None

        result = await store.find_routine("something random")
        assert result is None


class TestIncrementOccurrence:
    @pytest.mark.asyncio
    async def test_bumps_count(self, store, mock_pool):
        _, conn = mock_pool
        await store.increment_occurrence("goodnight")

        sql = conn.execute.call_args[0][0]
        assert "occurrence_count = occurrence_count + 1" in sql
        assert "last_used = NOW()" in sql
        assert conn.execute.call_args[0][1] == "goodnight"


class TestGetTopRoutines:
    @pytest.mark.asyncio
    async def test_returns_by_occurrence_desc(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = [
            {"name": "goodnight", "trigger_phrase": "לילה טוב", "occurrence_count": 10},
            {"name": "morning", "trigger_phrase": "בוקר טוב", "occurrence_count": 5},
        ]

        result = await store.get_top_routines(limit=10)
        assert len(result) == 2
        assert result[0]["name"] == "goodnight"

        sql = conn.fetch.call_args[0][0]
        assert "ORDER BY occurrence_count DESC" in sql
        assert "LIMIT $1" in sql


class TestLoadRoutinesForContext:
    @pytest.mark.asyncio
    async def test_formats_for_gemini(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = [
            {"name": "goodnight", "trigger_phrase": "לילה טוב", "occurrence_count": 10},
            {"name": "morning", "trigger_phrase": "בוקר טוב", "occurrence_count": 5},
        ]

        result = await store.load_routines_for_context()
        assert "goodnight" in result
        assert "לילה טוב" in result
        assert "10x" in result

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_routines(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = []

        result = await store.load_routines_for_context()
        assert result == ""


class TestLoadRoutines:
    @pytest.mark.asyncio
    async def test_returns_all(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = [
            {"name": "a", "trigger_phrase": "x", "steps": [], "script_id": None,
             "confidence": 1.0, "occurrence_count": 1, "last_used": None},
        ]

        result = await store.load_routines()
        assert len(result) == 1
