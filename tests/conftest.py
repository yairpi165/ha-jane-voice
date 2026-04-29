"""Shared test fixtures for Jane Voice Assistant."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

# Add custom_components to path so we can import jane_conversation
sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

# Mock homeassistant modules before importing jane_conversation
# (HA is not installed in test environment)
ha_mock = MagicMock()
sys.modules["homeassistant"] = ha_mock
sys.modules["homeassistant.core"] = ha_mock
sys.modules["homeassistant.components"] = ha_mock
sys.modules["homeassistant.components.conversation"] = ha_mock
sys.modules["homeassistant.config_entries"] = ha_mock
sys.modules["homeassistant.const"] = ha_mock
sys.modules["homeassistant.helpers"] = ha_mock
sys.modules["homeassistant.helpers.intent"] = ha_mock
sys.modules["homeassistant.helpers.entity_platform"] = ha_mock
sys.modules["homeassistant.helpers.event"] = ha_mock
sys.modules["homeassistant.helpers.area_registry"] = ha_mock
sys.modules["homeassistant.helpers.entity_registry"] = ha_mock
sys.modules["homeassistant.helpers.device_registry"] = ha_mock
sys.modules["homeassistant.helpers.floor_registry"] = ha_mock
sys.modules["homeassistant.helpers.template"] = ha_mock
sys.modules["homeassistant.helpers.collection"] = ha_mock
sys.modules["homeassistant.helpers.aiohttp_client"] = ha_mock
sys.modules["homeassistant.util"] = ha_mock
sys.modules["homeassistant.util.dt"] = ha_mock
sys.modules["homeassistant.util.yaml"] = ha_mock
sys.modules["homeassistant.components.recorder"] = ha_mock
sys.modules["homeassistant.components.recorder.history"] = ha_mock
sys.modules["homeassistant.components.select"] = ha_mock
sys.modules["homeassistant.helpers.restore_state"] = ha_mock


def make_state(entity_id, state, attributes=None):
    """Create a mock HA state object."""
    s = MagicMock()
    s.entity_id = entity_id
    s.state = state
    s.domain = entity_id.split(".")[0]
    s.attributes = attributes or {"friendly_name": entity_id.split(".")[1].replace("_", " ").title()}
    s.last_changed = MagicMock()
    return s


@pytest.fixture
def hass_mock():
    """Mock Home Assistant instance."""
    hass = AsyncMock()

    # Weather state
    weather = make_state(
        "weather.forecast_home",
        "sunny",
        {
            "temperature": 25,
            "friendly_name": "Forecast Home",
        },
    )

    # People
    person_alice = make_state("person.alice", "home", {"friendly_name": "Alice"})
    person_bob = make_state("person.bob", "not_home", {"friendly_name": "Bob"})

    # Lights
    light_living = make_state("light.living_room", "on", {"friendly_name": "אור סלון", "brightness": 200})
    light_bedroom = make_state("light.bedroom", "off", {"friendly_name": "אור חדר שינה"})

    # Climate
    ac = make_state("climate.ac", "cool", {"friendly_name": "מזגן", "temperature": 24})

    # Media
    tv = make_state("media_player.tv", "on", {"friendly_name": "טלוויזיה"})

    # Camera (should be filtered)
    camera = make_state("media_player.camera_stream", "on", {"friendly_name": "Camera Stream"})

    # Calendars
    cal_family = make_state("calendar.family", "off", {"friendly_name": "משפחה"})
    cal_personal = make_state("calendar.personal", "off", {"friendly_name": "אישי"})

    all_states = [
        weather,
        person_alice,
        person_bob,
        light_living,
        light_bedroom,
        ac,
        tv,
        camera,
        cal_family,
        cal_personal,
    ]

    def get_state(entity_id):
        for s in all_states:
            if s.entity_id == entity_id:
                return s
        return None

    def async_all(domain=None):
        if domain:
            return [s for s in all_states if s.domain == domain]
        return all_states

    hass.states.get = MagicMock(side_effect=get_state)
    hass.states.async_all = MagicMock(side_effect=async_all)
    hass.states.async_entity_ids = MagicMock(return_value=[s.entity_id for s in all_states])
    hass.services.async_call = AsyncMock(return_value=None)
    hass.services.async_services = MagicMock(return_value={})
    hass.config.config_dir = "/tmp/jane_test"
    hass.async_add_executor_job = AsyncMock(side_effect=lambda fn, *args: fn(*args))
    hass.async_create_task = MagicMock()

    return hass


@pytest.fixture
def tmp_memory_dir(tmp_path):
    """Temporary memory directory for file I/O tests."""
    mem = tmp_path / "jane_memory"
    (mem / "users").mkdir(parents=True)
    return mem


@pytest.fixture
def gemini_client_mock():
    """Mock Gemini client."""

    client = MagicMock()

    def make_text_response(text):
        part = MagicMock()
        part.text = text
        part.function_call = None
        type(part).text = PropertyMock(return_value=text)
        content = MagicMock()
        content.parts = [part]
        candidate = MagicMock()
        candidate.content = content
        response = MagicMock()
        response.candidates = [candidate]
        return response

    def make_tool_call_response(name, args):
        fc = MagicMock()
        fc.name = name
        fc.args = args
        part = MagicMock()
        part.function_call = fc
        part.text = None
        type(part).text = PropertyMock(return_value=None)
        content = MagicMock()
        content.parts = [part]
        content.role = "model"
        candidate = MagicMock()
        candidate.content = content
        response = MagicMock()
        response.candidates = [candidate]
        return response

    client._make_text_response = make_text_response
    client._make_tool_call_response = make_tool_call_response

    return client
