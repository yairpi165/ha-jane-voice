#!/usr/bin/env python3
"""Jane CLI — Run Jane locally against real HA via REST API.

Usage:
    python3 jane_cli.py "תיצרי אוטומציה שמכבה אור ב-23:00"
    python3 jane_cli.py   (interactive mode)

Requires .env with:
    HA_URL=http://homeassistant.local:8123
    HA_TOKEN=your_long_lived_token
    GEMINI_API_KEY=your_gemini_key
"""

import asyncio
import json
import os
import sys
from pathlib import Path

# Load .env
_env = Path(__file__).parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

HA_URL = os.environ.get("HA_URL", "").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

if not HA_URL or not HA_TOKEN or not GEMINI_API_KEY:
    print("ERROR: Set HA_URL, HA_TOKEN, GEMINI_API_KEY in .env")
    sys.exit(1)

import requests


# ---------------------------------------------------------------------------
# FakeHass — wraps HA REST API to look like hass object
# ---------------------------------------------------------------------------

class _StateObj:
    def __init__(self, data: dict):
        self.entity_id = data.get("entity_id", "")
        self.state = data.get("state", "")
        self.domain = self.entity_id.split(".")[0] if "." in self.entity_id else ""
        self.attributes = data.get("attributes", {})
        self.last_changed = data.get("last_changed", "")


class _States:
    def __init__(self, url, headers):
        self._url = url
        self._headers = headers
        self._cache = None

    def _fetch_all(self):
        if self._cache is None:
            resp = requests.get(f"{self._url}/api/states", headers=self._headers)
            self._cache = [_StateObj(s) for s in resp.json()]
        return self._cache

    def get(self, entity_id):
        for s in self._fetch_all():
            if s.entity_id == entity_id:
                return s
        return None

    def async_all(self, domain=None):
        states = self._fetch_all()
        if domain:
            return [s for s in states if s.domain == domain]
        return states

    def async_entity_ids(self, domain=None):
        return [s.entity_id for s in self.async_all(domain)]


class _Services:
    def __init__(self, url, headers):
        self._url = url
        self._headers = headers

    async def async_call(self, domain, service, service_data=None, target=None, blocking=True, return_response=False):
        data = service_data or {}
        if target:
            data["target"] = target
        resp = requests.post(
            f"{self._url}/api/services/{domain}/{service}",
            headers=self._headers,
            json=data,
        )
        if return_response:
            try:
                return resp.json()
            except Exception:
                return {}
        return None

    def async_services(self):
        resp = requests.get(f"{self._url}/api/services", headers=self._headers)
        return resp.json()


class _FakeRefreshToken:
    def __init__(self):
        self.client_name = "Jane Internal API"

class _FakeOwner:
    def __init__(self):
        self.refresh_tokens = {"fake": _FakeRefreshToken()}

class _Auth:
    """Fake auth — returns the HA_TOKEN from .env for all API calls."""
    def __init__(self, token):
        self._token = token

    async def async_get_owner(self):
        return _FakeOwner()

    def async_create_access_token(self, refresh_token):
        return self._token

    async def async_create_refresh_token(self, *args, **kwargs):
        return _FakeRefreshToken()


class _Http:
    def __init__(self, url):
        # Extract port from URL
        from urllib.parse import urlparse
        parsed = urlparse(url)
        self.server_port = parsed.port or 8123


class _Config:
    def __init__(self):
        self.config_dir = "/tmp/jane_cli"
        Path(self.config_dir).mkdir(exist_ok=True)


class FakeHass:
    """Mimics hass object using HA REST API."""
    def __init__(self, url, token):
        self._url = url
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self.states = _States(url, self._headers)
        self.services = _Services(url, self._headers)
        self.auth = _Auth(token)
        self.http = _Http(url)
        self.config = _Config()
        self.data = {"jane_conversation": {}}

    async def async_add_executor_job(self, fn, *args):
        return fn(*args)

    def async_create_task(self, coro):
        pass


# ---------------------------------------------------------------------------
# Patch homeassistant modules before importing Jane
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock

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

# Make async_get_clientsession return a mock that uses requests
class _FakeRequestCM:
    """Async context manager that mimics aiohttp's session.request()."""
    def __init__(self, method, url, kwargs):
        self._method = method
        # Rewrite 127.0.0.1 URLs to actual HA URL (CLI runs remotely, not inside HA)
        self._url = url.replace("http://127.0.0.1:8123", HA_URL)
        self._kwargs = kwargs

    async def __aenter__(self):
        resp = requests.request(
            self._method, self._url,
            headers=self._kwargs.get("headers", {}),
            json=self._kwargs.get("json"),
        )
        return _FakeResp(resp)

    async def __aexit__(self, *args):
        pass


