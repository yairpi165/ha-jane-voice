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
    describe_entity,
)
from jane_conversation.const import normalize_person_state, parse_csv  # noqa: E402


@pytest.fixture
def redis_mock():
    """Fake Redis for working memory tests."""
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def working_memory(redis_mock, hass_mock):
    """WorkingMemory instance with fake Redis and default config."""
    return WorkingMemory(redis_mock, hass_mock)


def _make_state_event(old_entity_id, old_state_val, new_entity_id, new_state_val, attrs=None):
    """Create a mock state_changed event."""
    old = make_state(old_entity_id, old_state_val, attrs) if old_state_val is not None else None
    new = make_state(new_entity_id, new_state_val, attrs) if new_state_val is not None else None
    event = MagicMock()
    event.data = {"old_state": old, "new_state": new}
    return event


# --- parse_csv ---


class TestParseCsv:
    def test_basic(self):
        assert parse_csv("light,switch") == {"light", "switch"}

    def test_strips_whitespace(self):
        assert parse_csv(" light , switch ") == {"light", "switch"}

    def test_lowercases(self):
        assert parse_csv("Light,Switch") == {"light", "switch"}

    def test_filters_empty(self):
        assert parse_csv("light,,switch") == {"light", "switch"}

    def test_empty_string(self):
        assert parse_csv("") == set()

    def test_none(self):
        assert parse_csv(None) == set()


# --- normalize_person_state ---


class TestNormalizePersonState:
    def test_home(self):
        assert normalize_person_state("home") == "home"

    def test_not_home(self):
        assert normalize_person_state("not_home") == "away"

    def test_away(self):
        assert normalize_person_state("away") == "away"

    def test_unknown(self):
        assert normalize_person_state("unknown") == "unknown"

    def test_other(self):
        assert normalize_person_state("unavailable") == "unknown"


# --- describe_entity ---


class TestDescribeEntity:
    def test_climate_with_temp(self):
        state = make_state("climate.ac", "cool", {"friendly_name": "מזגן", "temperature": 22})
        assert describe_entity(state) == "מזגן (cool, 22°C)"

    def test_climate_with_unit(self):
        state = make_state("climate.ac", "heat", {"friendly_name": "מזגן", "temperature": 72, "temperature_unit": "°F"})
        assert describe_entity(state) == "מזגן (heat, 72°F)"

    def test_climate_no_temp(self):
        state = make_state("climate.ac", "cool", {"friendly_name": "מזגן"})
        assert describe_entity(state) == "מזגן (cool)"

    def test_media_player_with_title(self):
        state = make_state(
            "media_player.tv", "playing", {"friendly_name": "SONY TV", "media_title": "YouTube Video Name"}
        )
        assert describe_entity(state) == "SONY TV (YouTube Video Name)"

    def test_media_player_title_truncated(self):
        state = make_state(
            "media_player.tv",
            "playing",
            {"friendly_name": "TV", "media_title": "A" * 50},
        )
        result = describe_entity(state)
        assert len(result) < 50

    def test_media_player_with_source(self):
        state = make_state("media_player.tv", "playing", {"friendly_name": "SONY TV", "app_name": "YouTube"})
        assert describe_entity(state) == "SONY TV (YouTube)"

    def test_media_player_state_only(self):
        state = make_state("media_player.tv", "playing", {"friendly_name": "SONY TV"})
        assert describe_entity(state) == "SONY TV (playing)"

    def test_cover_with_position(self):
        state = make_state("cover.shutter", "open", {"friendly_name": "תריס סלון", "current_position": 45})
        assert describe_entity(state) == "תריס סלון (45%)"

    def test_cover_without_position(self):
        state = make_state("cover.shutter", "open", {"friendly_name": "תריס סלון"})
        assert describe_entity(state) == "תריס סלון (open)"

    def test_vacuum(self):
        state = make_state("vacuum.x40", "cleaning", {"friendly_name": "X40 Ultra"})
        assert describe_entity(state) == "X40 Ultra (cleaning)"

    def test_light_with_brightness_raw(self):
        state = make_state("light.living", "on", {"friendly_name": "סלון", "brightness": 178})
        assert describe_entity(state) == "סלון (70%)"  # 178/255 ≈ 70%

    def test_light_with_brightness_pct(self):
        state = make_state("light.living", "on", {"friendly_name": "סלון", "brightness_pct": 70})
        assert describe_entity(state) == "סלון (70%)"

    def test_light_no_brightness(self):
        state = make_state("light.living", "on", {"friendly_name": "סלון"})
        assert describe_entity(state) == "סלון"

    def test_fan_with_percentage(self):
        state = make_state("fan.bedroom", "on", {"friendly_name": "מאוורר", "percentage": 50})
        assert describe_entity(state) == "מאוורר (50%)"

    def test_lock_locked(self):
        state = make_state("lock.door", "locked", {"friendly_name": "דלת כניסה"})
        assert describe_entity(state) == "דלת כניסה (locked)"

    def test_lock_unlocked(self):
        state = make_state("lock.door", "unlocked", {"friendly_name": "דלת כניסה"})
        assert describe_entity(state) == "דלת כניסה (unlocked)"

    def test_default_on(self):
        state = make_state("switch.boiler", "on", {"friendly_name": "דוד חשמל"})
        assert describe_entity(state) == "דוד חשמל"

    def test_default_other_state(self):
        state = make_state("switch.device", "standby", {"friendly_name": "מכשיר"})
        assert describe_entity(state) == "מכשיר (standby)"


