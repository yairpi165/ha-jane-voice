"""Tests for Gemini API integration — tool calling loop, model selection, history conversion."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from google.genai import types

from jane_conversation.brain import think, classify_request
from jane_conversation.const import GEMINI_MODEL_FAST, GEMINI_MODEL_SMART


# ---------------------------------------------------------------------------
# History Conversion (dict → Gemini Content)
# ---------------------------------------------------------------------------

class TestHistoryConversion:
    """Tests that dict-format history is converted to Gemini Content objects."""

    @pytest.mark.asyncio
    async def test_user_dict_becomes_content(self, hass_mock, gemini_client_mock):
        """History dict with role=user should become Content(role='user')."""
        text_resp = gemini_client_mock._make_text_response("שלום")
        gemini_client_mock.models.generate_content.return_value = text_resp

        with patch("jane_conversation.brain.engine.load_home", return_value=""), \
             patch("jane_conversation.brain.engine.get_recent_responses", return_value=""):
            result = await think(
                gemini_client_mock, "מה שלומך", "yair", hass_mock,
                history=[{"role": "user", "content": "היי"}],
            )

        # Verify generate_content was called
        call_args = gemini_client_mock.models.generate_content.call_args
        contents = call_args.kwargs.get("contents", call_args[1].get("contents") if len(call_args) > 1 else None)
        # Should have 2 messages: history user + new user
        assert len(contents) == 2
        assert contents[0].role == "user"

    @pytest.mark.asyncio
    async def test_assistant_becomes_model(self, hass_mock, gemini_client_mock):
        """History dict with role=assistant should become Content(role='model')."""
        text_resp = gemini_client_mock._make_text_response("בסדר")
        gemini_client_mock.models.generate_content.return_value = text_resp

        with patch("jane_conversation.brain.engine.load_home", return_value=""), \
             patch("jane_conversation.brain.engine.get_recent_responses", return_value=""):
            await think(
                gemini_client_mock, "תודה", "yair", hass_mock,
                history=[
                    {"role": "user", "content": "היי"},
                    {"role": "assistant", "content": "שלום!"},
                ],
            )

        call_args = gemini_client_mock.models.generate_content.call_args
        contents = call_args.kwargs.get("contents", call_args[1].get("contents") if len(call_args) > 1 else None)
        # History: user + assistant(→model) + new user = 3
        assert len(contents) == 3
        assert contents[1].role == "model"  # "assistant" mapped to "model"


# ---------------------------------------------------------------------------
# Model Selection
# ---------------------------------------------------------------------------

class TestModelSelection:
    """Tests that the right model is chosen based on request type."""

    @pytest.mark.asyncio
    async def test_chat_uses_flash(self, hass_mock, gemini_client_mock):
        text_resp = gemini_client_mock._make_text_response("שלום!")
        gemini_client_mock.models.generate_content.return_value = text_resp

        with patch("jane_conversation.brain.engine.load_home", return_value=""), \
             patch("jane_conversation.brain.engine.get_recent_responses", return_value=""):
            await think(gemini_client_mock, "מה שלומך", "yair", hass_mock)

        call_args = gemini_client_mock.models.generate_content.call_args
        model = call_args.kwargs.get("model", call_args[1].get("model") if len(call_args) > 1 else None)
        assert model == GEMINI_MODEL_FAST

    @pytest.mark.asyncio
    async def test_command_uses_flash(self, hass_mock, gemini_client_mock):
        text_resp = gemini_client_mock._make_text_response("הדלקתי")
        gemini_client_mock.models.generate_content.return_value = text_resp

        with patch("jane_conversation.brain.engine.load_home", return_value=""), \
             patch("jane_conversation.brain.engine.get_recent_responses", return_value=""):
            await think(gemini_client_mock, "תדליק אור", "yair", hass_mock)

        call_args = gemini_client_mock.models.generate_content.call_args
        model = call_args.kwargs.get("model", call_args[1].get("model") if len(call_args) > 1 else None)
        assert model == GEMINI_MODEL_FAST

    @pytest.mark.asyncio
    async def test_complex_uses_pro(self, hass_mock, gemini_client_mock):
        text_resp = gemini_client_mock._make_text_response("יצרתי אוטומציה")
        gemini_client_mock.models.generate_content.return_value = text_resp

        with patch("jane_conversation.brain.engine.load_home", return_value=""), \
             patch("jane_conversation.brain.engine.get_recent_responses", return_value=""):
            await think(gemini_client_mock, "תיצרי אוטומציה שמדליקה אור", "yair", hass_mock)

        call_args = gemini_client_mock.models.generate_content.call_args
        model = call_args.kwargs.get("model", call_args[1].get("model") if len(call_args) > 1 else None)
        assert model == GEMINI_MODEL_SMART


# ---------------------------------------------------------------------------
# Tool Calling Loop
# ---------------------------------------------------------------------------

class TestToolCallingLoop:
    """Tests for the think() tool calling loop."""

    @pytest.mark.asyncio
    async def test_simple_text_response(self, hass_mock, gemini_client_mock):
        """When Gemini returns text only, no tool calls."""
        text_resp = gemini_client_mock._make_text_response("הטמפרטורה היא 25 מעלות")
        gemini_client_mock.models.generate_content.return_value = text_resp

        with patch("jane_conversation.brain.engine.load_home", return_value=""), \
             patch("jane_conversation.brain.engine.get_recent_responses", return_value=""):
            result = await think(gemini_client_mock, "מה הטמפרטורה?", "yair", hass_mock)

        assert "25" in result
        assert gemini_client_mock.models.generate_content.call_count == 1

    @pytest.mark.asyncio
    async def test_single_tool_call(self, hass_mock, gemini_client_mock):
        """Gemini calls a tool → gets result → responds with text."""
        # First call: tool call
        tool_resp = gemini_client_mock._make_tool_call_response(
            "get_entity_state", {"entity_id": "climate.ac"}
        )
        # Second call: text response
        text_resp = gemini_client_mock._make_text_response("המזגן על 24 מעלות")

        gemini_client_mock.models.generate_content.side_effect = [tool_resp, text_resp]

        with patch("jane_conversation.brain.engine.load_home", return_value=""), \
             patch("jane_conversation.brain.engine.get_recent_responses", return_value=""), \
             patch("jane_conversation.brain.engine.execute_tool", new_callable=AsyncMock, return_value="מזגן: cool, 24°C"):
            result = await think(gemini_client_mock, "מה מצב המזגן?", "yair", hass_mock)

        assert "24" in result
        assert gemini_client_mock.models.generate_content.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_response_returns_empty(self, hass_mock, gemini_client_mock):
        """Handle empty response gracefully."""
        response = MagicMock()
        response.candidates = []
        gemini_client_mock.models.generate_content.return_value = response

        with patch("jane_conversation.brain.engine.load_home", return_value=""), \
             patch("jane_conversation.brain.engine.get_recent_responses", return_value=""):
            result = await think(gemini_client_mock, "בדיקה", "yair", hass_mock)

        assert result == ""
