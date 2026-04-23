"""End-to-end tests — full conversation simulation with mocked Gemini + HA."""

from unittest.mock import AsyncMock, patch

import pytest

from jane_conversation.brain import classify_request, think


class TestE2ETurnOnLight:
    """Simulate: "תדליק אור בסלון" → classify → Haiku → tool call → confirm."""

    @pytest.mark.asyncio
    async def test_full_flow(self, hass_mock, gemini_client_mock):
        # Step 1: Gemini decides to call call_ha_service
        tool_resp = gemini_client_mock._make_tool_call_response(
            "call_ha_service",
            {"domain": "light", "service": "turn_on", "entity_id": "light.living_room"},
        )
        # Step 2: Gemini confirms in Hebrew
        text_resp = gemini_client_mock._make_text_response("הדלקתי את האור בסלון")
        gemini_client_mock.models.generate_content.side_effect = [tool_resp, text_resp]

        with (
            patch(
                "jane_conversation.brain.engine.get_backend",
                return_value=AsyncMock(load=AsyncMock(return_value="- אור סלון (light.living_room)")),
            ),
            patch("jane_conversation.brain.engine.get_recent_responses", return_value=""),
            patch("jane_conversation.brain.engine.execute_tool", new_callable=AsyncMock, return_value="Success."),
        ):
            result = await think(gemini_client_mock, "תדליק אור בסלון", "alice", hass_mock)

        assert "הדלקתי" in result
        # Verify it was classified as command → Flash model
        assert classify_request("תדליק אור בסלון") == "command"


class TestE2EWeatherQuery:
    """Simulate: "מה מזג האוויר?" → complex → Pro → get_entity_state → answer."""

    @pytest.mark.asyncio
    async def test_full_flow(self, hass_mock, gemini_client_mock):
        tool_resp = gemini_client_mock._make_tool_call_response(
            "get_entity_state", {"entity_id": "weather.forecast_home"}
        )
        text_resp = gemini_client_mock._make_text_response("היום שמשי, 25 מעלות")
        gemini_client_mock.models.generate_content.side_effect = [tool_resp, text_resp]

        with (
            patch(
                "jane_conversation.brain.engine.get_backend", return_value=AsyncMock(load=AsyncMock(return_value=""))
            ),
            patch("jane_conversation.brain.engine.get_recent_responses", return_value=""),
            patch(
                "jane_conversation.brain.engine.execute_tool",
                new_callable=AsyncMock,
                return_value="Forecast Home: sunny, 25°C",
            ),
        ):
            result = await think(gemini_client_mock, "מה מזג האוויר?", "alice", hass_mock)

        assert "25" in result


class TestE2EGoodnightRoutine:
    """Simulate: "לילה טוב" → command → search for script → run it."""

    @pytest.mark.asyncio
    async def test_full_flow(self, hass_mock, gemini_client_mock):
        # Step 1: Gemini searches for "לילה טוב" script
        search_resp = gemini_client_mock._make_tool_call_response("search_entities", {"query": "לילה טוב"})
        # Step 2: Gemini calls the script
        run_resp = gemini_client_mock._make_tool_call_response(
            "call_ha_service", {"domain": "script", "service": "turn_on", "entity_id": "script.layla_tov"}
        )
        # Step 3: Gemini confirms
        text_resp = gemini_client_mock._make_text_response("הפעלתי את שגרת לילה טוב. לילה טוב!")
        gemini_client_mock.models.generate_content.side_effect = [search_resp, run_resp, text_resp]

        with (
            patch(
                "jane_conversation.brain.engine.get_backend", return_value=AsyncMock(load=AsyncMock(return_value=""))
            ),
            patch("jane_conversation.brain.engine.get_recent_responses", return_value=""),
            patch(
                "jane_conversation.brain.engine.execute_tool",
                new_callable=AsyncMock,
                side_effect=[
                    '[{"entity_id": "script.layla_tov", "name": "לילה טוב", "state": "off"}]',
                    "Success.",
                ],
            ),
        ):
            result = await think(gemini_client_mock, "לילה טוב", "alice", hass_mock)

        assert "לילה טוב" in result
        assert classify_request("לילה טוב") == "command"