# --- Config-driven WorkingMemory ---


class TestConfig:
    def test_defaults_when_no_config(self, redis_mock, hass_mock):
        wm = WorkingMemory(redis_mock, hass_mock)
        assert "light" in wm._tracked
        assert "switch" in wm._tracked
        assert "person" in wm._tracked
        assert "camera" in wm._skip

    def test_options_override_data(self, redis_mock, hass_mock):
        entry = MagicMock()
        entry.data = {"tracked_domains": "light,climate"}
        entry.options = {"tracked_domains": "switch,vacuum"}
        wm = WorkingMemory(redis_mock, hass_mock, config_entry=entry)
        assert "switch" in wm._tracked
        assert "vacuum" in wm._tracked
        assert "light" not in wm._tracked  # overridden
        assert "person" in wm._tracked  # always added

    def test_person_always_tracked(self, redis_mock, hass_mock):
        entry = MagicMock()
        entry.data = {"tracked_domains": "light"}
        entry.options = {}
        wm = WorkingMemory(redis_mock, hass_mock, config_entry=entry)
        assert "person" in wm._tracked

    def test_switch_tracked_by_default(self, redis_mock, hass_mock):
        wm = WorkingMemory(redis_mock, hass_mock)
        assert "switch" in wm._tracked

    def test_vacuum_tracked_by_default(self, redis_mock, hass_mock):
        wm = WorkingMemory(redis_mock, hass_mock)
        assert "vacuum" in wm._tracked


# --- Presence Tracking ---


class TestPresenceTracking:
    @pytest.mark.asyncio
    async def test_person_arrives_home(self, working_memory, redis_mock):
        event = _make_state_event("person.yair", "not_home", "person.yair", "home", {"friendly_name": "Yair"})
        await working_memory._on_state_changed(event)
        assert await redis_mock.hget("jane:presence", "Yair") == "home"

    @pytest.mark.asyncio
    async def test_person_leaves_home(self, working_memory, redis_mock):
        event = _make_state_event("person.yair", "home", "person.yair", "not_home", {"friendly_name": "Yair"})
        await working_memory._on_state_changed(event)
        assert await redis_mock.hget("jane:presence", "Yair") == "away"

    @pytest.mark.asyncio
    async def test_person_unknown(self, working_memory, redis_mock):
        event = _make_state_event("person.efrat", "home", "person.efrat", "unknown", {"friendly_name": "Efrat"})
        await working_memory._on_state_changed(event)
        assert await redis_mock.hget("jane:presence", "Efrat") == "unknown"

    @pytest.mark.asyncio
    async def test_presence_since_tracked(self, working_memory, redis_mock):
        event = _make_state_event("person.yair", "not_home", "person.yair", "home", {"friendly_name": "Yair"})
        before = time.time()
        await working_memory._on_state_changed(event)
        since = await redis_mock.hget("jane:presence:since", "Yair")
        assert since is not None
        assert float(since) >= before


