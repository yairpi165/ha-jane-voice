"""S3.0 (JANE-71) — speaker resolution tests.

Covers:
- Layered resolve_speaker() ladder (Steps 0/1/2/3/5).
- Recency decay on Step 3 sessions.
- Redis-down fallback for Step 2 (presence).
- write_speaker_session refresh threshold (only at confidence ≥ 0.7).
- Pending-ask state machine read/write/clear + match_known_person.
- Confidence-aware check_permission gates.
- Confidence-aware build_memory_context per-field tiers.
"""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_jane_data(redis=None, structured=None, persons=None, episodic=None) -> MagicMock:
    """Build a JaneData-like mock object for hass.data[DOMAIN]."""
    data = MagicMock()
    data.redis = redis
    data.structured = structured
    data.episodic = episodic
    data.policies = None
    return data


def _make_hass(jane_data=None, person_states=None, hass_user_name="Alice"):
    """Mock HomeAssistant with hass.data[DOMAIN] = jane_data."""
    hass = MagicMock()
    hass.data = {"jane_conversation": jane_data}
    if person_states is None:
        person_states = []
    hass.states.async_all.return_value = person_states
    user = MagicMock()
    user.name = hass_user_name
    hass.auth.async_get_user = AsyncMock(return_value=user)
    return hass


def _make_person_state(entity_id: str, state: str, friendly_name: str):
    s = MagicMock()
    s.entity_id = entity_id
    s.state = state
    s.attributes = {"friendly_name": friendly_name}
    return s


# ---------------------------------------------------------------------------
# Step 0 — HA context
# ---------------------------------------------------------------------------


class TestStep0HAContext:
    @pytest.mark.asyncio
    async def test_user_id_resolves_to_name_at_1_0(self):
        from jane_conversation.brain.speaker import resolve_speaker

        hass = _make_hass(jane_data=_make_jane_data())
        name, conf, layer = await resolve_speaker(hass, None, "conv-1", "ha-user-uuid")
        assert (name, conf, layer) == ("Alice", 1.0, "step_0")

    @pytest.mark.asyncio
    async def test_default_user_id_falls_through(self):
        """D2 — the literal 'default' is the JANE-62 fingerprint and must fall through."""
        from jane_conversation.brain.speaker import resolve_speaker

        hass = _make_hass(jane_data=_make_jane_data())
        # Step 5 fallback returns "default" name at 0.3 since no primary_user is set.
        name, conf, layer = await resolve_speaker(hass, None, "conv-1", "default")
        assert layer != "step_0"
        assert conf < 1.0

    @pytest.mark.asyncio
    async def test_none_user_id_falls_through(self):
        from jane_conversation.brain.speaker import resolve_speaker

        hass = _make_hass(jane_data=_make_jane_data())
        name, conf, layer = await resolve_speaker(hass, None, "conv-1", None)
        assert layer != "step_0"


# ---------------------------------------------------------------------------
# Step 2 — Presence
# ---------------------------------------------------------------------------


class TestStep2Presence:
    @pytest.mark.asyncio
    async def test_exactly_one_home_resolves_at_0_95(self):
        from jane_conversation.brain.speaker import resolve_speaker

        person_alice = _make_person_state("person.alice", "home", "Alice")
        person_bob = _make_person_state("person.bob", "not_home", "Bob")
        hass = _make_hass(
            jane_data=_make_jane_data(redis=None),
            person_states=[person_alice, person_bob],
        )
        name, conf, layer = await resolve_speaker(hass, None, "conv-1", None)
        assert (name, conf, layer) == ("Alice", 0.95, "step_2")

    @pytest.mark.asyncio
    async def test_multiple_home_falls_through(self):
        from jane_conversation.brain.speaker import resolve_speaker

        alice = _make_person_state("person.alice", "home", "Alice")
        bob = _make_person_state("person.bob", "home", "Bob")
        hass = _make_hass(jane_data=_make_jane_data(redis=None), person_states=[alice, bob])
        name, conf, layer = await resolve_speaker(hass, None, "conv-1", None)
        assert layer in ("step_5",)
        assert conf <= 0.3

    @pytest.mark.asyncio
    async def test_redis_down_falls_back_to_hass_states(self):
        """Redis-down mode: jane:presence read fails, fallback uses hass.states."""
        from jane_conversation.brain.speaker import resolve_speaker

        redis = AsyncMock()
        redis.hgetall.side_effect = ConnectionError("redis down")
        alice = _make_person_state("person.alice", "home", "Alice")
        bob = _make_person_state("person.bob", "not_home", "Bob")
        hass = _make_hass(jane_data=_make_jane_data(redis=redis), person_states=[alice, bob])
        name, conf, layer = await resolve_speaker(hass, None, "conv-1", None)
        assert (name, conf, layer) == ("Alice", 0.95, "step_2")


