"""Tests for HA tool handlers — mock hass object."""

import pytest

from jane_conversation.tools import execute_tool
from jane_conversation.tools.handlers.calendar import _is_date_only

# ---------------------------------------------------------------------------
# Entity State
# ---------------------------------------------------------------------------


class TestGetEntityState:
    @pytest.mark.asyncio
    async def test_found_entity(self, hass_mock):
        result = await execute_tool(hass_mock, "get_entity_state", {"entity_id": "climate.ac"})
        assert "מזגן" in result
        assert "cool" in result

    @pytest.mark.asyncio
    async def test_entity_not_found(self, hass_mock):
        result = await execute_tool(hass_mock, "get_entity_state", {"entity_id": "light.nonexistent"})
        assert "not found" in result


# ---------------------------------------------------------------------------
# Call HA Service
# ---------------------------------------------------------------------------


class TestCallHaService:
    @pytest.mark.asyncio
    async def test_simple_service(self, hass_mock):
        result = await execute_tool(
            hass_mock,
            "call_ha_service",
            {
                "domain": "light",
                "service": "turn_on",
                "entity_id": "light.living_room",
            },
        )
        assert "Success" in result
        hass_mock.services.async_call.assert_called_once()

    @pytest.mark.asyncio
    async def test_service_with_data(self, hass_mock):
        result = await execute_tool(
            hass_mock,
            "call_ha_service",
            {
                "domain": "light",
                "service": "turn_on",
                "entity_id": "light.living_room",
                "data": {"brightness_pct": 50},
            },
        )
        assert "Success" in result
        call_args = hass_mock.services.async_call.call_args
        assert call_args[1].get("blocking") or call_args[0][2].get("brightness_pct") == 50


# ---------------------------------------------------------------------------
# Search Entities
# ---------------------------------------------------------------------------


class TestSearchEntities:
    @pytest.mark.asyncio
    async def test_find_by_name(self, hass_mock):
        result = await execute_tool(hass_mock, "search_entities", {"query": "סלון"})
        assert "light.living_room" in result

    @pytest.mark.asyncio
    async def test_find_by_domain(self, hass_mock):
        result = await execute_tool(hass_mock, "search_entities", {"query": "ac", "domain": "climate"})
        assert "climate.ac" in result

    @pytest.mark.asyncio
    async def test_no_results(self, hass_mock):
        result = await execute_tool(hass_mock, "search_entities", {"query": "xyz_nothing"})
        assert "No entities found" in result


# ---------------------------------------------------------------------------
# Check People
# ---------------------------------------------------------------------------


class TestCheckPeople:
    @pytest.mark.asyncio
    async def test_people_status(self, hass_mock):
        result = await execute_tool(hass_mock, "check_people", {})
        assert "Alice" in result
        assert "home" in result or "at home" in result
        assert "Bob" in result
        assert "away" in result


# ---------------------------------------------------------------------------
# Bulk Control
# ---------------------------------------------------------------------------


class TestBulkControl:
    @pytest.mark.asyncio
    async def test_multiple_entities(self, hass_mock):
        result = await execute_tool(
            hass_mock,
            "bulk_control",
            {
                "entity_ids": ["light.living_room", "light.bedroom"],
                "domain": "light",
                "service": "turn_off",
            },
        )
        assert "light.living_room: OK" in result
        assert "light.bedroom: OK" in result
        assert hass_mock.services.async_call.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_list_error(self, hass_mock):
        result = await execute_tool(
            hass_mock,
            "bulk_control",
            {
                "entity_ids": [],
                "domain": "light",
                "service": "turn_off",
            },
        )
        assert "Error" in result


# ---------------------------------------------------------------------------
# List Helpers
# ---------------------------------------------------------------------------


class TestListHelpers:
    @pytest.mark.asyncio
    async def test_no_helpers(self, hass_mock):
        result = await execute_tool(hass_mock, "list_helpers", {})
        assert "No helper" in result


# ---------------------------------------------------------------------------
# List Services
# ---------------------------------------------------------------------------


