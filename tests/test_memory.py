"""Tests for memory — anti-repetition tracking, date normalization."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jane_conversation.memory import (
    _recent_responses,
    get_recent_responses,
    track_response,
)
from jane_conversation.memory.extraction import (
    _MAX_CONTEXT_CHARS,
    _cap_exchanges,
    _format_exchanges_for_prompt,
    _normalize_date,
    process_memory,
)

# ---------------------------------------------------------------------------
# Anti-Repetition Tracking
# ---------------------------------------------------------------------------


class TestAntiRepetition:
    def setup_method(self):
        """Clear responses before each test."""
        _recent_responses.clear()

    def test_empty_returns_empty_string(self):
        assert get_recent_responses() == ""

    def test_track_stores_opening(self):
        track_response("שלום לך! איך אני יכולה לעזור?")
        result = get_recent_responses()
        assert "שלום לך" in result

    def test_track_truncates_to_60_chars(self):
        long_response = "א" * 100
        track_response(long_response)
        assert len(_recent_responses[-1]) == 60

    def test_caps_at_20(self):
        for i in range(25):
            track_response(f"Response {i}")
        assert len(_recent_responses) == 20

    def test_oldest_removed_first(self):
        for i in range(25):
            track_response(f"Response {i}")
        assert "Response 0" not in _recent_responses[0]
        assert "Response 24" in _recent_responses[-1]

    def test_get_formats_with_pipe_separator(self):
        track_response("First response")
        track_response("Second response")
        result = get_recent_responses()
        assert " | " in result

    def test_empty_response_ignored(self):
        track_response("")
        assert len(_recent_responses) == 0

    def test_get_returns_last_10(self):
        for i in range(15):
            track_response(f"Response {i}")
        result = get_recent_responses()
        assert "Response 14" in result
        assert "Response 4" not in result


# ---------------------------------------------------------------------------
# Birthday Date Normalization
# ---------------------------------------------------------------------------


class TestNormalizeDate:
    def test_iso_format(self):
        from datetime import date

        assert _normalize_date("2024-12-08") == date(2024, 12, 8)

    def test_slash_format_dayfirst(self):
        from datetime import date

        assert _normalize_date("08/12/2024") == date(2024, 12, 8)

    def test_natural_english(self):
        from datetime import date

        assert _normalize_date("December 8, 2024") == date(2024, 12, 8)

    def test_invalid_returns_none(self):
        assert _normalize_date("not a date") is None

    def test_empty_returns_none(self):
        assert _normalize_date("") is None

    def test_returns_date_object_not_string(self):
        """asyncpg requires datetime.date for DATE columns, not str."""
        from datetime import date

        result = _normalize_date("2024-12-08")
        assert isinstance(result, date)
        assert not isinstance(result, str)


# ---------------------------------------------------------------------------
# A2 — Multi-exchange extraction helpers
# ---------------------------------------------------------------------------


def _mk(text: str, response: str) -> dict:
    return {"user": "Alice", "text": text, "response": response, "ts": 0}


class TestCapExchanges:
    def test_keeps_all_within_limit(self):
        exchanges = [_mk(f"q{i}", f"a{i}") for i in range(5)]
        assert _cap_exchanges(exchanges) == exchanges

    def test_keeps_recent_when_over_limit(self):
        # 10 large exchanges, each ~1000 chars → total 10000 > 8000 cap
        exchanges = [_mk("x" * 500, "y" * 500) for _ in range(10)]
        capped = _cap_exchanges(exchanges)
        assert len(capped) < len(exchanges)
        # Latest kept (recency gives them priority — reversed iteration)
        assert capped[-1] is exchanges[-1]

    def test_single_giant_exchange_still_included(self):
        """Never drop the latest turn, even if it alone exceeds the cap."""
        huge = _mk("x" * (_MAX_CONTEXT_CHARS * 2), "y")
        assert _cap_exchanges([huge]) == [huge]

    def test_empty_input_returns_empty(self):
        assert _cap_exchanges([]) == []


class TestFormatExchanges:
    def test_renders_chronologically_with_numbering(self):
        exchanges = [_mk("hi", "hello"), _mk("what's up?", "not much")]
        rendered = _format_exchanges_for_prompt(exchanges)
        lines = rendered.splitlines()
        assert lines[0] == "[1] User: hi"
        assert lines[1] == "    Jane: hello"
        assert lines[2] == "[2] User: what's up?"
        assert lines[3] == "    Jane: not much"

    def test_empty_list_returns_empty_string(self):
        assert _format_exchanges_for_prompt([]) == ""


class TestProcessMemoryMultiExchange:
    @pytest.mark.asyncio
    async def test_empty_exchanges_early_return(self, hass_mock):
        """Empty list (post-cap edge case) returns without calling Gemini."""
        client = MagicMock()
        # No mocking of backend needed — we expect early exit before backend load.
        await process_memory(client, "Alice", [], "tool", hass_mock)
        client.models.generate_content.assert_not_called()

    @pytest.mark.asyncio
    async def test_ha_service_skip_only_when_single_exchange(self, hass_mock):
        """A2 §3.3: 2-exchange burst with short responses must NOT skip."""
        client = MagicMock()
        with patch("jane_conversation.memory.extraction.get_backend") as gb:
            backend, structured, pool = _setup_jane_data(hass_mock)
            gb.return_value = backend
            hass_mock.async_add_executor_job = AsyncMock(
                return_value=_fake_gemini_response('{"ops":[{"op":"NOOP","reason":"stub"}]}')
            )

            # Single short exchange with ha_service → skip (legacy behavior preserved).
            await process_memory(client, "Alice", [_mk("turn off", "off")], "ha_service", hass_mock)
            hass_mock.async_add_executor_job.assert_not_called()

            # 2 short exchanges with ha_service → must NOT skip (multi-exchange).
            await process_memory(
                client,
                "Alice",
                [_mk("turn off", "off"), _mk("birthday 15/6", "rashamti")],
                "ha_service",
                hass_mock,
            )
            hass_mock.async_add_executor_job.assert_called()

    @pytest.mark.asyncio
    async def test_passes_multi_exchange_prompt_to_gemini(self, hass_mock):
        """A3: Gemini prompt contains ops schema + all exchanges + memory snapshot."""
        client = MagicMock()
        captured_prompts = []

        def _capture(fn, client_arg, prompt):
            captured_prompts.append(prompt)
            return _fake_gemini_response('{"ops":[{"op":"NOOP","reason":"stub"}]}')

        with patch("jane_conversation.memory.extraction.get_backend") as gb:
            backend, structured, pool = _setup_jane_data(hass_mock)
            gb.return_value = backend
            hass_mock.async_add_executor_job = AsyncMock(side_effect=_capture)

            exchanges = [
                _mk("turn 1", "r1"),
                _mk("my birthday is June 15", "noted"),
                _mk("thanks", "np"),
            ]
            await process_memory(client, "Alice", exchanges, "tool", hass_mock)

        assert len(captured_prompts) == 1
        prompt = captured_prompts[0]
        assert "3 total" in prompt
        assert "turn 1" in prompt
        assert "my birthday is June 15" in prompt
        assert "thanks" in prompt
        assert "ADD" in prompt and "NOOP" in prompt  # ops schema present


def _fake_gemini_response(text: str):
    """Build a fake Gemini API response with given .text."""
    part = MagicMock()
    part.text = text
    content = MagicMock()
    content.parts = [part]
    candidate = MagicMock()
    candidate.content = content
    response = MagicMock()
    response.candidates = [candidate]
    return response


def _setup_jane_data(hass_mock):
    """Populate hass.data[DOMAIN] with structured + pg_pool stubs for A3 process_memory.

    Returns (backend_mock, structured_mock, pool_mock) for further customisation.
    """
    from jane_conversation.const import DOMAIN

    backend = MagicMock()
    backend.load = AsyncMock(return_value="")
    backend.load_all = AsyncMock(return_value="")
    backend.load_snapshot = AsyncMock(return_value={})
    backend.save = AsyncMock()
    backend.delete_category = AsyncMock(return_value=None)
    backend.append_event = AsyncMock()

    structured = MagicMock()
    structured.load_persons = AsyncMock(return_value=[])
    structured.load_all_preferences = AsyncMock(return_value={})
    structured.load_preference = AsyncMock(return_value=None)
    structured.load_person = AsyncMock(return_value=None)
    structured.save_preference = AsyncMock()
    structured.save_person = AsyncMock()
    structured.delete_preference = AsyncMock(return_value=None)

    pool = MagicMock()
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock()
    acq_cm = MagicMock()
    acq_cm.__aenter__ = AsyncMock(return_value=conn)
    acq_cm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acq_cm)

    jane = MagicMock()
    jane.structured = structured
    jane.pg_pool = pool
    hass_mock.data = {DOMAIN: jane}
    return backend, structured, pool


# ---------------------------------------------------------------------------
# B1 — Stage 1 preference key normalization
# ---------------------------------------------------------------------------


class TestNormalizePrefKey:
    def test_lowercase_underscore_spaces_trim(self):
        from jane_conversation.memory.structured import _normalize_pref_key

        assert _normalize_pref_key("food_Preferences") == "food preferences"
        assert _normalize_pref_key("  food preferences  ") == "food preferences"
        assert _normalize_pref_key("FOOD  PREFERENCES") == "food preferences"
        assert _normalize_pref_key("note_Travel_Plans") == "note travel plans"

    def test_empty_and_none_safe(self):
        from jane_conversation.memory.structured import _normalize_pref_key

        assert _normalize_pref_key("") == ""
        assert _normalize_pref_key(None) is None  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_save_preference_passes_normalized_key_to_sql(self):
        from jane_conversation.memory.structured import StructuredMemoryStore

        pool = MagicMock()
        conn = MagicMock()
        conn.execute = AsyncMock()
        acq = MagicMock()
        acq.__aenter__ = AsyncMock(return_value=conn)
        acq.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=acq)
        store = StructuredMemoryStore(pool)
        await store.save_preference("Alice", "Food_Preferences", "coffee")
        # Second positional arg is the key
        assert conn.execute.await_args.args[2] == "food preferences"


# ---------------------------------------------------------------------------
# JANE-84 — Extraction uses response_schema for Gemini JSON mode
# ---------------------------------------------------------------------------


class TestExtractionResponseSchema:
    def test_call_with_retry_passes_response_schema(self):
        from jane_conversation.memory.extraction import _OPS_RESPONSE_SCHEMA, _call_with_retry

        client = MagicMock()
        client.models.generate_content = MagicMock(return_value=MagicMock())
        _call_with_retry(client, "fake prompt")

        kwargs = client.models.generate_content.call_args.kwargs
        # Prompt lives in system_instruction (stronger priority channel for long
        # few-shot prompts); contents is a short directive telling Flash to emit JSON.
        config = kwargs["config"]
        assert getattr(config, "system_instruction", None) == "fake prompt"
        assert kwargs["contents"] == "Emit the ops JSON per the schema."
        # Gemini's types.GenerateContentConfig stores fields as attrs.
        assert getattr(config, "response_mime_type", None) == "application/json"
        assert getattr(config, "response_schema", None) == _OPS_RESPONSE_SCHEMA
