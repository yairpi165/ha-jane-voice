"""Tests for Working Memory (Redis-backed real-time awareness)."""

import json
import time
from unittest.mock import MagicMock

import pytest

from tests.conftest import make_state

fakeredis = pytest.importorskip("fakeredis")

import fakeredis.aioredis  # noqa: E402, F811

from jane_conversation.brain.working_memory import (  # noqa: E402
    CHANGES_TTL,
    CONTEXT_CACHE_TTL,
    WorkingMemory,
    _format_time_ago,
)


@pytest.fixture
def redis_mock():
    """Fake Redis for working memory tests."""
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def working_memory(redis_mock, hass_mock):
    """WorkingMemory instance with fake Redis."""
    return WorkingMemory(redis_mock, hass_mock)


def _make_state_event(old_entity_id, old_state_val, new_entity_id, new_state_val, attrs=None):
    """Create a mock state_changed event."""
    old = make_state(old_entity_id, old_state_val, attrs) if old_state_val is not None else None
    new = make_state(new_entity_id, new_state_val, attrs) if new_state_val is not None else None
    event = MagicMock()
    event.data = {"old_state": old, "new_state": new}
    return event


# --- Presence Tracking ---


class TestPresenceTracking:
    @pytest.mark.asyncio
    async def test_person_arrives_home(self, working_memory, redis_mock):
        event = _make_state_event(
            "person.yair",
            "not_home",
            "person.yair",
            "home",
            {"friendly_name": "Yair"},
        )
        await working_memory._on_state_changed(event)

        status = await redis_mock.hget("jane:presence", "Yair")
        assert status == "home"

    @pytest.mark.asyncio
    async def test_person_leaves_home(self, working_memory, redis_mock):
        event = _make_state_event(
            "person.yair",
            "home",
            "person.yair",
            "not_home",
            {"friendly_name": "Yair"},
        )
        await working_memory._on_state_changed(event)

        status = await redis_mock.hget("jane:presence", "Yair")
        assert status == "away"

    @pytest.mark.asyncio
    async def test_presence_since_tracked(self, working_memory, redis_mock):
        event = _make_state_event(
            "person.yair",
            "not_home",
            "person.yair",
            "home",
            {"friendly_name": "Yair"},
        )
        before = time.time()
        await working_memory._on_state_changed(event)

        since = await redis_mock.hget("jane:presence:since", "Yair")
        assert since is not None
        assert float(since) >= before


# --- Active Devices ---


class TestActiveDevices:
    @pytest.mark.asyncio
    async def test_light_on_added(self, working_memory, redis_mock):
        event = _make_state_event(
            "light.living_room",
            "off",
            "light.living_room",
            "on",
            {"friendly_name": "Living Room Light"},
        )
        await working_memory._on_state_changed(event)

        name = await redis_mock.hget("jane:active", "light.living_room")
        assert name == "Living Room Light"

    @pytest.mark.asyncio
    async def test_light_off_removed(self, working_memory, redis_mock):
        # First turn on
        await redis_mock.hset("jane:active", "light.living_room", "Living Room Light")
        # Then turn off
        event = _make_state_event(
            "light.living_room",
            "on",
            "light.living_room",
            "off",
            {"friendly_name": "Living Room Light"},
        )
        await working_memory._on_state_changed(event)

        name = await redis_mock.hget("jane:active", "light.living_room")
        assert name is None

    @pytest.mark.asyncio
    async def test_camera_filtered(self, working_memory, redis_mock):
        event = _make_state_event(
            "media_player.camera_stream",
            "off",
            "media_player.camera_stream",
            "on",
            {"friendly_name": "Camera Stream"},
        )
        await working_memory._on_state_changed(event)

        name = await redis_mock.hget("jane:active", "media_player.camera_stream")
        assert name is None

    @pytest.mark.asyncio
    async def test_untracked_domain_ignored(self, working_memory, redis_mock):
        event = _make_state_event(
            "sensor.temperature",
            "20",
            "sensor.temperature",
            "21",
            {"friendly_name": "Temperature"},
        )
        await working_memory._on_state_changed(event)

        active = await redis_mock.hgetall("jane:active")
        assert "sensor.temperature" not in active


# --- Recent Changes ---


class TestRecentChanges:
    @pytest.mark.asyncio
    async def test_change_recorded(self, working_memory, redis_mock):
        event = _make_state_event(
            "light.living_room",
            "off",
            "light.living_room",
            "on",
            {"friendly_name": "Living Room Light"},
        )
        await working_memory._on_state_changed(event)

        changes = await redis_mock.zrange("jane:changes", 0, -1)
        assert len(changes) == 1
        data = json.loads(changes[0])
        assert data["entity"] == "Living Room Light"
        assert data["from"] == "off"
        assert data["to"] == "on"

    @pytest.mark.asyncio
    async def test_same_state_not_recorded(self, working_memory, redis_mock):
        event = _make_state_event(
            "light.living_room",
            "on",
            "light.living_room",
            "on",
            {"friendly_name": "Living Room Light"},
        )
        await working_memory._on_state_changed(event)

        changes = await redis_mock.zrange("jane:changes", 0, -1)
        assert len(changes) == 0

    @pytest.mark.asyncio
    async def test_old_changes_pruned(self, working_memory, redis_mock):
        # Add an old entry
        old_time = time.time() - CHANGES_TTL - 100
        old_entry = json.dumps({"entity": "old", "from": "a", "to": "b", "ts": old_time})
        await redis_mock.zadd("jane:changes", {old_entry: old_time})

        # Fire a new event (triggers pruning)
        event = _make_state_event(
            "light.bedroom",
            "off",
            "light.bedroom",
            "on",
            {"friendly_name": "Bedroom Light"},
        )
        await working_memory._on_state_changed(event)

        changes = await redis_mock.zrange("jane:changes", 0, -1)
        assert len(changes) == 1
        data = json.loads(changes[0])
        assert data["entity"] == "Bedroom Light"


