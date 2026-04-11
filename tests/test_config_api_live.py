"""Live test — Config Store API against real HA.

Usage: python3 tests/test_config_api_live.py

Requires .env with:
  HA_URL=http://homeassistant.local:8123
  HA_TOKEN=your_long_lived_token
"""

import os
import re
import sys
import time
from pathlib import Path

import requests

# Load .env
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip())

HA_URL = os.environ.get("HA_URL", "").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "")

if not HA_URL or not HA_TOKEN:
    print("ERROR: Set HA_URL and HA_TOKEN in .env")
    sys.exit(1)

HEADERS = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json",
}


def _slugify(text):
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s-]+", "_", slug).strip("_")
    if len(slug) < 3 or not slug[0].isalpha():
        return f"jane_{int(time.time() * 1000)}"
    return slug[:40]


def api(method, path, json_data=None):
    url = f"{HA_URL}/api{path}"
    resp = requests.request(method, url, headers=HEADERS, json=json_data)
    print(f"  {method} {path} → {resp.status_code}")
    if resp.status_code >= 400:
        print(f"  ERROR: {resp.text}")
    return resp


def test_connection():
    print("\n=== 0. Connection ===")
    resp = api("GET", "/")
    assert resp.status_code == 200, "Cannot connect to HA"
    print("  OK")


def test_list_automations():
    print("\n=== 1. List automations (via states) ===")
    resp = api("GET", "/states")
    states = resp.json()
    automations = [s for s in states if s["entity_id"].startswith("automation.")]
    print(f"  Found {len(automations)} automations:")
    for a in automations:
        uid = a["attributes"].get("id", "?")
        name = a["attributes"].get("friendly_name", "?")
        print(f"    {uid} — {name}")
    assert len(automations) > 0, "No automations found"


def test_create_automation():
    print("\n=== 2. Create automation ===")
    unique_id = str(int(time.time() * 1000))
    config = {
        "id": unique_id,
        "alias": "QA Test — כיבוי אור ב-23:00",
        "trigger": [{"platform": "time", "at": "23:00:00"}],
        "action": [{"service": "light.turn_off", "target": {"entity_id": "light.switcher_light_3708"}}],
        "mode": "single",
    }
    resp = api("POST", f"/config/automation/config/{unique_id}", config)
    assert resp.status_code == 200, f"Create failed: {resp.text}"
    print(f"  Created with id: {unique_id}")
    return unique_id


def test_get_automation(unique_id):
    print(f"\n=== 3. Get automation config ({unique_id}) ===")
    resp = api("GET", f"/config/automation/config/{unique_id}")
    assert resp.status_code == 200, f"Get failed: {resp.text}"
    config = resp.json()
    print(f"  alias: {config.get('alias')}")
    print(f"  trigger: {config.get('trigger')}")
    return config


def test_delete_automation(unique_id):
    print(f"\n=== 4. Delete automation ({unique_id}) ===")
    resp = api("DELETE", f"/config/automation/config/{unique_id}")
    assert resp.status_code == 200, f"Delete failed: {resp.text}"
    print("  Deleted OK")


def test_create_script():
    print("\n=== 5. Create script ===")
    slug = _slugify("QA test turn off tv")
    print(f"  slug: {slug}")
    config = {
        "alias": "QA Test — כיבוי טלוויזיה",
        "sequence": [
            {"service": "media_player.turn_off", "target": {"entity_id": "media_player.sony_kd_65x85j"}},
        ],
        "mode": "single",
    }
    # Note: NO "id" field for scripts!
    resp = api("POST", f"/config/script/config/{slug}", config)
    assert resp.status_code == 200, f"Create script failed: {resp.text}"
    print(f"  Created script: {slug}")
    return slug


def test_get_script(slug):
    print(f"\n=== 6. Get script config ({slug}) ===")
    resp = api("GET", f"/config/script/config/{slug}")
    assert resp.status_code == 200, f"Get script failed: {resp.text}"
    config = resp.json()
    print(f"  alias: {config.get('alias')}")
    return config


def test_delete_script(slug):
    print(f"\n=== 7. Delete script ({slug}) ===")
    resp = api("DELETE", f"/config/script/config/{slug}")
    assert resp.status_code == 200, f"Delete script failed: {resp.text}"
    print("  Deleted OK")


def test_script_rejects_id():
    print("\n=== 8. Script rejects 'id' field ===")
    slug = f"jane_{int(time.time() * 1000)}"
    config = {
        "id": slug,  # This should be rejected!
        "alias": "Bad Script",
        "sequence": [{"delay": {"seconds": 1}}],
    }
    resp = api("POST", f"/config/script/config/{slug}", config)
    assert resp.status_code == 400, f"Expected 400 but got {resp.status_code}"
    print("  Correctly rejected (400) — scripts don't accept 'id' in body")


if __name__ == "__main__":
    print(f"HA: {HA_URL}")
    print(f"Token: {HA_TOKEN[:10]}...")

    test_connection()
    test_list_automations()

    # Automation CRUD
    uid = test_create_automation()
    test_get_automation(uid)
    test_delete_automation(uid)

    # Script CRUD
    slug = test_create_script()
    test_get_script(slug)
    test_delete_script(slug)

    # Edge case
    test_script_rejects_id()

    print("\n✅ All tests passed!")
