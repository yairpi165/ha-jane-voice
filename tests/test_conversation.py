"""Tests for conversation.py — hallucination filter, session management."""

from jane_conversation.const import WHISPER_HALLUCINATIONS

# ---------------------------------------------------------------------------
# Hallucination Filter
# ---------------------------------------------------------------------------

class TestHallucinationFilter:
    def test_hebrew_hallucinations_detected(self):
        assert "תודה רבה" in WHISPER_HALLUCINATIONS
        assert "תודה לצפייה" in WHISPER_HALLUCINATIONS
        assert "תודה על הצפייה" in WHISPER_HALLUCINATIONS
        assert "שבוע טוב" in WHISPER_HALLUCINATIONS

    def test_english_hallucinations_detected(self):
        assert "thank you" in WHISPER_HALLUCINATIONS
        assert "thanks for watching" in WHISPER_HALLUCINATIONS
        assert "thank you for watching" in WHISPER_HALLUCINATIONS
        assert "you" in WHISPER_HALLUCINATIONS
        assert "the end" in WHISPER_HALLUCINATIONS

    def test_empty_string_is_hallucination(self):
        assert "" in WHISPER_HALLUCINATIONS

    def test_dots_are_hallucinations(self):
        assert "..." in WHISPER_HALLUCINATIONS
        assert "." in WHISPER_HALLUCINATIONS

    def test_real_commands_not_filtered(self):
        assert "תדליק אור" not in WHISPER_HALLUCINATIONS
        assert "מה שלומך" not in WHISPER_HALLUCINATIONS
        assert "לילה טוב" not in WHISPER_HALLUCINATIONS
        assert "כבה את המזגן" not in WHISPER_HALLUCINATIONS

    def test_hallucination_count(self):
        """Ensure we have a reasonable number of filters."""
        assert len(WHISPER_HALLUCINATIONS) >= 10