# --- Active Devices ---


class TestActiveDevices:
    @pytest.mark.asyncio
    async def test_light_on_rich_description(self, working_memory, redis_mock):
        event = _make_state_event(
            "light.living_room",
            "off",
            "light.living_room",
            "on",
            {"friendly_name": "סלון", "brightness": 200},
        )
        await working_memory._on_state_changed(event)
        val = await redis_mock.hget("jane:active", "light.living_room")
        assert "סלון" in val
        assert "%" in val  # rich description with brightness

    @pytest.mark.asyncio
    async def test_light_off_removed(self, working_memory, redis_mock):
        await redis_mock.hset("jane:active", "light.living_room", "סלון")
        event = _make_state_event(
            "light.living_room",
            "on",
            "light.living_room",
            "off",
            {"friendly_name": "סלון"},
        )
        await working_memory._on_state_changed(event)
        assert await redis_mock.hget("jane:active", "light.living_room") is None

    @pytest.mark.asyncio
    async def test_switch_tracked(self, working_memory, redis_mock):
        event = _make_state_event(
            "switch.boiler",
            "off",
            "switch.boiler",
            "on",
            {"friendly_name": "דוד חשמל"},
        )
        await working_memory._on_state_changed(event)
        assert await redis_mock.hget("jane:active", "switch.boiler") == "דוד חשמל"

    @pytest.mark.asyncio
    async def test_camera_filtered(self, working_memory, redis_mock):
        event = _make_state_event(
            "switch.camera_enabled",
            "off",
            "switch.camera_enabled",
            "on",
            {"friendly_name": "Camera"},
        )
        await working_memory._on_state_changed(event)
        assert await redis_mock.hget("jane:active", "switch.camera_enabled") is None

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
        assert "sensor.temperature" not in await redis_mock.hgetall("jane:active")


# --- Smart Debounce ---


class TestDebounce:
    @pytest.mark.asyncio
    async def test_first_change_recorded(self, working_memory, redis_mock):
        event = _make_state_event(
            "light.living_room",
            "off",
            "light.living_room",
            "on",
            {"friendly_name": "Living Room"},
        )
        await working_memory._on_state_changed(event)
        changes = await redis_mock.zrange("jane:changes", 0, -1)
        assert len(changes) == 1

    @pytest.mark.asyncio
    async def test_same_state_within_60s_suppressed(self, working_memory, redis_mock):
        """TV goes playing→idle→playing within 60s — second 'playing' suppressed."""
        event1 = _make_state_event(
            "media_player.tv",
            "idle",
            "media_player.tv",
            "playing",
            {"friendly_name": "TV"},
        )
        await working_memory._on_state_changed(event1)

        event2 = _make_state_event(
            "media_player.tv",
            "playing",
            "media_player.tv",
            "idle",
            {"friendly_name": "TV"},
        )
        await working_memory._on_state_changed(event2)

        # Verify per-state debounce keys exist
        assert await redis_mock.get("jane:change_ts:media_player.tv:playing") is not None
        assert await redis_mock.get("jane:change_ts:media_player.tv:idle") is not None

        # Now back to playing (same as first) within 60s — should be suppressed
        event3 = _make_state_event(
            "media_player.tv",
            "idle",
            "media_player.tv",
            "playing",
            {"friendly_name": "TV"},
        )
        await working_memory._on_state_changed(event3)

        changes = await redis_mock.zrange("jane:changes", 0, -1)
        assert len(changes) == 2  # event1 + event2, not event3

    @pytest.mark.asyncio
    async def test_different_state_within_60s_passes(self, working_memory, redis_mock):
        """off→on→off — all different transitions, all recorded."""
        event1 = _make_state_event("light.hall", "off", "light.hall", "on", {"friendly_name": "Hall"})
        await working_memory._on_state_changed(event1)

        event2 = _make_state_event("light.hall", "on", "light.hall", "off", {"friendly_name": "Hall"})
        await working_memory._on_state_changed(event2)

        changes = await redis_mock.zrange("jane:changes", 0, -1)
        assert len(changes) == 2

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


