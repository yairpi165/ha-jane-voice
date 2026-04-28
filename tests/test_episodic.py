"""Tests for EpisodicStore (S1.4 — Episodic Memory)."""

import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from jane_conversation.brain.working_memory import WorkingMemory
from jane_conversation.memory.episodic import EpisodicStore


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
    """EpisodicStore with mocked pool."""
    pool, _ = mock_pool
    return EpisodicStore(pool)


class TestPersistStateChange:
    @pytest.mark.asyncio
    async def test_inserts_event_and_entity(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetchval.return_value = 42  # event id

        await store.persist_state_change(
            entity_id="light.living_room",
            friendly_name="Living Room",
            old_state="off",
            new_state="on",
            timestamp=time.time(),
        )

        # Should insert event then event_entity
        assert conn.fetchval.call_count == 1
        assert conn.execute.call_count == 1

        # Verify event insert
        event_sql = conn.fetchval.call_args[0][0]
        assert "INSERT INTO events" in event_sql
        assert "state_change" in event_sql
        assert "RETURNING id" in event_sql

        # Verify event_entity insert
        entity_sql = conn.execute.call_args[0][0]
        assert "INSERT INTO event_entities" in entity_sql
        assert conn.execute.call_args[0][1] == 42  # event_id
        assert conn.execute.call_args[0][2] == "light.living_room"

    @pytest.mark.asyncio
    async def test_description_format(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetchval.return_value = 1

        await store.persist_state_change(
            entity_id="light.bedroom",
            friendly_name="Bedroom",
            old_state="on",
            new_state="off",
            timestamp=time.time(),
        )

        description = conn.fetchval.call_args[0][2]
        assert description == "Bedroom: on → off"


class TestQueryEvents:
    @pytest.mark.asyncio
    async def test_query_by_time_range(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = [
            {
                "id": 1,
                "timestamp": datetime.now(),
                "event_type": "state_change",
                "user_name": None,
                "description": "Light on",
                "metadata": {},
            },
        ]

        now = datetime.now()
        result = await store.query_events(now - timedelta(hours=1), now)
        assert len(result) == 1

        sql = conn.fetch.call_args[0][0]
        assert "timestamp >= $1" in sql
        assert "timestamp < $2" in sql

    @pytest.mark.asyncio
    async def test_query_with_entity_filter(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = []

        now = datetime.now()
        await store.query_events(now - timedelta(hours=1), now, entity_id="light.living_room")

        sql = conn.fetch.call_args[0][0]
        assert "event_entities" in sql
        assert "ee.entity_id = $4" in sql


class TestSaveEpisode:
    @pytest.mark.asyncio
    async def test_returns_episode_id(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetchval.return_value = 7

        now = datetime.now()
        result = await store.save_episode(
            title="Evening routine",
            summary="Lights turned on in living room and bedroom",
            start_ts=now - timedelta(minutes=5),
            end_ts=now,
            episode_type="routine",
        )

        assert result == 7
        sql = conn.fetchval.call_args[0][0]
        assert "INSERT INTO episodes" in sql
        assert "RETURNING id" in sql


class TestQueryEpisodes:
    @pytest.mark.asyncio
    async def test_query_by_time_range(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetch.return_value = [
            {
                "id": 1,
                "title": "Test",
                "summary": "Test",
                "start_ts": datetime.now(),
                "end_ts": datetime.now(),
                "episode_type": "activity",
            },
        ]

        now = datetime.now()
        result = await store.query_episodes(now - timedelta(hours=6), now)
        assert len(result) == 1

        sql = conn.fetch.call_args[0][0]
        assert "start_ts < $2" in sql
        assert "end_ts > $1" in sql


class TestDailySummary:
    @pytest.mark.asyncio
    async def test_save_upserts(self, store, mock_pool):
        _, conn = mock_pool
        from datetime import date

        await store.save_daily_summary(
            summary_date=date(2026, 4, 14),
            summary="Quiet day at home",
            event_count=50,
            episode_count=3,
        )

        sql = conn.execute.call_args[0][0]
        assert "ON CONFLICT (summary_date) DO UPDATE" in sql

    @pytest.mark.asyncio
    async def test_get_returns_dict(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = {"summary_date": "2026-04-14", "summary": "Test"}

        from datetime import date

        result = await store.get_daily_summary(date(2026, 4, 14))
        assert result["summary"] == "Test"

    @pytest.mark.asyncio
    async def test_get_returns_none_when_missing(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetchrow.return_value = None

        from datetime import date

        result = await store.get_daily_summary(date(2026, 4, 14))
        assert result is None


class TestConsolidationIdempotency:
    @pytest.mark.asyncio
    async def test_get_returns_none_when_no_sentinel(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetchval.return_value = None

        result = await store.get_last_consolidation_ts()
        assert result is None

    @pytest.mark.asyncio
    async def test_get_parses_iso_timestamp(self, store, mock_pool):
        _, conn = mock_pool
        conn.fetchval.return_value = "2026-04-14T08:00:00"

        result = await store.get_last_consolidation_ts()
        assert result == datetime(2026, 4, 14, 8, 0, 0)

    @pytest.mark.asyncio
    async def test_set_upserts_sentinel(self, store, mock_pool):
        _, conn = mock_pool
        ts = datetime(2026, 4, 14, 8, 0, 0)

        await store.set_last_consolidation_ts(ts)

        sql = conn.execute.call_args[0][0]
        assert "_consolidation" in sql
        assert "ON CONFLICT" in sql


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_returns_counts(self, store, mock_pool):
        _, conn = mock_pool
        conn.execute.side_effect = ["DELETE 10", "DELETE 2", "DELETE 0"]

        result = await store.cleanup_old_data(event_days=10, episode_days=90)
        assert result["events"] == 10
        assert result["episodes"] == 2
        assert result["summaries"] == 0

    @pytest.mark.asyncio
    async def test_cleanup_uses_correct_intervals(self, store, mock_pool):
        _, conn = mock_pool
        conn.execute.side_effect = ["DELETE 0", "DELETE 0", "DELETE 0"]

        await store.cleanup_old_data(event_days=10, episode_days=90, summary_days=365)

        calls = conn.execute.call_args_list
        # Events: 10 days
        assert calls[0][0][1] == timedelta(days=10)
        # Episodes: 90 days
        assert calls[1][0][1] == timedelta(days=90)
        # Summaries: 365 days
        assert calls[2][0][1] == timedelta(days=365)


def _make_redis_mock():
    """Create a properly structured Redis mock (pipeline() is sync, execute() is async)."""
    redis = AsyncMock()
    pipe = MagicMock()
    pipe.execute = AsyncMock()
    pipe.zadd = MagicMock(return_value=pipe)
    pipe.zremrangebyscore = MagicMock(return_value=pipe)
    redis.pipeline = MagicMock(return_value=pipe)
    return redis


def _make_state_event(entity_id, friendly_name, domain, old, new):
    """Create a mock state_changed event."""
    old_state = MagicMock()
    old_state.state = old
    old_state.domain = domain
    old_state.entity_id = entity_id

    new_state = MagicMock()
    new_state.state = new
    new_state.domain = domain
    new_state.entity_id = entity_id
    new_state.attributes = {"friendly_name": friendly_name}

    event = MagicMock()
    event.data = {"old_state": old_state, "new_state": new_state}
    return event


class TestDualWrite:
    @pytest.mark.asyncio
    async def test_working_memory_calls_episodic(self):
        """Dual-write: state change persists to both Redis and PG."""
        redis = _make_redis_mock()
        episodic = AsyncMock()
        hass = MagicMock()
        wm = WorkingMemory(redis, hass, episodic=episodic)

        event = _make_state_event("light.living_room", "Living Room", "light", "off", "on")
        await wm._on_state_changed(event)

        episodic.persist_state_change.assert_called_once()
        call_kwargs = episodic.persist_state_change.call_args[1]
        assert call_kwargs["entity_id"] == "light.living_room"
        assert call_kwargs["old_state"] == "off"
        assert call_kwargs["new_state"] == "on"

    @pytest.mark.asyncio
    async def test_pg_failure_does_not_break_redis(self):
        """PG failure in dual-write must not break the Redis write."""
        redis = _make_redis_mock()
        episodic = AsyncMock()
        episodic.persist_state_change.side_effect = Exception("PG down")
        hass = MagicMock()
        wm = WorkingMemory(redis, hass, episodic=episodic)

        event = _make_state_event("light.test", "Test", "light", "off", "on")

        # Should NOT raise — PG failure is caught
        await wm._on_state_changed(event)

        # Redis should still have been called
        redis.delete.assert_called_with("jane:context_cache")

    @pytest.mark.asyncio
    async def test_no_episodic_store_works_fine(self):
        """Without episodic store, working memory functions normally."""
        redis = _make_redis_mock()
        hass = MagicMock()
        wm = WorkingMemory(redis, hass)  # No episodic param

        event = _make_state_event("light.test", "Test", "light", "off", "on")

        # Should work without errors
        await wm._on_state_changed(event)
        redis.delete.assert_called_with("jane:context_cache")