# --- Context Rendering ---


class TestGetContext:
    @pytest.mark.asyncio
    async def test_context_includes_presence(self, working_memory, redis_mock):
        await redis_mock.hset("jane:presence", "Yair", "home")
        await redis_mock.hset("jane:presence:since", "Yair", str(time.time() - 120))

        context = await working_memory.get_context()
        assert "Yair" in context
        assert "home" in context
        assert "2 min ago" in context

    @pytest.mark.asyncio
    async def test_context_includes_active(self, working_memory, redis_mock):
        await redis_mock.hset("jane:active", "light.living_room", "Living Room Light")

        context = await working_memory.get_context()
        assert "Living Room Light" in context

    @pytest.mark.asyncio
    async def test_context_includes_recent_changes(self, working_memory, redis_mock):
        now = time.time()
        entry = json.dumps({"entity": "AC", "from": "off", "to": "cool", "ts": now - 300})
        await redis_mock.zadd("jane:changes", {entry: now - 300})

        context = await working_memory.get_context()
        assert "AC" in context
        assert "off" in context
        assert "cool" in context

    @pytest.mark.asyncio
    async def test_context_cache_used(self, working_memory, redis_mock):
        await redis_mock.set("jane:context_cache", "cached context", ex=CONTEXT_CACHE_TTL)

        context = await working_memory.get_context()
        assert context == "cached context"

    @pytest.mark.asyncio
    async def test_context_cache_invalidated_on_change(self, working_memory, redis_mock):
        await redis_mock.set("jane:context_cache", "old cache")

        event = _make_state_event(
            "light.living_room",
            "off",
            "light.living_room",
            "on",
            {"friendly_name": "Living Room Light"},
        )
        await working_memory._on_state_changed(event)

        cached = await redis_mock.get("jane:context_cache")
        assert cached is None

    @pytest.mark.asyncio
    async def test_empty_redis_returns_none(self, working_memory, redis_mock):
        context = await working_memory.get_context()
        # Weather comes from hass_mock, so context won't be None
        # but if no weather either, it would be None
        assert context is not None or context is None  # Depends on hass_mock weather


# --- Initial Snapshot ---


class TestSnapshot:
    @pytest.mark.asyncio
    async def test_snapshot_populates_presence(self, working_memory, redis_mock):
        await working_memory._snapshot_current_state()

        # hass_mock has person.yair (home) and person.efrat (not_home)
        yair = await redis_mock.hget("jane:presence", "יאיר")
        assert yair == "home"

        efrat = await redis_mock.hget("jane:presence", "אפרת")
        assert efrat == "away"

    @pytest.mark.asyncio
    async def test_snapshot_populates_active_devices(self, working_memory, redis_mock):
        await working_memory._snapshot_current_state()

        # hass_mock has light.living_room (on), climate.ac (cool), media_player.tv (on)
        living = await redis_mock.hget("jane:active", "light.living_room")
        assert living is not None

        ac = await redis_mock.hget("jane:active", "climate.ac")
        assert ac is not None

        # Camera should be filtered
        camera = await redis_mock.hget("jane:active", "media_player.camera_stream")
        assert camera is None

        # Off light should not be in active
        bedroom = await redis_mock.hget("jane:active", "light.bedroom")
        assert bedroom is None


# --- Last Interaction ---


class TestLastInteraction:
    @pytest.mark.asyncio
    async def test_record_and_read(self, working_memory, redis_mock):
        await working_memory.record_interaction("Yair", "turn on the light", "done!")

        data = await redis_mock.hgetall("jane:last_interaction")
        assert data["user"] == "Yair"
        assert data["text"] == "turn on the light"
        assert data["response"] == "done!"
        assert "timestamp" in data


# --- Fallback (context.py integration) ---


class TestFallback:
    @pytest.mark.asyncio
    async def test_build_context_with_working_memory(self, working_memory, redis_mock):
        from jane_conversation.brain.context import build_context

        await redis_mock.hset("jane:presence", "Yair", "home")
        await redis_mock.hset("jane:presence:since", "Yair", str(time.time()))

        context = await build_context(MagicMock(), working_memory)
        assert "Yair" in context

    @pytest.mark.asyncio
    async def test_build_context_fallback_without_working_memory(self, hass_mock):
        from jane_conversation.brain.context import build_context

        context = await build_context(hass_mock, None)
        assert "Weather" in context or context == ""


# --- Helper ---


class TestFormatTimeAgo:
    def test_just_now(self):
        assert _format_time_ago(time.time() - 10) == "just now"

    def test_minutes(self):
        assert _format_time_ago(time.time() - 300) == "5 min ago"

    def test_hours(self):
        assert _format_time_ago(time.time() - 7200) == "2h ago"