# ---------------------------------------------------------------------------
# Step 3 — Speaker session
# ---------------------------------------------------------------------------


class TestStep3SpeakerSession:
    @pytest.mark.asyncio
    async def test_recent_session_resolves_at_inherited_confidence(self):
        from jane_conversation.brain.speaker import resolve_speaker

        # Session written 30 seconds ago at confidence 0.95.
        session_ts = time.time() - 30
        session_blob = json.dumps(
            {
                "user_name": "Alice",
                "conversation_id": "old-conv",
                "ts": session_ts,
                "confidence": 0.95,
            }
        )
        redis = AsyncMock()
        redis.hgetall.return_value = {}  # no presence
        redis.get.return_value = session_blob
        hass = _make_hass(jane_data=_make_jane_data(redis=redis), person_states=[])
        name, conf, layer = await resolve_speaker(hass, "device-X", "new-conv", None)
        assert layer == "step_3"
        assert name == "Alice"
        # 30 seconds ≈ 0.5 min → 0.95 ** 0.5 ≈ 0.975 → conf ≈ 0.95 * 0.975 ≈ 0.93
        assert 0.9 < conf <= 0.95

    @pytest.mark.asyncio
    async def test_old_session_clamped_to_floor(self):
        from jane_conversation.brain.speaker import resolve_speaker

        # Session 1 hour old.
        session_blob = json.dumps(
            {
                "user_name": "Alice",
                "conversation_id": "old-conv",
                "ts": time.time() - 3600,
                "confidence": 0.95,
            }
        )
        redis = AsyncMock()
        redis.hgetall.return_value = {}
        redis.get.return_value = session_blob
        hass = _make_hass(jane_data=_make_jane_data(redis=redis), person_states=[])
        name, conf, layer = await resolve_speaker(hass, "device-X", "new-conv", None)
        assert layer == "step_3"
        assert conf == 0.5  # floor

    @pytest.mark.asyncio
    async def test_no_session_falls_through(self):
        from jane_conversation.brain.speaker import resolve_speaker

        redis = AsyncMock()
        redis.hgetall.return_value = {}
        redis.get.return_value = None
        hass = _make_hass(jane_data=_make_jane_data(redis=redis), person_states=[])
        name, conf, layer = await resolve_speaker(hass, "device-X", "new-conv", None)
        assert layer == "step_5"


# ---------------------------------------------------------------------------
# Step 5 — Fallback
# ---------------------------------------------------------------------------


class TestStep5Fallback:
    @pytest.mark.asyncio
    async def test_returns_primary_user_at_0_3(self):
        from jane_conversation.brain.speaker import resolve_speaker

        structured = AsyncMock()
        structured.load_persons.return_value = [
            {"name": "Charlie", "metadata": {"is_primary": True}},
            {"name": "Daisy", "metadata": {}},
        ]
        hass = _make_hass(jane_data=_make_jane_data(structured=structured), person_states=[])
        name, conf, layer = await resolve_speaker(hass, None, "conv-1", None)
        assert (name, conf, layer) == ("Charlie", 0.3, "step_5")

    @pytest.mark.asyncio
    async def test_returns_default_name_when_no_primary_user(self):
        from jane_conversation.brain.speaker import resolve_speaker

        structured = AsyncMock()
        structured.load_persons.return_value = [{"name": "Alice", "metadata": {}}]
        hass = _make_hass(jane_data=_make_jane_data(structured=structured), person_states=[])
        name, conf, layer = await resolve_speaker(hass, None, "conv-1", None)
        assert (name, conf, layer) == ("default", 0.3, "step_5")