class TestListServices:
    @pytest.mark.asyncio
    async def test_unknown_domain(self, hass_mock):
        result = await execute_tool(hass_mock, "list_services", {"domain": "nonexistent"})
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_empty_domain(self, hass_mock):
        result = await execute_tool(hass_mock, "list_services", {"domain": ""})
        assert "Error" in result


# ---------------------------------------------------------------------------
# Send Notification
# ---------------------------------------------------------------------------


class TestSendNotification:
    @pytest.mark.asyncio
    async def test_no_target_found(self, hass_mock):
        result = await execute_tool(
            hass_mock,
            "send_notification",
            {
                "target": "unknown_person",
                "message": "test",
            },
        )
        assert "not found" in result or "No notification" in result


# ---------------------------------------------------------------------------
# Set Timer
# ---------------------------------------------------------------------------


class TestSetTimer:
    @pytest.mark.asyncio
    async def test_positive_minutes(self, hass_mock):
        result = await execute_tool(hass_mock, "set_timer", {"minutes": 5})
        assert "Timer set" in result or "5 minutes" in result

    @pytest.mark.asyncio
    async def test_zero_minutes_error(self, hass_mock):
        result = await execute_tool(hass_mock, "set_timer", {"minutes": 0})
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_too_many_minutes_error(self, hass_mock):
        result = await execute_tool(hass_mock, "set_timer", {"minutes": 200})
        assert "Error" in result or "max" in result.lower()


# ---------------------------------------------------------------------------
# Calendar — _is_date_only helper
# ---------------------------------------------------------------------------


class TestIsDateOnly:
    def test_date_only(self):
        assert _is_date_only("2026-12-08") is True

    def test_datetime_with_T(self):
        assert _is_date_only("2026-12-08T09:00:00") is False

    def test_empty_string(self):
        assert _is_date_only("") is False

    def test_long_string(self):
        assert _is_date_only("2026-12-08 09:00") is False


# ---------------------------------------------------------------------------
# Calendar — Create Event
# ---------------------------------------------------------------------------


class TestCreateCalendarEvent:
    @pytest.mark.asyncio
    async def test_create_timed_event(self, hass_mock):
        result = await execute_tool(
            hass_mock,
            "create_calendar_event",
            {
                "summary": "Meeting",
                "start": "2026-04-20T10:00:00",
                "end": "2026-04-20T11:00:00",
            },
        )
        assert "Created event" in result
        call_args = hass_mock.services.async_call.call_args
        service_data = call_args[0][2]
        assert "start_date_time" in service_data
        assert "start_date" not in service_data

    @pytest.mark.asyncio
    async def test_create_allday_event(self, hass_mock):
        result = await execute_tool(
            hass_mock,
            "create_calendar_event",
            {
                "summary": "Birthday",
                "start": "2026-12-08",
                "end": "2026-12-09",
            },
        )
        assert "Created event" in result
        call_args = hass_mock.services.async_call.call_args
        service_data = call_args[0][2]
        assert "start_date" in service_data
        assert "start_date_time" not in service_data

    @pytest.mark.asyncio
    async def test_create_with_entity_id(self, hass_mock):
        result = await execute_tool(
            hass_mock,
            "create_calendar_event",
            {
                "entity_id": "calendar.family",
                "summary": "Birthday",
                "start": "2026-12-08",
                "end": "2026-12-09",
            },
        )
        assert "Created event" in result
        call_args = hass_mock.services.async_call.call_args
        service_data = call_args[0][2]
        assert service_data["entity_id"] == "calendar.family"

    @pytest.mark.asyncio
    async def test_create_with_invalid_entity_id(self, hass_mock):
        result = await execute_tool(
            hass_mock,
            "create_calendar_event",
            {
                "entity_id": "calendar.nonexistent",
                "summary": "Birthday",
                "start": "2026-12-08",
                "end": "2026-12-09",
            },
        )
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_missing_required_fields(self, hass_mock):
        result = await execute_tool(
            hass_mock,
            "create_calendar_event",
            {
                "summary": "Test",
            },
        )
        assert "Error" in result
