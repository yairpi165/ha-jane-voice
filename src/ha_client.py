import requests
from config import HA_URL, HA_TOKEN

HEADERS = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json"
}

def get_states():
    """מחזיר את כל המכשירים והסטטוס שלהם"""
    try:
        r = requests.get(f"{HA_URL}/api/states", headers=HEADERS, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"❌ שגיאה בחיבור ל-HA: {e}")
        return []

def get_exposed_entities():
    """מחזיר רשימה מסוננת של מכשירים רלוונטיים"""
    states = get_states()
    relevant_domains = ["light", "switch", "climate", "cover", "media_player", "fan"]
    entities = []
    for s in states:
        domain = s["entity_id"].split(".")[0]
        if domain in relevant_domains:
            entities.append({
                "entity_id": s["entity_id"],
                "name": s.get("attributes", {}).get("friendly_name", s["entity_id"]),
                "state": s["state"],
                "domain": domain
            })
    return entities

def call_service(domain, service, entity_id, data=None):
    """מפעיל שירות ב-HA"""
    try:
        payload = {"entity_id": entity_id}
        if data:
            payload.update(data)
        r = requests.post(
            f"{HA_URL}/api/services/{domain}/{service}",
            headers=HEADERS,
            json=payload,
            timeout=5
        )
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"❌ שגיאה בביצוע פקודה: {e}")
        return False

def test_connection():
    """בדיקת חיבור ל-HA"""
    try:
        r = requests.get(f"{HA_URL}/api/", headers=HEADERS, timeout=5)
        return r.status_code == 200
    except:
        return False