# ---------------------------------------------------------------------------
# write_speaker_session refresh threshold
# ---------------------------------------------------------------------------


class TestWriteSpeakerSession:
    @pytest.mark.asyncio
    async def test_writes_when_confidence_ge_0_7(self):
        from jane_conversation.brain.speaker import write_speaker_session

        redis = AsyncMock()
        hass = _make_hass(jane_data=_make_jane_data(redis=redis))
        await write_speaker_session(hass, "device-X", "Alice", "conv-1", 0.85)
        redis.set.assert_called_once()
        args, kwargs = redis.set.call_args
        assert "jane:session:device-X" in args[0]
        payload = json.loads(args[1])
        assert payload["user_name"] == "Alice"
        assert kwargs["ex"] == 900  # SPEAKER_SESSION_TTL_SECONDS

    @pytest.mark.asyncio
    async def test_skips_when_confidence_below_0_7(self):
        """Low-confidence resolutions must NOT poison the session for next turn."""
        from jane_conversation.brain.speaker import write_speaker_session

        redis = AsyncMock()
        hass = _make_hass(jane_data=_make_jane_data(redis=redis))
        await write_speaker_session(hass, "device-X", "default", "conv-1", 0.3)
        redis.set.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_no_device_id(self):
        from jane_conversation.brain.speaker import write_speaker_session

        redis = AsyncMock()
        hass = _make_hass(jane_data=_make_jane_data(redis=redis))
        await write_speaker_session(hass, None, "Alice", "conv-1", 0.95)
        redis.set.assert_not_called()


# ---------------------------------------------------------------------------
# Pending-ask state machine
# ---------------------------------------------------------------------------


class TestPendingAsk:
    @pytest.mark.asyncio
    async def test_set_check_clear_round_trip(self):
        from jane_conversation.brain.speaker_pending_ask import (
            check_pending_ask,
            clear_pending_ask,
            set_pending_ask,
        )

        store: dict[str, str] = {}

        async def fake_set(key, value, ex=None):
            store[key] = value

        async def fake_get(key):
            return store.get(key)

        async def fake_delete(key):
            store.pop(key, None)

        redis = AsyncMock()
        redis.set.side_effect = fake_set
        redis.get.side_effect = fake_get
        redis.delete.side_effect = fake_delete
        hass = _make_hass(jane_data=_make_jane_data(redis=redis))

        await set_pending_ask(hass, "device-X", "conv-1", "what time is it?")
        pending = await check_pending_ask(hass, "device-X")
        assert pending is not None
        assert pending["original_request"] == "what time is it?"
        await clear_pending_ask(hass, "device-X")
        assert await check_pending_ask(hass, "device-X") is None

    @pytest.mark.asyncio
    async def test_match_known_person_finds_substring(self):
        from jane_conversation.brain.speaker_pending_ask import match_known_person

        structured = AsyncMock()
        structured.load_persons.return_value = [
            {"name": "Alice"},
            {"name": "Bob"},
        ]
        hass = _make_hass(jane_data=_make_jane_data(structured=structured))
        assert await match_known_person(hass, "this is alice speaking") == "Alice"
        assert await match_known_person(hass, "Bob") == "Bob"
        assert await match_known_person(hass, "nobody") is None
        assert await match_known_person(hass, "") is None


# ---------------------------------------------------------------------------
# Confidence-aware policy gates
# ---------------------------------------------------------------------------


class TestPolicyConfidenceGates:
    @pytest.mark.asyncio
    async def test_personal_data_gate_at_below_0_5(self):
        from jane_conversation.memory.policy import PolicyStore

        pool = MagicMock()
        store = PolicyStore(pool)
        # Don't need DB calls — gate triggers before load_policies.
        result = await store.check_permission("Alice", "load_preferences", confidence=0.3)
        assert result is not None
        assert "מידע אישי" in result

    @pytest.mark.asyncio
    async def test_sensitive_action_gate_at_below_0_7(self):
        from jane_conversation.memory.policy import PolicyStore

        pool = MagicMock()
        store = PolicyStore(pool)
        result = await store.check_permission("Alice", "forget_memory", confidence=0.6)
        assert result is not None
        assert "אשר" in result

    @pytest.mark.asyncio
    async def test_high_confidence_passes_gate(self):
        """At confidence ≥ 0.7 + admin role, both gates open."""
        from jane_conversation.memory.policy import PolicyStore

        pool = MagicMock()
        conn = AsyncMock()
        conn.fetch.return_value = [{"key": "role", "value": "admin"}]
        pool.acquire.return_value.__aenter__.return_value = conn
        store = PolicyStore(pool)
        result = await store.check_permission("Alice", "forget_memory", confidence=0.95)
        assert result is None