class _FakeSession:
    def request(self, method, url, **kwargs):
        """Return a context manager, not a coroutine (like aiohttp)."""
        return _FakeRequestCM(method, url, kwargs)


class _FakeResp:
    def __init__(self, resp):
        self.status = resp.status_code
        self._resp = resp

    async def json(self, content_type=None):
        try:
            return self._resp.json()
        except Exception:
            return {}

    async def text(self):
        return self._resp.text


# All sys.modules point to the same ha_mock, so the import
# `from homeassistant.helpers.aiohttp_client import async_get_clientsession`
# resolves to ha_mock.async_get_clientsession (not ha_mock.helpers.aiohttp_client.xxx)
ha_mock.async_get_clientsession = lambda hass: _FakeSession()

# Entity registry mock — return None so resolve_config_id falls back to bare ID
_fake_er = MagicMock()
_fake_er.async_get.return_value = None  # No entity found → use fallback
ha_mock.async_get = lambda hass: _fake_er  # er.async_get(hass)

# Add custom_components to path
sys.path.insert(0, str(Path(__file__).parent / "custom_components"))

from jane_conversation.tools import execute_tool, _ALL_FUNCTION_DECLARATIONS
from jane_conversation.brain import _classify_request, _build_context

# Gemini client
from google import genai
from google.genai import types


# ---------------------------------------------------------------------------
# Run Jane
# ---------------------------------------------------------------------------

async def run_jane(text: str) -> str:
    hass = FakeHass(HA_URL, HA_TOKEN)

    client = genai.Client(api_key=GEMINI_API_KEY)

    # Classify
    category = _classify_request(text)
    print(f"\n[classify] {category}")

    # Build context
    context = await _build_context(hass)
    print(f"[context] {context[:100]}..." if len(context) > 100 else f"[context] {context}")

    # Model
    from jane_conversation.const import GEMINI_MODEL_FAST, GEMINI_MODEL_SMART, SYSTEM_PROMPT
    model = GEMINI_MODEL_SMART if category == "complex" else GEMINI_MODEL_FAST

    # Tools
    from jane_conversation.tools import get_tools, get_tools_minimal
    tools = get_tools_minimal() if category == "chat" else get_tools()

    # System prompt
    system = SYSTEM_PROMPT + "\n\n" + context

    print(f"[model] {model}")
    print(f"[tools] {len(_ALL_FUNCTION_DECLARATIONS)} available")

    # Call Gemini
    messages = [types.Content(role="user", parts=[types.Part(text=text)])]

    for iteration in range(10):
        response = client.models.generate_content(
            model=model,
            contents=messages,
            config=types.GenerateContentConfig(
                system_instruction=system,
                tools=tools,
                max_output_tokens=2000,
                temperature=0.7,
            ),
        )

        if not response.candidates or not response.candidates[0].content or not response.candidates[0].content.parts:
            print("[gemini] Empty response")
            return ""

        parts = response.candidates[0].content.parts
        messages.append(response.candidates[0].content)

        # Check for tool calls
        tool_calls = [p for p in parts if p.function_call]
        if not tool_calls:
            # Text response
            text_parts = [p.text for p in parts if p.text]
            result = " ".join(text_parts)
            print(f"\n[jane] {result}")
            return result

        # Execute tool calls
        tool_response_parts = []
        for part in tool_calls:
            fc = part.function_call
            print(f"[tool] {fc.name}({json.dumps(dict(fc.args), ensure_ascii=False)[:200]})")

            tool_result = await execute_tool(hass, fc.name, dict(fc.args))
            print(f"[result] {tool_result[:200]}")

            tool_response_parts.append(
                types.Part(function_response=types.FunctionResponse(
                    name=fc.name,
                    response={"result": tool_result},
                ))
            )

        messages.append(types.Content(role="user", parts=tool_response_parts))

    return "[max iterations reached]"


async def interactive():
    print("Jane CLI — type a command (or 'exit' to quit)")
    print(f"HA: {HA_URL}\n")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye!")
            break
        if not text or text.lower() in ("exit", "quit", "q"):
            break
        await run_jane(text)
        print()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        asyncio.run(run_jane(" ".join(sys.argv[1:])))
    else:
        asyncio.run(interactive())
