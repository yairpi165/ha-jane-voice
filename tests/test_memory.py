"""Tests for memory.py — file I/O, anti-repetition tracking, action log."""

from jane_conversation.memory import (
    _recent_responses,
    append_action,
    append_history,
    get_recent_responses,
    init_memory,
    load_all_memory,
    load_home,
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
        # Should contain response 5-14 (last 10)
        assert "Response 14" in result
        assert "Response 4" not in result


# ---------------------------------------------------------------------------
# Memory File I/O
# ---------------------------------------------------------------------------


class TestMemoryFileIO:
    def test_init_creates_directories(self, tmp_memory_dir):
        init_memory(str(tmp_memory_dir.parent))
        assert (tmp_memory_dir / "users").exists()

    def test_load_missing_file_returns_empty(self, tmp_memory_dir):
        init_memory(str(tmp_memory_dir.parent))
        result = load_home()
        assert result == ""

    def test_load_existing_file(self, tmp_memory_dir):
        init_memory(str(tmp_memory_dir.parent))
        home_file = tmp_memory_dir / "home.md"
        home_file.write_text("# Home\n- Light (light.test)")
        result = load_home()
        assert "# Home" in result
        assert "light.test" in result

    def test_load_all_memory_sections(self, tmp_memory_dir):
        init_memory(str(tmp_memory_dir.parent))
        result = load_all_memory("yair")
        assert "## Home Layout" in result
        assert "## Personal Memory" in result
        assert "## Family Memory" in result


# ---------------------------------------------------------------------------
# Action Log
# ---------------------------------------------------------------------------


class TestActionLog:
    def test_append_creates_file(self, tmp_memory_dir):
        init_memory(str(tmp_memory_dir.parent))
        append_action("yair", "Turned on light")
        actions_file = tmp_memory_dir / "actions.md"
        assert actions_file.exists()
        content = actions_file.read_text()
        assert "Turned on light" in content
        assert "yair" in content

    def test_append_history_creates_log(self, tmp_memory_dir):
        init_memory(str(tmp_memory_dir.parent))
        append_history("yair", "תדליק אור", "הדלקתי")
        log_file = tmp_memory_dir / "history.log"
        assert log_file.exists()
        content = log_file.read_text()
        assert "תדליק אור" in content
        assert "הדלקתי" in content


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