# ---------------------------------------------------------------------------
# build_memory_context tiers
# ---------------------------------------------------------------------------


class TestBuildMemoryContextTiers:
    @pytest.mark.asyncio
    async def test_household_min_below_0_5_returns_persons_only(self):
        """At confidence < 0.5: only persons summary, no preferences, no birth_date."""
        from jane_conversation.memory.context_builder import build_memory_context

        structured = AsyncMock()
        structured.load_persons.return_value = [
            {"name": "Alice", "role": "parent", "birth_date": None, "metadata": {}},
            {"name": "Bob", "role": "child", "birth_date": None, "metadata": {}},
        ]
        structured.load_all_preferences.return_value = {
            "Alice": [{"key": "music_taste", "value": "jazz", "confidence": 0.9}],
            "_family": [{"key": "tv_volume_default", "value": "20", "confidence": 0.9}],
        }
        hass = _make_hass(jane_data=_make_jane_data(structured=structured))
        result = await build_memory_context(hass, "Alice", confidence=0.3)
        assert "## Family" in result
        assert "Alice" in result
        # Preferences and household rules must NOT appear at household-min.
        assert "## Alice's Preferences" not in result
        assert "## Household Rules" not in result

    @pytest.mark.asyncio
    async def test_family_tier_includes_household_rules_not_personal_prefs(self):
        from jane_conversation.memory.context_builder import build_memory_context

        structured = AsyncMock()
        structured.load_persons.return_value = [
            {"name": "Alice", "role": "parent", "birth_date": None, "metadata": {}},
        ]
        structured.load_all_preferences.return_value = {
            "Alice": [{"key": "music_taste", "value": "jazz", "confidence": 0.9}],
            "_family": [{"key": "tv_volume_default", "value": "20", "confidence": 0.9}],
        }
        hass = _make_hass(jane_data=_make_jane_data(structured=structured))
        result = await build_memory_context(hass, "Alice", confidence=0.6)
        assert "## Family" in result
        assert "## Household Rules" in result
        assert "Tv Volume Default" in result
        assert "## Alice's Preferences" not in result

    @pytest.mark.asyncio
    async def test_personal_tier_full_context(self):
        from jane_conversation.memory.context_builder import build_memory_context

        structured = AsyncMock()
        structured.load_persons.return_value = [
            {"name": "Alice", "role": "parent", "birth_date": None, "metadata": {}},
        ]
        structured.load_all_preferences.return_value = {
            "Alice": [{"key": "music_taste", "value": "jazz", "confidence": 0.9}],
            "_family": [{"key": "tv_volume_default", "value": "20", "confidence": 0.9}],
        }
        hass = _make_hass(jane_data=_make_jane_data(structured=structured))
        with patch(
            "jane_conversation.memory.context_builder._fallback_pg",
            new=AsyncMock(return_value=""),
        ):
            result = await build_memory_context(hass, "Alice", confidence=0.95)
        assert "## Alice's Preferences" in result
        assert "Music Taste" in result
        assert "## Household Rules" in result


# ---------------------------------------------------------------------------
# SpeakerSession serialization
# ---------------------------------------------------------------------------


class TestSpeakerSessionSerialization:
    def test_round_trip(self):
        from jane_conversation.brain.speaker import SpeakerSession

        s = SpeakerSession(user_name="Alice", conversation_id="conv-1", ts=1234.5, confidence=0.85)
        round_tripped = SpeakerSession.from_json(s.to_json())
        assert round_tripped == s

    def test_invalid_json_returns_none(self):
        from jane_conversation.brain.speaker import SpeakerSession

        assert SpeakerSession.from_json("not json") is None
        assert SpeakerSession.from_json('{"user_name": "Alice"}') is None  # missing fields