# --- Recent Changes ---


class TestRecentChanges:
    @pytest.mark.asyncio
    async def test_old_changes_pruned(self, working_memory, redis_mock):
        old_time = time.time() - CHANGES_TTL - 100
        old_entry = json.dumps({"entity": "old", "from": "a", "to": "b", "ts": old_time})
        await redis_mock.zadd("jane:changes", {old_entry: old_time})

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
        assert json.loads(changes[0])["entity"] == "Bedroom Light"


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
        await redis_mock.hset("jane:active", "light.living_room", "סלון (78%)")
        context = await working_memory.get_context()
        assert "סלון (78%)" in context

    @pytest.mark.asyncio
    async def test_context_includes_recent_changes(self, working_memory, redis_mock):
        now = time.time()
        entry = json.dumps({"entity": "AC", "from": "off", "to": "cool", "ts": now - 300})
        await redis_mock.zadd("jane:changes", {entry: now - 300})
        context = await working_memory.get_context()
        assert "AC" in context
        assert "cool" in context

    @pytest.mark.asyncio
    async def test_context_cache_used(self, working_memory, redis_mock):
        await redis_mock.set("jane:context_cache", "cached context", ex=CONTEXT_CACHE_TTL)
        assert await working_memory.get_context() == "cached context"

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
        assert await redis_mock.get("jane:context_cache") is None

    @pytest.mark.asyncio
    async def test_empty_redis_returns_none(self, working_memory):
        working_memory._hass.states.get = MagicMock(return_value=None)
        assert await working_memory.get_context() is None


# --- Initial Snapshot ---


class TestSnapshot:
    @pytest.mark.asyncio
    async def test_snapshot_populates_presence(self, working_memory, redis_mock):
        await working_memory._snapshot_current_state()
        assert await redis_mock.hget("jane:presence", "יאיר") == "home"
        assert await redis_mock.hget("jane:presence", "אפרת") == "away"

    @pytest.mark.asyncio
    async def test_snapshot_populates_active_with_rich_descriptions(self, working_memory, redis_mock):
        await working_memory._snapshot_current_state()
        living = await redis_mock.hget("jane:active", "light.living_room")
        assert living is not None
        assert "סלון" in living

        ac = await redis_mock.hget("jane:active", "climate.ac")
        assert ac is not None
        assert "cool" in ac
        assert "24" in ac

        assert await redis_mock.hget("jane:active", "media_player.camera_stream") is None
        assert await redis_mock.hget("jane:active", "light.bedroom") is None

    @pytest.mark.asyncio
    async def test_startup_flushes_stale_data(self, working_memory, redis_mock):
        await redis_mock.hset("jane:active", "stale.entity", "old data")
        await working_memory.start_listening()
        assert await redis_mock.hget("jane:active", "stale.entity") is None


# --- Last Interaction ---


class TestLastInteraction:
    @pytest.mark.asyncio
    async def test_record_and_read(self, working_memory, redis_mock):
        await working_memory.record_interaction("Yair", "turn on the light", "done!")
        data = await redis_mock.hgetall("jane:last_interaction")
        assert data["user"] == "Yair"
        assert data["text"] == "turn on the light"
        assert data["response"] == "done!"


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
