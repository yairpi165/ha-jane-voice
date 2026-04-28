"""S3.0 (JANE-71) — speaker resolution tests.

Covers the full Notion S3.0 ladder:
- Layered resolve_speaker() Steps 0/1/2/3/5 — confidence values per Notion
  (1.0 / 0.85 / 0.95 / 0.8 / 0.5).
- Redis-down fallback for Step 2 (presence).
- write_speaker_session refresh threshold (only at confidence ≥ 0.7).
- Step 4 pending-ask state machine: round-trip + word-boundary match
  + ambiguous → None + integration ask→replay through execute_tool.
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
# Step 1 — Device area → sole resident
# ---------------------------------------------------------------------------


def _make_device(area_id):
    d = MagicMock()
    d.area_id = area_id
    return d


def _make_entity_entry(area_id=None, device_id=None):
    e = MagicMock()
    e.area_id = area_id
    e.device_id = device_id
    return e


class TestStep1DeviceArea:
    """D9 — `device_id → device_registry → area → sole resident` (confidence 0.85).

    Manually verified V4 on dev VM, but unit tests lock the layer against
    HA registry refactors.
    """

    @pytest.mark.asyncio
    async def test_device_in_area_with_sole_resident_resolves_at_0_85(self):
        from jane_conversation.brain import speaker, speaker_helpers

        device = _make_device(area_id="salon")
        dr_mock = MagicMock()
        dr_mock.async_get.return_value = MagicMock(async_get=MagicMock(return_value=device))
        ar_mock = MagicMock()
        ar_mock.async_get.return_value = MagicMock(async_get_area=MagicMock(return_value=MagicMock()))
        # Entity registry: person.alice has area="salon".
        ent_entry = _make_entity_entry(area_id="salon")
        er_mock = MagicMock()
        er_mock.async_get.return_value = MagicMock(async_get=MagicMock(return_value=ent_entry))

        alice = _make_person_state("person.alice", "home", "Alice")
        hass = _make_hass(jane_data=_make_jane_data(redis=None), person_states=[alice])
        with (
            patch.object(speaker, "dr", dr_mock),
            patch.object(speaker_helpers, "dr", dr_mock),
            patch.object(speaker_helpers, "ar", ar_mock),
            patch.object(speaker_helpers, "er", er_mock),
        ):
            name, conf, layer = await speaker.resolve_speaker(hass, "device-X", "conv-1", None)
        assert (name, conf, layer) == ("Alice", 0.85, "step_1")

    @pytest.mark.asyncio
    async def test_device_with_no_area_falls_through(self):
        from jane_conversation.brain import speaker

        device = _make_device(area_id=None)
        dr_mock = MagicMock()
        dr_mock.async_get.return_value = MagicMock(async_get=MagicMock(return_value=device))
        # No one home → fall through past Step 2 to Step 5.
        hass = _make_hass(jane_data=_make_jane_data(redis=None), person_states=[])
        with patch.object(speaker, "dr", dr_mock):
            name, conf, layer = await speaker.resolve_speaker(hass, "device-X", "conv-1", None)
        assert layer != "step_1"

    @pytest.mark.asyncio
    async def test_area_with_multiple_residents_falls_through(self):
        from jane_conversation.brain import speaker, speaker_helpers

        device = _make_device(area_id="salon")
        dr_mock = MagicMock()
        dr_mock.async_get.return_value = MagicMock(async_get=MagicMock(return_value=device))
        ar_mock = MagicMock()
        ar_mock.async_get.return_value = MagicMock(async_get_area=MagicMock(return_value=MagicMock()))
        ent_entry = _make_entity_entry(area_id="salon")
        er_mock = MagicMock()
        er_mock.async_get.return_value = MagicMock(async_get=MagicMock(return_value=ent_entry))

        alice = _make_person_state("person.alice", "home", "Alice")
        bob = _make_person_state("person.bob", "home", "Bob")
        hass = _make_hass(jane_data=_make_jane_data(redis=None), person_states=[alice, bob])
        with (
            patch.object(speaker, "dr", dr_mock),
            patch.object(speaker_helpers, "dr", dr_mock),
            patch.object(speaker_helpers, "ar", ar_mock),
            patch.object(speaker_helpers, "er", er_mock),
        ):
            name, conf, layer = await speaker.resolve_speaker(hass, "device-X", "conv-1", None)
        assert layer != "step_1"

    @pytest.mark.asyncio
    async def test_area_with_zero_residents_falls_through(self):
        from jane_conversation.brain import speaker, speaker_helpers

        device = _make_device(area_id="salon")
        dr_mock = MagicMock()
        dr_mock.async_get.return_value = MagicMock(async_get=MagicMock(return_value=device))
        ar_mock = MagicMock()
        ar_mock.async_get.return_value = MagicMock(async_get_area=MagicMock(return_value=MagicMock()))
        # Entity registry returns a different area for every person — no one in salon.
        ent_entry = _make_entity_entry(area_id="kitchen")
        er_mock = MagicMock()
        er_mock.async_get.return_value = MagicMock(async_get=MagicMock(return_value=ent_entry))

        alice = _make_person_state("person.alice", "home", "Alice")
        hass = _make_hass(jane_data=_make_jane_data(redis=None), person_states=[alice])
        with (
            patch.object(speaker, "dr", dr_mock),
            patch.object(speaker_helpers, "dr", dr_mock),
            patch.object(speaker_helpers, "ar", ar_mock),
            patch.object(speaker_helpers, "er", er_mock),
        ):
            name, conf, layer = await speaker.resolve_speaker(hass, "device-X", "conv-1", None)
        assert layer != "step_1"


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
        assert conf == 0.5

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
    """Notion S3.0: room session in Redis (<TTL 15m) → 0.8 fixed. No decay."""

    @pytest.mark.asyncio
    async def test_session_within_ttl_returns_0_8(self):
        from jane_conversation.brain.speaker import resolve_speaker

        # Session written 30s ago. Persisted confidence is irrelevant — any
        # active session yields 0.8 fixed (TTL is the cliff).
        session_blob = json.dumps(
            {
                "user_name": "Alice",
                "conversation_id": "old-conv",
                "ts": time.time() - 30,
                "confidence": 0.95,
            }
        )
        redis = AsyncMock()
        redis.hgetall.return_value = {}  # no presence
        redis.get.return_value = session_blob
        hass = _make_hass(jane_data=_make_jane_data(redis=redis), person_states=[])
        name, conf, layer = await resolve_speaker(hass, "device-X", "new-conv", None)
        assert (name, conf, layer) == ("Alice", 0.8, "step_3")

    @pytest.mark.asyncio
    async def test_session_persisted_low_confidence_still_returns_0_8(self):
        """Even if a low confidence was somehow persisted, read returns 0.8."""
        from jane_conversation.brain.speaker import resolve_speaker

        session_blob = json.dumps(
            {
                "user_name": "Alice",
                "conversation_id": "old-conv",
                "ts": time.time() - 30,
                "confidence": 0.55,
            }
        )
        redis = AsyncMock()
        redis.hgetall.return_value = {}
        redis.get.return_value = session_blob
        hass = _make_hass(jane_data=_make_jane_data(redis=redis), person_states=[])
        name, conf, layer = await resolve_speaker(hass, "device-X", "new-conv", None)
        assert layer == "step_3"
        assert conf == 0.8

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
    """Notion S3.0: Step 5 fallback to primary_user → 0.5."""

    @pytest.mark.asyncio
    async def test_returns_primary_user_at_0_5(self):
        from jane_conversation.brain.speaker import resolve_speaker

        structured = AsyncMock()
        structured.load_persons.return_value = [
            {"name": "Charlie", "metadata": {"is_primary": True}},
            {"name": "Daisy", "metadata": {}},
        ]
        hass = _make_hass(jane_data=_make_jane_data(structured=structured), person_states=[])
        name, conf, layer = await resolve_speaker(hass, None, "conv-1", None)
        assert (name, conf, layer) == ("Charlie", 0.5, "step_5")

    @pytest.mark.asyncio
    async def test_returns_default_name_when_no_primary_user(self):
        from jane_conversation.brain.speaker import resolve_speaker

        structured = AsyncMock()
        structured.load_persons.return_value = [{"name": "Alice", "metadata": {}}]
        hass = _make_hass(jane_data=_make_jane_data(structured=structured), person_states=[])
        name, conf, layer = await resolve_speaker(hass, None, "conv-1", None)
        assert (name, conf, layer) == ("default", 0.5, "step_5")


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
# Step 4 — Pending-ask state machine
# ---------------------------------------------------------------------------


class TestPendingAsk:
    """Round-trip + match_known_person semantics."""

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
        assert pending["conversation_id"] == "conv-1"
        await clear_pending_ask(hass, "device-X")
        assert await check_pending_ask(hass, "device-X") is None

    @pytest.mark.asyncio
    async def test_match_known_person_exact_match(self):
        from jane_conversation.brain.speaker_pending_ask import match_known_person

        structured = AsyncMock()
        structured.load_persons.return_value = [{"name": "Alice"}, {"name": "Bob"}]
        hass = _make_hass(jane_data=_make_jane_data(structured=structured))
        assert await match_known_person(hass, "Alice") == "Alice"
        assert await match_known_person(hass, "this is alice speaking") == "Alice"
        assert await match_known_person(hass, "Bob") == "Bob"

    @pytest.mark.asyncio
    async def test_match_known_person_word_boundary(self):
        """Reviewer fix: no false-positive on substrings like 'al' inside 'alice'."""
        from jane_conversation.brain.speaker_pending_ask import match_known_person

        structured = AsyncMock()
        # Short nickname 'Al' must not match a reply containing 'alice'.
        structured.load_persons.return_value = [{"name": "Al"}, {"name": "Bob"}]
        hass = _make_hass(jane_data=_make_jane_data(structured=structured))
        assert await match_known_person(hass, "alice was here") is None
        assert await match_known_person(hass, "Al") == "Al"
        assert await match_known_person(hass, "I'm Al actually") == "Al"

    @pytest.mark.asyncio
    async def test_match_known_person_ambiguous_returns_none(self):
        """Reviewer fix: multiple matches → ambiguous → None (caller re-asks)."""
        from jane_conversation.brain.speaker_pending_ask import match_known_person

        structured = AsyncMock()
        structured.load_persons.return_value = [{"name": "Alice"}, {"name": "Bob"}]
        hass = _make_hass(jane_data=_make_jane_data(structured=structured))
        # Both names appear → fail closed rather than guess first-in-iteration.
        assert await match_known_person(hass, "I'm not Bob, I'm Alice") is None

    @pytest.mark.asyncio
    async def test_match_known_person_no_match_returns_none(self):
        from jane_conversation.brain.speaker_pending_ask import match_known_person

        structured = AsyncMock()
        structured.load_persons.return_value = [{"name": "Alice"}]
        hass = _make_hass(jane_data=_make_jane_data(structured=structured))
        assert await match_known_person(hass, "nobody") is None
        assert await match_known_person(hass, "") is None

    @pytest.mark.asyncio
    async def test_match_known_person_hebrew_word_boundary(self):
        """Hebrew names: 'אבי' must not match inside 'אביב' (substring inside word)."""
        from jane_conversation.brain.speaker_pending_ask import match_known_person

        structured = AsyncMock()
        structured.load_persons.return_value = [{"name": "אבי"}, {"name": "דנה"}]
        hass = _make_hass(jane_data=_make_jane_data(structured=structured))
        # No match — 'אבי' is a strict prefix of 'אביב' (no \W between).
        assert await match_known_person(hass, "אביב היה כאן") is None
        # Match — name is its own word.
        assert await match_known_person(hass, "אני אבי") == "אבי"
        assert await match_known_person(hass, "מדברת דנה") == "דנה"

    @pytest.mark.asyncio
    async def test_match_known_person_redis_down_returns_none(self):
        """Edge case: structured store unavailable → fail closed (no guess)."""
        from jane_conversation.brain.speaker_pending_ask import match_known_person

        hass = _make_hass(jane_data=_make_jane_data(structured=None))
        assert await match_known_person(hass, "Alice") is None

    @pytest.mark.asyncio
    async def test_check_pending_ask_returns_none_when_no_device_id(self):
        from jane_conversation.brain.speaker_pending_ask import check_pending_ask

        redis = AsyncMock()
        hass = _make_hass(jane_data=_make_jane_data(redis=redis))
        assert await check_pending_ask(hass, None) is None
        redis.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_pending_ask_returns_none_when_redis_down(self):
        from jane_conversation.brain.speaker_pending_ask import check_pending_ask

        redis = AsyncMock()
        redis.get.side_effect = ConnectionError("redis down")
        hass = _make_hass(jane_data=_make_jane_data(redis=redis))
        assert await check_pending_ask(hass, "device-X") is None

    @pytest.mark.asyncio
    async def test_set_pending_ask_silent_on_redis_failure(self):
        """Redis failure must not propagate — write is best-effort."""
        from jane_conversation.brain.speaker_pending_ask import set_pending_ask

        redis = AsyncMock()
        redis.set.side_effect = ConnectionError("redis down")
        hass = _make_hass(jane_data=_make_jane_data(redis=redis))
        # Must not raise.
        await set_pending_ask(hass, "device-X", "conv-1", "x")


# ---------------------------------------------------------------------------
# Step 4 — End-to-end trigger through execute_tool + engine
# ---------------------------------------------------------------------------


class TestStep4Trigger:
    """When the gate denies a sensitive call at low confidence + device_id is
    known, `execute_tool` writes a pending-ask payload to Redis and raises
    `SpeakerAskRequired`. The engine catches and emits "מי מדבר?".
    """

    @pytest.mark.asyncio
    async def test_low_conf_sensitive_call_with_device_id_raises(self):
        from jane_conversation.brain.speaker_pending_ask import SpeakerAskRequired
        from jane_conversation.tools import execute_tool

        store: dict[str, str] = {}

        async def fake_set(key, value, ex=None):
            store[key] = value

        redis = AsyncMock()
        redis.set.side_effect = fake_set

        policies = MagicMock()
        policies.check_permission = AsyncMock(return_value="זיהוי לא בטוח — אנא אשר את הפעולה")
        jane_data = MagicMock()
        jane_data.policies = policies
        jane_data.redis = redis

        hass = MagicMock()
        hass.data = {"jane_conversation": jane_data}

        with pytest.raises(SpeakerAskRequired):
            await execute_tool(
                hass,
                "set_automation",
                {},
                user_name="default",
                confidence=0.5,
                device_id="device-X",
                conversation_id="conv-1",
                original_request="הדלק את האזעקה",
            )
        # Pending-ask payload was written before raising.
        assert "jane:pending_speaker_ask:device-X" in store
        payload = json.loads(store["jane:pending_speaker_ask:device-X"])
        assert payload["original_request"] == "הדלק את האזעקה"
        assert payload["conversation_id"] == "conv-1"

    @pytest.mark.asyncio
    async def test_low_conf_sensitive_call_without_device_id_returns_deny(self):
        """No device_id → no Redis key for replay → fall back to deny string."""
        from jane_conversation.tools import execute_tool

        deny_msg = "זיהוי לא בטוח — אנא אשר את הפעולה"
        policies = MagicMock()
        policies.check_permission = AsyncMock(return_value=deny_msg)
        jane_data = MagicMock()
        jane_data.policies = policies
        hass = MagicMock()
        hass.data = {"jane_conversation": jane_data}

        result = await execute_tool(
            hass,
            "set_automation",
            {},
            user_name="default",
            confidence=0.5,
            device_id=None,
            original_request="x",
        )
        assert result == deny_msg

    @pytest.mark.asyncio
    async def test_high_conf_sensitive_call_passes_no_ask(self):
        """At conf ≥ 0.7 the gate doesn't deny → no SpeakerAskRequired raised."""
        from jane_conversation.tools import execute_tool

        policies = MagicMock()
        policies.check_permission = AsyncMock(return_value=None)  # allowed
        jane_data = MagicMock()
        jane_data.policies = policies
        jane_data.redis = AsyncMock()
        hass = MagicMock()
        hass.data = {"jane_conversation": jane_data}

        # Won't raise — handler runs (set_automation handler returns some string).
        result = await execute_tool(
            hass,
            "set_automation",
            {"object_id": "x", "config": {}},
            user_name="Alice",
            confidence=0.95,
            device_id="device-X",
            original_request="x",
        )
        # Whatever set_automation returned — it's not the ask prompt.
        assert result != "מי מדבר?"

    @pytest.mark.asyncio
    async def test_role_deny_at_high_conf_returns_deny_no_ask(self):
        """Role-based deny (child trying set_automation) at conf=1.0 must NOT
        trigger Step 4. Asking 'מי מדבר?' wouldn't change a role gate.
        """
        from jane_conversation.tools import execute_tool

        store: dict[str, str] = {}

        async def fake_set(key, value, ex=None):
            store[key] = value

        role_deny_msg = "פעולה זו דורשת אישור מהורה"
        policies = MagicMock()
        policies.check_permission = AsyncMock(return_value=role_deny_msg)
        jane_data = MagicMock()
        jane_data.policies = policies
        redis = AsyncMock()
        redis.set.side_effect = fake_set
        jane_data.redis = redis

        hass = MagicMock()
        hass.data = {"jane_conversation": jane_data}

        result = await execute_tool(
            hass,
            "set_automation",
            {},
            user_name="Bob",  # child
            confidence=1.0,  # correctly identified
            device_id="device-X",
            conversation_id="conv-1",
            original_request="x",
        )
        # Role deny string returned; ASK was NOT triggered.
        assert result == role_deny_msg
        assert "jane:pending_speaker_ask:device-X" not in store

    @pytest.mark.asyncio
    async def test_quiet_hours_deny_at_high_conf_returns_deny_no_ask(self):
        """Same shape for quiet-hours: high conf + non-confidence deny → return string, no ask."""
        from jane_conversation.tools import execute_tool

        store: dict[str, str] = {}

        async def fake_set(key, value, ex=None):
            store[key] = value

        quiet_deny_msg = "שעות שקט: 23:00–07:00"
        policies = MagicMock()
        policies.check_permission = AsyncMock(return_value=quiet_deny_msg)
        jane_data = MagicMock()
        jane_data.policies = policies
        redis = AsyncMock()
        redis.set.side_effect = fake_set
        jane_data.redis = redis

        hass = MagicMock()
        hass.data = {"jane_conversation": jane_data}

        result = await execute_tool(
            hass,
            "tts_announce",
            {"message": "hi"},
            user_name="Alice",
            confidence=1.0,  # correctly identified, but quiet hours
            device_id="device-X",
            conversation_id="conv-1",
            original_request="x",
        )
        assert result == quiet_deny_msg
        assert "jane:pending_speaker_ask:device-X" not in store

    @pytest.mark.asyncio
    async def test_step_4_replay_at_0_85_passes_gate_for_sensitive(self):
        """After ask→replay at conf 0.85, the same sensitive call must pass the gate.

        (Step 4 brings us to 0.85, which is ≥ 0.7 SENSITIVE threshold and ≥ 0.5
        PERSONAL_DATA threshold — the recovered turn should not re-trigger an ask.)
        """
        from jane_conversation.brain.speaker_pending_ask import SpeakerAskRequired
        from jane_conversation.memory.policy import PolicyStore
        from jane_conversation.tools import execute_tool

        # Use the real PolicyStore so we exercise the same threshold logic
        # the prod path runs.
        pool = MagicMock()
        conn = AsyncMock()
        conn.fetch.return_value = [{"key": "role", "value": "admin"}]
        pool.acquire.return_value.__aenter__.return_value = conn
        store = PolicyStore(pool)

        jane_data = MagicMock()
        jane_data.policies = store
        jane_data.redis = AsyncMock()
        hass = MagicMock()
        hass.data = {"jane_conversation": jane_data}

        # confidence=0.85 (post-recovery). Must NOT raise.
        try:
            await execute_tool(
                hass,
                "forget_memory",
                {"target": "preferences", "key": {"person": "Alice", "key": "x"}},
                user_name="Alice",
                confidence=0.85,
                device_id="device-X",
                original_request="תשכחי ש...",
            )
        except SpeakerAskRequired:
            pytest.fail("Step 4 replay at 0.85 should pass the gate, not re-trigger ask")


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
        # `read_memory` is a real tool in PERSONAL_DATA_ACTIONS (post-review fix).
        result = await store.check_permission("Alice", "read_memory", confidence=0.3)
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