class TestE2ECreateAutomation:
    """Simulate: "תיצרי אוטומציה" → complex → Pro → ha_config_api → confirm."""

    @pytest.mark.asyncio
    async def test_full_flow(self, hass_mock, gemini_client_mock):
        tool_resp = gemini_client_mock._make_tool_call_response(
            "ha_config_api",
            {
                "resource": "automation",
                "operation": "create",
                "config": {
                    "alias": "Light at 18:00",
                    "trigger": [{"platform": "time", "at": "18:00"}],
                    "action": [{"service": "light.turn_on", "target": {"entity_id": "light.living_room"}}],
                    "mode": "single",
                },
            },
        )
        text_resp = gemini_client_mock._make_text_response("יצרתי אוטומציה שמדליקה אור בסלון כל יום ב-18:00")
        gemini_client_mock.models.generate_content.side_effect = [tool_resp, text_resp]

        with (
            patch(
                "jane_conversation.brain.engine.get_backend",
                return_value=AsyncMock(load=AsyncMock(return_value="- אור סלון (light.living_room)")),
            ),
            patch("jane_conversation.brain.engine.get_recent_responses", return_value=""),
            patch(
                "jane_conversation.brain.engine.execute_tool",
                new_callable=AsyncMock,
                return_value="Created automation with id 'abc123'.",
            ),
        ):
            result = await think(
                gemini_client_mock,
                "תיצרי אוטומציה שמדליקה את האור בסלון כל יום ב-18:00",
                "alice",
                hass_mock,
            )

        assert "אוטומציה" in result or "18:00" in result
        assert classify_request("תיצרי אוטומציה שמדליקה את האור בסלון כל יום ב-18:00") == "complex"


class TestE2EChatNoTools:
    """Simulate: "מה שלומך" → chat → Flash → text only, no tools."""

    @pytest.mark.asyncio
    async def test_full_flow(self, hass_mock, gemini_client_mock):
        text_resp = gemini_client_mock._make_text_response("אני בסדר, תודה ששאלת! מה איתך?")
        gemini_client_mock.models.generate_content.return_value = text_resp

        with (
            patch(
                "jane_conversation.brain.engine.get_backend", return_value=AsyncMock(load=AsyncMock(return_value=""))
            ),
            patch("jane_conversation.brain.engine.get_recent_responses", return_value=""),
        ):
            result = await think(gemini_client_mock, "מה שלומך", "alice", hass_mock)

        assert len(result) > 0
        # Should be only 1 API call (no tools)
        assert gemini_client_mock.models.generate_content.call_count == 1
        assert classify_request("מה שלומך") == "chat"


class TestE2EWebSearch:
    """Simulate: "מה שער הדולר" → complex → search_web → answer."""

    @pytest.mark.asyncio
    async def test_full_flow(self, hass_mock, gemini_client_mock):
        tool_resp = gemini_client_mock._make_tool_call_response("search_web", {"query": "USD ILS exchange rate today"})
        text_resp = gemini_client_mock._make_text_response("שער הדולר היום הוא 3.06 שקלים")
        gemini_client_mock.models.generate_content.side_effect = [tool_resp, text_resp]

        with (
            patch(
                "jane_conversation.brain.engine.get_backend", return_value=AsyncMock(load=AsyncMock(return_value=""))
            ),
            patch("jane_conversation.brain.engine.get_recent_responses", return_value=""),
            patch(
                "jane_conversation.brain.engine.execute_tool", new_callable=AsyncMock, return_value="USD to ILS: 3.06"
            ),
        ):
            result = await think(gemini_client_mock, "מה שער הדולר?", "alice", hass_mock)

        assert "3.06" in result or "שקל" in result
