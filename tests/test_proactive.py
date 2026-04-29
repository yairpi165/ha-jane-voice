"""Tests for Proactive Triggers Tier 1 + Decision Logging — S3.2 (JANE-45).

Covers:

- ``_parse_proactive_payload`` happy + fallback paths (D2).
- ``is_proactive_message`` boundary cases.
- ``route_alert`` 3 × 4 × 7 routing table (D6, D8).
- ``check_speech_budget`` / ``increment_speech_budget`` local-TZ counter
  failure-soft semantics (D4, D13).
- ``check_dismissal_streak`` 3-strike contract (D4, D5).
- ``record_proactive_decision`` insert shape + RETURNING id (D3).
- ``handle_log_proactive_decision`` budget-advance gating (D4 + safety
  bypass D8).
- ``user_overrides.proactive_decision_id`` FK present in jane bootstrap +
  scripts/schema.sql (D5).
- ``_strip_proactive_prefix`` defensive strip in TTS / notify (D14).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jane_conversation.brain.proactive import (
    ProactivePayload,
    _parse_proactive_payload,
    check_dismissal_streak,
    check_speech_budget,
    increment_speech_budget,
    is_proactive_message,
    route_alert,
)
from jane_conversation.memory.proactive_decisions import record_proactive_decision
from jane_conversation.modes import (
    HOUSEHOLD_MODES,
    MODE_AWAY,
    MODE_GUESTS,
    MODE_KIDS_SLEEPING,
    MODE_NIGHT,
    MODE_NORMAL,
    MODE_RULES,
    MODE_TRAVEL,
    MODE_WORK,
)
from jane_conversation.tools.handlers.family import _strip_proactive_prefix
from jane_conversation.tools.handlers.proactive import handle_log_proactive_decision

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hass_with_mode(mode: str) -> MagicMock:
    """Mock hass whose select.jane_household_mode resolves to ``mode``."""
    hass = MagicMock()
    state = MagicMock()
    state.state = mode
    hass.states.get = MagicMock(return_value=state)
    return hass


def _mock_pool() -> tuple[MagicMock, AsyncMock]:
    """asyncpg pool mock — same shape as test_household_mode.py."""
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn


# ---------------------------------------------------------------------------
# 1. Parse helper (D2)
# ---------------------------------------------------------------------------


class TestProactivePayloadParser:
    """``_parse_proactive_payload`` resolves all four fields with fallbacks.

    The parser is the only place [PROACTIVE] strings turn into structured
    data — every fallback rule (missing Time / Mode / description, bogus
    HH:MM) is exercised here so a regression can't silently change which
    operator paste produces a usable payload vs a drop.
    """

    def test_happy_path_full_payload(self):
        hass = _hass_with_mode(MODE_NORMAL)
        text = "[PROACTIVE] Alice arrived. Time: 14:30. Mode: רגיל."
        payload = _parse_proactive_payload(text, hass)
        assert payload is not None
        assert payload.description == "Alice arrived"
        assert payload.time_str == "14:30"
        assert payload.mode == MODE_NORMAL
        assert payload.person == "Alice"

    def test_missing_mode_falls_back_to_active(self):
        # No Mode marker → parser reads from helper. Operator might paste a
        # template that omits Mode; the household state is the fallback.
        hass = _hass_with_mode(MODE_NIGHT)
        text = "[PROACTIVE] Bob arrived. Time: 23:45."
        payload = _parse_proactive_payload(text, hass)
        assert payload is not None
        assert payload.mode == MODE_NIGHT

    def test_unknown_mode_in_message_falls_back_to_active(self):
        # Mode value not in MODE_RULES → treat as missing, fall back. Same
        # contract as the gate (test_household_mode.test_unknown_mode_falls_back).
        hass = _hass_with_mode(MODE_NORMAL)
        text = "[PROACTIVE] Alice arrived. Time: 14:30. Mode: ערפילי."
        payload = _parse_proactive_payload(text, hass)
        assert payload is not None
        assert payload.mode == MODE_NORMAL

    def test_missing_time_falls_back_to_now(self):
        hass = _hass_with_mode(MODE_NORMAL)
        text = "[PROACTIVE] Alice arrived. Mode: רגיל."
        with patch("jane_conversation.brain.proactive.dt_util") as dt_mock:
            dt_mock.now.return_value.strftime.return_value = "09:15"
            payload = _parse_proactive_payload(text, hass)
        assert payload is not None
        assert payload.time_str == "09:15"

    def test_bogus_hhmm_falls_back_to_now(self):
        # 25:99 must not produce a payload claiming "25:99" — the operator
        # might paste a malformed Jinja result. Parser falls back silently.
        hass = _hass_with_mode(MODE_NORMAL)
        text = "[PROACTIVE] Alice arrived. Time: 25:99. Mode: רגיל."
        with patch("jane_conversation.brain.proactive.dt_util") as dt_mock:
            dt_mock.now.return_value.strftime.return_value = "12:00"
            payload = _parse_proactive_payload(text, hass)
        assert payload is not None
        assert payload.time_str == "12:00"

    def test_no_description_no_time_returns_none(self):
        # Bare "[PROACTIVE]" with nothing actionable → drop. Caller will
        # write a "dropped_malformed_payload" audit row.
        hass = _hass_with_mode(MODE_NORMAL)
        assert _parse_proactive_payload("[PROACTIVE]", hass) is None
        assert _parse_proactive_payload("[PROACTIVE]   ", hass) is None
        assert _parse_proactive_payload("[PROACTIVE] Mode: רגיל.", hass) is None

    def test_description_without_time_allowed(self):
        # Description present + Time absent → allow, fall back on time.
        # Jane can still reason about "Alice arrived" without a clock.
        hass = _hass_with_mode(MODE_NORMAL)
        text = "[PROACTIVE] Alice arrived."
        with patch("jane_conversation.brain.proactive.dt_util") as dt_mock:
            dt_mock.now.return_value.strftime.return_value = "10:00"
            payload = _parse_proactive_payload(text, hass)
        assert payload is not None
        assert payload.description == "Alice arrived"

    def test_non_proactive_text_returns_none(self):
        hass = _hass_with_mode(MODE_NORMAL)
        assert _parse_proactive_payload("Hello", hass) is None
        assert _parse_proactive_payload("", hass) is None

    def test_canonical_trigger_extracted(self):
        # The Trigger: field is the canonical key tying dispatch streak gate
        # to user_overrides.action_type. It must be parsed cleanly so the
        # contract holds across the full chain.
        hass = _hass_with_mode(MODE_NORMAL)
        text = "[PROACTIVE] Alice arrived. Time: 14:30. Mode: רגיל. Trigger: arrival."
        payload = _parse_proactive_payload(text, hass)
        assert payload is not None
        assert payload.trigger == "arrival"

    def test_canonical_trigger_lowercased(self):
        # Operators may type Trigger: ARRIVAL or Trigger: All_Away — the parser
        # normalises so the streak query (case-sensitive PG comparison) matches.
        hass = _hass_with_mode(MODE_NORMAL)
        text = "[PROACTIVE] All away. Time: 14:30. Mode: רגיל. Trigger: All_Away."
        payload = _parse_proactive_payload(text, hass)
        assert payload is not None
        assert payload.trigger == "all_away"

    def test_missing_trigger_falls_back_to_unknown(self):
        # Missing `Trigger:` shouldn't drop the turn (operator may have an
        # older YAML); fall back to 'unknown' and log debug. The streak gate
        # then no-ops for this trigger, but the rest of the flow proceeds.
        hass = _hass_with_mode(MODE_NORMAL)
        text = "[PROACTIVE] Alice arrived. Time: 14:30. Mode: רגיל."
        payload = _parse_proactive_payload(text, hass)
        assert payload is not None
        assert payload.trigger == "unknown"


# ---------------------------------------------------------------------------
# 2. is_proactive_message — gate predicate
# ---------------------------------------------------------------------------


class TestIsProactiveMessage:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("[PROACTIVE] anything", True),
            ("   [PROACTIVE] leading whitespace", True),
            ("[PROACTIVE]", True),
            ("Hello", False),
            ("", False),
            ("PROACTIVE without brackets", False),
            ("user said [PROACTIVE]", False),  # not at start
        ],
    )
    def test_detection(self, text: str, expected: bool):
        assert is_proactive_message(text) is expected


# ---------------------------------------------------------------------------
# 3. route_alert — 3 × 4 × 7 matrix (D6, D8)
# ---------------------------------------------------------------------------


class TestRouteAlert:
    """Pure routing decision. Three observation-class action types, four
    urgencies, seven modes — 84 cells, asserted symbolically against
    MODE_RULES so a future flag flip propagates here automatically.
    """

    @pytest.mark.parametrize("action_type", ["arrival", "all_away_30min", "goodnight"])
    @pytest.mark.parametrize("urgency", ["normal", "low", "high", "critical"])
    @pytest.mark.parametrize("mode", HOUSEHOLD_MODES)
    def test_matrix(self, action_type: str, urgency: str, mode: str):
        result = route_alert(action_type, urgency, mode)
        # D8: critical urgency always speaks (safety bypass).
        if urgency == "critical":
            assert result == "voice"
            return
        # D6: tts=False mode demotes voice to notification.
        if MODE_RULES[mode]["tts"] is False:
            assert result == "notification"
        else:
            assert result == "voice"

    def test_unknown_mode_defaults_to_voice(self):
        # Defensive: parser feeds NORMAL on unknown, but if route_alert is
        # ever called with a foreign mode it must not crash.
        assert route_alert("arrival", "normal", "ערפילי") == "voice"


# ---------------------------------------------------------------------------
# 4. Speech budget (D4, D13)
# ---------------------------------------------------------------------------


class TestSpeechBudget:
    """Daily Redis counter. Failure-soft: any error must allow speech —
    silencing every proactive turn because Redis hiccupped is the worse
    failure mode.
    """

    @pytest.mark.asyncio
    async def test_no_redis_allows(self):
        assert await check_speech_budget(MagicMock(), None) is True

    @pytest.mark.asyncio
    async def test_redis_missing_key_allows(self):
        hass = MagicMock()
        hass.config.time_zone = "Asia/Jerusalem"
        redis = AsyncMock()
        redis.get.return_value = None
        with patch("jane_conversation.brain.proactive._local_day_key", return_value="k"):
            assert await check_speech_budget(hass, redis) is True

    @pytest.mark.asyncio
    async def test_under_cap_allows(self):
        hass = MagicMock()
        redis = AsyncMock()
        redis.get.return_value = b"1"  # 1 < cap (2)
        with patch("jane_conversation.brain.proactive._local_day_key", return_value="k"):
            assert await check_speech_budget(hass, redis) is True

    @pytest.mark.asyncio
    async def test_at_cap_blocks(self):
        hass = MagicMock()
        redis = AsyncMock()
        redis.get.return_value = b"2"
        with patch("jane_conversation.brain.proactive._local_day_key", return_value="k"):
            assert await check_speech_budget(hass, redis) is False

    @pytest.mark.asyncio
    async def test_redis_error_allows(self):
        hass = MagicMock()
        redis = AsyncMock()
        redis.get.side_effect = RuntimeError("connection lost")
        with patch("jane_conversation.brain.proactive._local_day_key", return_value="k"):
            assert await check_speech_budget(hass, redis) is True

    @pytest.mark.asyncio
    async def test_increment_sets_ttl_on_first_call(self):
        hass = MagicMock()
        redis = AsyncMock()
        redis.incr.return_value = 1  # first set-of-day
        with patch("jane_conversation.brain.proactive._local_day_key", return_value="k"):
            await increment_speech_budget(hass, redis)
        redis.incr.assert_awaited_once_with("k")
        redis.expire.assert_awaited_once()
        # 26h TTL — buffer past local-day for clock-skew resilience.
        assert redis.expire.await_args.args[1] == 26 * 3600

    @pytest.mark.asyncio
    async def test_increment_skips_ttl_after_first(self):
        hass = MagicMock()
        redis = AsyncMock()
        redis.incr.return_value = 2  # second increment of the day
        with patch("jane_conversation.brain.proactive._local_day_key", return_value="k"):
            await increment_speech_budget(hass, redis)
        redis.expire.assert_not_awaited()


# ---------------------------------------------------------------------------
# 5. Dismissal streak (D4, D5)
# ---------------------------------------------------------------------------


class TestDismissalStreak:
    """3-strike suppression: only when the last 3 overrides for an action
    type are all dismissals. Mixed history means the user still cared
    enough to actively override — different action, same engagement.
    """

    @pytest.mark.asyncio
    async def test_no_pool_allows(self):
        assert await check_dismissal_streak(None, "arrival") is True

    @pytest.mark.asyncio
    async def test_three_dismissals_blocks(self):
        pool, conn = _mock_pool()
        conn.fetch.return_value = [
            {"override_type": "dismissed"},
            {"override_type": "dismissed"},
            {"override_type": "dismissed"},
        ]
        assert await check_dismissal_streak(pool, "arrival") is False

    @pytest.mark.asyncio
    async def test_mixed_history_allows(self):
        pool, conn = _mock_pool()
        conn.fetch.return_value = [
            {"override_type": "dismissed"},
            {"override_type": "reversed"},
            {"override_type": "dismissed"},
        ]
        assert await check_dismissal_streak(pool, "arrival") is True

    @pytest.mark.asyncio
    async def test_fewer_than_three_allows(self):
        pool, conn = _mock_pool()
        conn.fetch.return_value = [{"override_type": "dismissed"}, {"override_type": "dismissed"}]
        assert await check_dismissal_streak(pool, "arrival") is True

    @pytest.mark.asyncio
    async def test_pg_error_allows(self):
        pool = MagicMock()
        pool.acquire.side_effect = RuntimeError("pool exhausted")
        assert await check_dismissal_streak(pool, "arrival") is True


# ---------------------------------------------------------------------------
# 6. record_proactive_decision (D3)
# ---------------------------------------------------------------------------


class TestProactiveDecisionWriter:
    @pytest.mark.asyncio
    async def test_returns_none_when_pool_unset(self):
        # Fresh integration startup before the pool wires up — must not raise.
        result = await record_proactive_decision(
            None,
            trigger="arrival",
            mode=MODE_NORMAL,
            action_taken="suppressed_by_mode",
            reasoning="x",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_event_id_on_success(self):
        pool, conn = _mock_pool()
        conn.fetchval.return_value = 42
        result = await record_proactive_decision(
            pool,
            trigger="arrival",
            mode=MODE_NORMAL,
            action_taken="greeted",
            reasoning="happy path",
            person="Alice",
            urgency="normal",
            routed_via="voice",
        )
        assert result == 42

    @pytest.mark.asyncio
    async def test_metadata_carries_all_fields(self):
        # KPI queries group/filter on metadata JSONB. Every structured field
        # must round-trip — a missing key would silently break a query.
        pool, conn = _mock_pool()
        conn.fetchval.return_value = 7
        await record_proactive_decision(
            pool,
            trigger="all_away_30min",
            mode=MODE_AWAY,
            action_taken="suppressed_by_mode",
            reasoning="r",
            person="Bob",
            urgency="critical",
            routed_via=None,
        )
        # fetchval positional args: (sql, ts, person, description, metadata).
        # event_type is hardcoded in the SQL, so args[4] = metadata JSON.
        call_args = conn.fetchval.await_args.args
        metadata = json.loads(call_args[4])
        assert metadata == {
            "trigger": "all_away_30min",
            "mode": MODE_AWAY,
            "action_taken": "suppressed_by_mode",
            "reasoning": "r",
            "person": "Bob",
            "urgency": "critical",
            "routed_via": None,
        }

    @pytest.mark.asyncio
    async def test_description_format(self):
        pool, conn = _mock_pool()
        conn.fetchval.return_value = 1
        await record_proactive_decision(
            pool,
            trigger="goodnight",
            mode=MODE_NORMAL,
            action_taken="set_mode_night",
            reasoning="r",
            routed_via="voice",
        )
        # fetchval positional args: (sql, ts, person, description, metadata).
        description = conn.fetchval.await_args.args[3]
        assert description == "goodnight → set_mode_night (mode=רגיל, routed=voice)"

    @pytest.mark.asyncio
    async def test_pg_error_returns_none(self):
        pool = MagicMock()
        pool.acquire.side_effect = RuntimeError("pool exhausted")
        result = await record_proactive_decision(pool, trigger="x", mode=MODE_NORMAL, action_taken="y", reasoning="z")
        assert result is None


# ---------------------------------------------------------------------------
# 7. handle_log_proactive_decision (D4 + safety bypass D8)
# ---------------------------------------------------------------------------


class TestLogProactiveDecisionTool:
    @pytest.mark.asyncio
    async def test_voice_normal_advances_budget(self):
        # The exact rule: voice route + non-critical urgency consumes one
        # daily speech token. Without this row the budget never advances and
        # Jane could quietly speak more than 2/day — slow trust break.
        hass = _hass_with_mode(MODE_NORMAL)
        jane = MagicMock()
        jane.pg_pool = None
        jane.redis = AsyncMock()
        hass.data = {"jane_conversation": jane}
        with patch("jane_conversation.tools.handlers.proactive.increment_speech_budget") as inc:
            inc.return_value = None
            await handle_log_proactive_decision(
                hass,
                {
                    "trigger": "arrival",
                    "action_taken": "greeted",
                    "reasoning": "r",
                    "urgency": "normal",
                    "routed_via": "voice",
                },
            )
        inc.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_notification_route_does_not_advance(self):
        hass = _hass_with_mode(MODE_NORMAL)
        jane = MagicMock()
        jane.pg_pool = None
        jane.redis = AsyncMock()
        hass.data = {"jane_conversation": jane}
        with patch("jane_conversation.tools.handlers.proactive.increment_speech_budget") as inc:
            await handle_log_proactive_decision(
                hass,
                {
                    "trigger": "arrival",
                    "action_taken": "notified",
                    "reasoning": "r",
                    "urgency": "normal",
                    "routed_via": "notification",
                },
            )
        inc.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_critical_voice_bypasses_budget(self):
        # D8: critical urgency is for SAFETY ONLY. Speech here is mandatory
        # and the budget must NOT count it — otherwise a smoke alarm on a
        # day Jane has already greeted twice would silently downgrade.
        hass = _hass_with_mode(MODE_NIGHT)
        jane = MagicMock()
        jane.pg_pool = None
        jane.redis = AsyncMock()
        hass.data = {"jane_conversation": jane}
        with patch("jane_conversation.tools.handlers.proactive.increment_speech_budget") as inc:
            await handle_log_proactive_decision(
                hass,
                {
                    "trigger": "smoke_detector",
                    "action_taken": "alerted",
                    "reasoning": "safety",
                    "urgency": "critical",
                    "routed_via": "voice",
                },
            )
        inc.assert_not_awaited()


# ---------------------------------------------------------------------------
# 8. user_overrides FK schema (D5)
# ---------------------------------------------------------------------------


class TestUserOverrideFKSchema:
    """Both the runtime bootstrap and the reference SQL ship the FK ALTER.
    A schema-level test catches a future contributor dropping the DDL from
    one place and not the other (the kind of drift that's invisible until
    a fresh install fails on the 30-day KPI query).
    """

    REPO_ROOT = Path(__file__).resolve().parent.parent

    def test_init_py_has_alter(self):
        text = (self.REPO_ROOT / "custom_components/jane_conversation/__init__.py").read_text()
        assert "proactive_decision_id" in text
        assert "REFERENCES events(id) ON DELETE SET NULL" in text
        assert "ADD COLUMN IF NOT EXISTS" in text  # idempotent migration

    def test_schema_sql_has_alter(self):
        text = (self.REPO_ROOT / "scripts/schema.sql").read_text()
        assert "proactive_decision_id" in text
        assert "ON DELETE SET NULL" in text


# ---------------------------------------------------------------------------
# 9. Defensive [PROACTIVE] strip (D14)
# ---------------------------------------------------------------------------


class TestDefensiveStrip:
    """SYSTEM_PROMPT teaches Jane never to echo the tag; this filter is the
    belt-and-braces. If the LLM ever drifts and tries, the strip fires AND
    logs a warning so the drift is visible in dev VM logs.
    """

    def test_strip_removes_prefix(self):
        assert _strip_proactive_prefix("[PROACTIVE] hello") == "hello"

    def test_strip_handles_leading_whitespace(self):
        assert _strip_proactive_prefix("   [PROACTIVE] hello") == "hello"

    def test_no_prefix_unchanged(self):
        assert _strip_proactive_prefix("hello world") == "hello world"

    def test_empty_unchanged(self):
        assert _strip_proactive_prefix("") == ""
        assert _strip_proactive_prefix(None) is None  # type: ignore[arg-type]

    def test_strip_logs_warning(self, caplog):
        # Visibility matters: a silent strip masks prompt drift forever.
        import logging

        with caplog.at_level(logging.WARNING, logger="jane_conversation.tools.handlers.family"):
            _strip_proactive_prefix("[PROACTIVE] hi")
        assert any("[PROACTIVE]" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# Sanity — ProactivePayload is frozen
# ---------------------------------------------------------------------------


def test_payload_is_frozen():
    # Frozen so a dispatch helper can't mutate the parsed payload mid-flow.
    payload = ProactivePayload(description="x", time_str="10:00", mode=MODE_NORMAL, trigger="arrival", person=None)
    with pytest.raises(AttributeError):
        payload.description = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 10. Dispatch enforcement: streak short-circuit + budget context
# ---------------------------------------------------------------------------


class TestProactiveDispatchEnforcement:
    """Wires `check_dismissal_streak` and `check_speech_budget` into the
    dispatch flow per D4. Streak is a hard short-circuit (mirrors mode-gate);
    budget is a soft context flag passed to think() so the LLM downgrades
    voice→notification when the daily cap is hit. Critical urgency may still
    speak per the rule inside `PROACTIVE_BUDGET_EXHAUSTED_NOTE` (D8).
    """

    def _hass_with_jane(self, mode: str = MODE_NORMAL):
        """hass mock with a JaneData-like jane_conversation entry."""
        hass = _hass_with_mode(mode)
        jane = MagicMock()
        jane.pg_pool = MagicMock()
        jane.redis = AsyncMock()
        jane.working_memory = None
        hass.data = {"jane_conversation": jane}
        return hass, jane

    @pytest.mark.asyncio
    async def test_streak_short_circuits_before_think(self):
        from jane_conversation.brain import proactive_dispatch

        hass, _ = self._hass_with_jane()
        user_input = MagicMock(language="he")
        get_client = AsyncMock(return_value=MagicMock())

        with (
            patch.object(proactive_dispatch, "check_dismissal_streak", AsyncMock(return_value=False)),
            patch.object(proactive_dispatch, "check_speech_budget", AsyncMock(return_value=True)),
            patch.object(proactive_dispatch, "record_proactive_decision", AsyncMock(return_value=99)) as rec,
            patch.object(proactive_dispatch, "think", AsyncMock(return_value="should not be called")) as think_mock,
        ):
            await proactive_dispatch.handle_proactive_dispatch(
                hass,
                user_input,
                "[PROACTIVE] Alice arrived. Time: 14:30. Mode: רגיל. Trigger: arrival.",
                "conv-1",
                get_client,
                None,
            )
        # The streak short-circuit must write a suppressed_by_streak audit
        # row and skip the LLM entirely — same shape as the mode gate.
        rec.assert_awaited_once()
        kwargs = rec.await_args.kwargs
        assert kwargs["action_taken"] == "suppressed_by_streak"
        assert "3-strike" in kwargs["reasoning"]
        think_mock.assert_not_awaited()
        get_client.assert_not_awaited()  # never built a client either

    @pytest.mark.asyncio
    async def test_budget_exhausted_passes_flag_to_think(self):
        from jane_conversation.brain import proactive_dispatch

        hass, _ = self._hass_with_jane()
        user_input = MagicMock(language="he")
        get_client = AsyncMock(return_value=MagicMock())

        with (
            patch.object(proactive_dispatch, "check_dismissal_streak", AsyncMock(return_value=True)),
            patch.object(proactive_dispatch, "check_speech_budget", AsyncMock(return_value=False)),
            patch.object(proactive_dispatch, "record_proactive_decision", AsyncMock(return_value=99)),
            patch.object(proactive_dispatch, "think", AsyncMock(return_value="ok")) as think_mock,
        ):
            await proactive_dispatch.handle_proactive_dispatch(
                hass,
                user_input,
                "[PROACTIVE] Alice arrived. Time: 14:30. Mode: רגיל. Trigger: arrival.",
                "conv-2",
                get_client,
                None,
            )
        # Budget exhausted is not a hard suppress — think() still runs, with
        # the override flag set so engine.py appends the budget-exhausted
        # note to the system instruction.
        think_mock.assert_awaited_once()
        assert think_mock.await_args.kwargs.get("proactive_budget_exhausted") is True
        assert think_mock.await_args.kwargs.get("is_proactive") is True

    @pytest.mark.asyncio
    async def test_budget_available_passes_flag_false(self):
        from jane_conversation.brain import proactive_dispatch

        hass, _ = self._hass_with_jane()
        user_input = MagicMock(language="he")
        get_client = AsyncMock(return_value=MagicMock())

        with (
            patch.object(proactive_dispatch, "check_dismissal_streak", AsyncMock(return_value=True)),
            patch.object(proactive_dispatch, "check_speech_budget", AsyncMock(return_value=True)),
            patch.object(proactive_dispatch, "record_proactive_decision", AsyncMock(return_value=99)),
            patch.object(proactive_dispatch, "think", AsyncMock(return_value="ok")) as think_mock,
        ):
            await proactive_dispatch.handle_proactive_dispatch(
                hass,
                user_input,
                "[PROACTIVE] Alice arrived. Time: 14:30. Mode: רגיל. Trigger: arrival.",
                "conv-3",
                get_client,
                None,
            )
        think_mock.assert_awaited_once()
        assert think_mock.await_args.kwargs.get("proactive_budget_exhausted") is False

    @pytest.mark.asyncio
    async def test_canonical_trigger_is_passed_to_think(self):
        # Per the tool-definition contract: the trigger key the LLM writes
        # into log_proactive_decision MUST equal the user_overrides.action_type
        # the dispatch streak gate queries against. Pre-filling the canonical
        # value via think() removes the LLM's degree of freedom on this field
        # and keeps the streak contract intact.
        from jane_conversation.brain import proactive_dispatch

        hass, _ = self._hass_with_jane()
        user_input = MagicMock(language="he")
        get_client = AsyncMock(return_value=MagicMock())

        with (
            patch.object(proactive_dispatch, "check_dismissal_streak", AsyncMock(return_value=True)),
            patch.object(proactive_dispatch, "check_speech_budget", AsyncMock(return_value=True)),
            patch.object(proactive_dispatch, "record_proactive_decision", AsyncMock(return_value=99)),
            patch.object(proactive_dispatch, "think", AsyncMock(return_value="ok")) as think_mock,
        ):
            await proactive_dispatch.handle_proactive_dispatch(
                hass,
                user_input,
                "[PROACTIVE] Alice arrived. Time: 14:30. Mode: רגיל. Trigger: arrival.",
                "conv-trigger",
                get_client,
                None,
            )
        think_mock.assert_awaited_once()
        # Critical: 'arrival' (canonical), not 'Alice' (description.split()[0]).
        # If this assertion ever flips back to 'Alice' the streak contract
        # silently breaks — see PR #57 review by yairpihH.
        assert think_mock.await_args.kwargs.get("proactive_canonical_trigger") == "arrival"

    @pytest.mark.asyncio
    async def test_streak_query_uses_canonical_trigger_not_first_word(self):
        # Verify the streak gate keys on payload.trigger ('arrival') and not
        # on description.split()[0] ('Alice'). check_dismissal_streak is the
        # observation point — its first arg must be the canonical key.
        from jane_conversation.brain import proactive_dispatch

        hass, _ = self._hass_with_jane()
        user_input = MagicMock(language="he")
        get_client = AsyncMock(return_value=MagicMock())
        streak_check = AsyncMock(return_value=True)

        with (
            patch.object(proactive_dispatch, "check_dismissal_streak", streak_check),
            patch.object(proactive_dispatch, "check_speech_budget", AsyncMock(return_value=True)),
            patch.object(proactive_dispatch, "record_proactive_decision", AsyncMock(return_value=99)),
            patch.object(proactive_dispatch, "think", AsyncMock(return_value="ok")),
        ):
            await proactive_dispatch.handle_proactive_dispatch(
                hass,
                user_input,
                "[PROACTIVE] Alice arrived. Time: 14:30. Mode: רגיל. Trigger: arrival.",
                "conv-streak-key",
                get_client,
                None,
            )
        streak_check.assert_awaited_once()
        # check_dismissal_streak(pg_pool, action_type=...). action_type is the
        # second positional arg.
        action_type = streak_check.await_args.args[1]
        assert action_type == "arrival", f"streak gate keyed on {action_type!r}, not 'arrival'"


# ---------------------------------------------------------------------------
# 11. Budget-exhausted prompt fragment
# ---------------------------------------------------------------------------


def test_budget_exhausted_note_exists_and_names_the_right_tools():
    # Sanity: the prompt fragment must actually mention which tool to use
    # vs avoid, otherwise the LLM has no actionable signal when the cap
    # is hit. Catches a future contributor refactoring the constant away.
    from jane_conversation.brain.proactive_prompts import PROACTIVE_BUDGET_EXHAUSTED_NOTE

    assert "DAILY SPEECH CAP REACHED" in PROACTIVE_BUDGET_EXHAUSTED_NOTE
    assert "send_notification" in PROACTIVE_BUDGET_EXHAUSTED_NOTE  # use this
    assert "tts_announce" in PROACTIVE_BUDGET_EXHAUSTED_NOTE  # not this
    assert "Critical urgency" in PROACTIVE_BUDGET_EXHAUSTED_NOTE  # safety bypass


def test_canonical_trigger_note_pre_fills_exact_key():
    # The pre-fill is the LLM's only signal that 'arrival' (not 'Alice')
    # is the right trigger key. Must literally include the canonical value
    # and the tool name so the LLM can't drift on which arg to set.
    from jane_conversation.brain.proactive_prompts import canonical_trigger_note

    note = canonical_trigger_note("arrival")
    assert "trigger='arrival'" in note
    assert "log_proactive_decision" in note
    assert "user_overrides" in note  # explains why the contract matters


def test_proactive_system_parts_includes_fragments_conditionally():
    # The composer always emits the base instructions; trigger and budget
    # fragments are conditional. Lets engine.py stay agnostic of how many
    # fragments exist or in what order they layer.
    from jane_conversation.brain.proactive_prompts import (
        PROACTIVE_BUDGET_EXHAUSTED_NOTE,
        PROACTIVE_SYSTEM_INSTRUCTIONS,
        proactive_system_parts,
    )

    base = proactive_system_parts(canonical_trigger=None, budget_exhausted=False)
    assert base == [PROACTIVE_SYSTEM_INSTRUCTIONS]

    with_trigger = proactive_system_parts(canonical_trigger="arrival", budget_exhausted=False)
    assert len(with_trigger) == 2
    assert "trigger='arrival'" in with_trigger[1]

    with_both = proactive_system_parts(canonical_trigger="arrival", budget_exhausted=True)
    assert len(with_both) == 3
    assert with_both[2] == PROACTIVE_BUDGET_EXHAUSTED_NOTE


# Reference unused mode constants so the import isn't trimmed by ruff.
_REFERENCED_MODES = (MODE_GUESTS, MODE_KIDS_SLEEPING, MODE_TRAVEL, MODE_WORK)
