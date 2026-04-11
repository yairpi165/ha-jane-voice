"""Tests for brain.py — request classification, context building, history conversion."""

from unittest.mock import MagicMock

import pytest

from jane_conversation.brain import classify_request
from jane_conversation.brain.context import build_context
from jane_conversation.brain.engine import _extract_text

# ---------------------------------------------------------------------------
# Request Classification
# ---------------------------------------------------------------------------

class TestClassifyRequest:
    """Test classify_request categorizes Hebrew input correctly."""

    # Commands
    def test_turn_on_light(self):
        assert classify_request("תדליק אור") == "command"

    def test_turn_off_ac(self):
        assert classify_request("תכבה מזגן") == "command"

    def test_open_shutter(self):
        assert classify_request("תפתח תריס") == "command"

    def test_close_shutter(self):
        assert classify_request("תסגור תריס") == "command"

    def test_boil_water(self):
        assert classify_request("תרתיח מים") == "command"

    # Routine triggers (commands, not chat!)
    def test_goodnight_is_command(self):
        assert classify_request("לילה טוב") == "command"

    def test_good_morning_is_command(self):
        assert classify_request("בוקר טוב") == "command"

    def test_good_evening_is_command(self):
        assert classify_request("ערב טוב") == "command"

    def test_goodnight_with_name(self):
        assert classify_request("לילה טוב ג'יין") == "command"

    # Chat
    def test_how_are_you(self):
        assert classify_request("מה שלומך") == "chat"

    def test_hi(self):
        assert classify_request("היי") == "chat"

    def test_thanks(self):
        assert classify_request("תודה") == "chat"

    def test_whats_up(self):
        assert classify_request("מה קורה") == "chat"

    # Complex
    def test_create_automation(self):
        assert classify_request("תיצרי אוטומציה שמדליקה אור בשש") == "complex"

    def test_explain(self):
        assert classify_request("למה המזגן לא נכבה?") == "complex"

    def test_history_question(self):
        assert classify_request("מתי המזגן נדלק לאחרונה?") == "complex"

    def test_shopping_list(self):
        assert classify_request("תוסיפי חלב לרשימת קניות") == "complex"

    def test_send_message(self):
        assert classify_request("תשלחי הודעה ליאיר") == "complex"

    # Default → complex
    def test_unknown_defaults_to_complex(self):
        assert classify_request("מה הטמפרטורה?") == "complex"


# ---------------------------------------------------------------------------
# Context Building
# ---------------------------------------------------------------------------

class TestBuildContext:
    """Test build_context generates concise home awareness."""

    @pytest.mark.asyncio
    async def test_includes_weather(self, hass_mock):
        context = await build_context(hass_mock)
        assert "sunny" in context
        assert "25" in context

    @pytest.mark.asyncio
    async def test_includes_people(self, hass_mock):
        context = await build_context(hass_mock)
        assert "יאיר" in context
        assert "home" in context
        assert "אפרת" in context
        assert "away" in context

    @pytest.mark.asyncio
    async def test_includes_active_devices(self, hass_mock):
        context = await build_context(hass_mock)
        assert "אור סלון" in context
        assert "טלוויזיה" in context

    @pytest.mark.asyncio
    async def test_skips_cameras(self, hass_mock):
        context = await build_context(hass_mock)
        assert "Camera" not in context
        assert "camera" not in context.lower().split("active")[1] if "Active" in context else True

    @pytest.mark.asyncio
    async def test_skips_off_devices(self, hass_mock):
        context = await build_context(hass_mock)
        assert "אור חדר שינה" not in context  # It's off


# ---------------------------------------------------------------------------
# Text Extraction
# ---------------------------------------------------------------------------

class TestExtractText:
    def test_extracts_text_from_parts(self):
        part = MagicMock()
        part.text = "שלום!"
        assert _extract_text([part]) == "שלום!"

    def test_returns_empty_for_no_text(self):
        part = MagicMock(spec=[])  # No text attribute
        assert _extract_text([part]) == ""

    def test_skips_none_text(self):
        part1 = MagicMock()
        part1.text = None
        part2 = MagicMock()
        part2.text = "Found it"
        assert _extract_text([part1, part2]) == "Found it"
