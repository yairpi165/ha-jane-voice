import json
from openai import OpenAI
from config import OPENAI_API_KEY, SYSTEM_PROMPT
from ha_client import get_exposed_entities, call_service
from memory import load_all_memory

client = OpenAI(api_key=OPENAI_API_KEY)

def think(user_text, user_name="default"):
    """שולח את הטקסט ל-GPT עם הקשר של הבית החכם והזיכרון"""
    entities = get_exposed_entities()
    memory_context = load_all_memory(user_name)

    entities_context = "\n".join([
        f"- {e['name']} ({e['entity_id']}) - מצב: {e['state']}"
        for e in entities
    ]) if entities else "לא נמצאו מכשירים"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": f"Memory:\n{memory_context}"},
        {"role": "system", "content": f"מכשירים בבית:\n{entities_context}"},
        {"role": "user", "content": user_text}
    ]

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        max_tokens=300,
        temperature=0.7
    )

    raw = response.choices[0].message.content.strip()
    
    # ניקוי markdown אם יש
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    
    try:
        return json.loads(raw)
    except:
        return {"action": "speak", "response": raw}

def execute(result):
    """מבצע את הפקודה שה-GPT החזיר"""
    action = result.get("action")
    response_text = result.get("response", "")

    if action == "ha_service":
        domain = result.get("domain")
        service = result.get("service")
        entity_id = result.get("entity_id")
        data = result.get("data", {})
        
        success = call_service(domain, service, entity_id, data)
        if not success:
            response_text = "סליחה, לא הצלחתי לבצע את הפקודה"

    return response_text
