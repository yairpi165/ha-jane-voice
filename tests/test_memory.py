"""Tests for memory — anti-repetition tracking, date normalization."""

from jane_conversation.memory import (
    _recent_responses,
    get_recent_responses,
    track_response,
)
from jane_conversation.memory.extraction import _normalize_date

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
