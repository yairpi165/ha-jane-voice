# Jane Tool Calling Architecture

## Overview

Jane uses OpenAI's function calling to autonomously decide what tools to use. Instead of us pre-fetching data and hardcoding logic, GPT receives a set of tools and decides on its own what to call, when, and in what order.

This is the same pattern as Claude with MCP tools — the LLM has capabilities and uses them as needed.

---

## Current vs New Approach

### Current (hardcoded)
```
We pre-fetch all device states → send everything to GPT → GPT responds
We pre-fetch weather → send to GPT
We decide what GPT sees
```

**Problems:**
- GPT gets data it doesn't need (all entities on every call)
- GPT can't get data we didn't think to fetch (forecasts, history, calendars)
- Every new capability requires code changes
- Wastes tokens on irrelevant context

### New (tool calling)
```
GPT receives tool definitions → GPT decides what to call → we execute → GPT responds
```

**Benefits:**
- GPT only fetches what it needs
- New HA capabilities are automatic (any service, any entity)
- Web search is just another tool
- Less code, more flexible
- Lower token usage (no pre-fetching everything)

---

## Tools

### 1. get_entity_state
Read the current state and attributes of any HA entity.

```json
{
  "name": "get_entity_state",
  "description": "Get the current state of a Home Assistant entity. Use to check device status, temperature, weather, sensor readings, etc.",
  "parameters": {
    "entity_id": "The entity ID (e.g. weather.forecast_home, light.living_room)"
  }
}
```

**GPT uses this for:**
- "כמה מעלות בבית?" → `get_entity_state("weather.forecast_home")`
- "האם האור בסלון דולק?" → `get_entity_state("light.switcher_light_3708")`
- "מה מצב השואב?" → `get_entity_state("vacuum.x40_ultra")`

**Returns:** Entity state + all attributes as formatted text.

### 2. call_ha_service
Call any Home Assistant service — control devices, get forecasts, trigger scripts.

```json
{
  "name": "call_ha_service",
  "description": "Call a Home Assistant service. Use to control devices (turn on/off, set temperature, open/close), get weather forecasts, trigger scripts, or any other HA service.",
  "parameters": {
    "domain": "Service domain (e.g. light, climate, weather, script)",
    "service": "Service name (e.g. turn_on, turn_off, get_forecasts)",
    "entity_id": "Target entity ID",
    "data": "Additional service data as JSON object (optional)"
  }
}
```

**GPT uses this for:**
- "תדליקי אור בסלון" → `call_ha_service("light", "turn_on", "light.switcher_light_3708")`
- "מה מזג האוויר מחר?" → `call_ha_service("weather", "get_forecasts", "weather.forecast_home", {"type": "daily"})`
- "כבי הכל" → multiple `call_ha_service` calls
- "תפעילי את השואב" → `call_ha_service("vacuum", "start", "vacuum.x40_ultra")`

**Returns:** Service execution result or response data.

### 3. search_web
Search the internet for real-time information not available in HA.

```json
{
  "name": "search_web",
  "description": "Search the web for current information. Use ONLY when the information is not available from Home Assistant entities or services. Good for: news, exchange rates, traffic, business hours, sports scores, general knowledge.",
  "parameters": {
    "query": "Search query (Hebrew for Israeli topics, English for international)"
  }
}
```

**GPT uses this for:**
- "מה שער הדולר?" → `search_web("USD ILS exchange rate today")`
- "מה קורה בחדשות?" → `search_web("Israel news today")`
- "כמה זמן נסיעה לירושלים?" → `search_web("driving time to Jerusalem now")`

**Returns:** Tavily clean text results (answer + source snippets).

---

## Flow

### Simple device command
```
User: "תדליקי אור בסלון"
  → GPT call #1 (tools available)
  → GPT: call_ha_service("light", "turn_on", "light.switcher_light_3708")
  → HA executes → success
  → GPT call #2 (with result)
  → "בוצע"
```

### Weather forecast (from HA)
```
User: "מה מזג האוויר מחר?"
  → GPT call #1 (tools available)
  → GPT: call_ha_service("weather", "get_forecasts", "weather.forecast_home", {"type": "daily"})
  → HA returns forecast data
  → GPT call #2 (with forecast)
  → "מחר יהיה שמשי, 21 מעלות, בלי גשם"
```

### Web search (from Tavily)
```
User: "מה שער הדולר?"
  → GPT call #1 (tools available)
  → GPT: search_web("USD ILS exchange rate")
  → Tavily returns results
  → GPT call #2 (with search results)
  → "שער הדולר היום הוא 3.72 שקלים"
```

### Simple conversation (no tools)
```
User: "ספרי לי בדיחה"
  → GPT call #1 (tools available but not used)
  → GPT returns response directly
  → "למה התרנגולת חצתה את הכביש?..."
```

### Multi-tool (GPT calls multiple tools)
```
User: "כבי את כל האורות וספרי לי מה מזג האוויר"
  → GPT call #1 (tools)
  → GPT: call_ha_service("light", "turn_off", "all") + get_entity_state("weather.forecast_home")
  → Both execute
  → GPT call #2 (with results)
  → "כיביתי את כל האורות. בחוץ 19 מעלות, מעונן חלקית"
```

---

## What GPT Needs to Know

Instead of pre-fetching all entity states, GPT gets:
1. **System prompt** — personality, response format
2. **Memory** — personal, family, habits, corrections, routines (from MD files)
3. **Home layout** — `home.md` with room→device mapping + entity IDs
4. **Session history** — previous turns in this conversation
5. **Tools** — `get_entity_state`, `call_ha_service`, `search_web`

GPT uses `home.md` to know WHAT exists, and the tools to interact with them.

**We no longer pre-fetch entity states.** GPT fetches only what it needs via `get_entity_state`.

---

## Changes from Current Architecture

### What's removed
- `get_exposed_entities()` function — no more pre-fetching all states
- Hardcoded JSON response format in system prompt — GPT uses tools natively
- Custom action parsing in `execute()` — replaced by tool execution

### What's added
- `tools.py` — tool definitions + execution handlers
- Function calling loop in `brain.py` — multi-step tool use
- `web_search.py` — Tavily wrapper (one of the tools)

### What stays the same
- Memory system (load context before GPT call, extract after)
- Session history (multi-turn)
- User identification (from HA auth)
- Action logging (append_action after each interaction)

---

## System Prompt (Updated)

The system prompt simplifies dramatically. No more JSON format instructions — GPT uses tools:

```
את ג'יין — עוזרת בית חכמה ומסייעת אישית.
את מדברת עברית בצורה טבעית וידידותית.

יש לך כלים לשלוט בבית ולחפש מידע. השתמשי בהם כשצריך.
לפקודות פשוטות (הדלקת אור, כיבוי) — עני בקצרה: "בוצע", "נעשה".
לשאלות — עני בצורה טבעית ותמציתית.

אם המשתמש שואל על מזג אוויר, טמפרטורה, או מצב מכשיר — השתמשי ב-get_entity_state או call_ha_service כדי לקבל מידע עדכני.
אל תנחשי מצב מכשירים — תמיד בדקי קודם.

חפשי באינטרנט רק כשהמידע לא זמין מהבית החכם (חדשות, שערי מטבע, שעות פעילות וכו').
```

---

## Implementation

### Files

| File | Change |
|------|--------|
| `tools.py` | **New** — tool definitions, execution handlers |
| `web_search.py` | **New** — Tavily REST wrapper |
| `brain.py` | Replace hardcoded logic with function calling loop |
| `const.py` | Simplified system prompt, add Tavily key constant |
| `config_flow.py` | Add optional Tavily key |
| `strings.json` | Add Tavily field label |
| `conversation.py` | Pass Tavily key, simplify result handling |

### tools.py
```
TOOLS = [get_entity_state, call_ha_service, search_web]

execute_tool(hass, tool_name, arguments, tavily_key) → str
    Routes to the right handler:
    - get_entity_state → hass.states.get()
    - call_ha_service → hass.services.async_call()
    - search_web → tavily API call
```

### brain.py (new flow)
```
think(client, user_text, user_name, hass, history, tavily_key)
    1. Build messages: system prompt + memory + home.md + history + user text
    2. Call GPT with tools=[TOOLS] (or without search_web if no tavily key)
    3. Loop:
       a. If GPT returns tool_calls → execute each tool → append results → call GPT again
       b. If GPT returns text response → done
    4. Max 3 iterations (prevent infinite loops)
    5. Return final response text
```

No more `execute()` function — tool execution happens inside the loop.

### Token Budget
- Before: ~1,500 tokens input (prompt + ALL entities + memory)
- After: ~800 tokens input (prompt + memory + home.md) + tool results only when needed
- Net saving on most interactions (simple commands don't fetch all states)

---

## Error Handling

| Error | Behavior |
|-------|----------|
| Tool execution fails | Return error message to GPT → GPT adapts response |
| Entity not found | GPT gets "Entity not found" → asks user to clarify |
| Service call fails | GPT gets "Service failed" → tells user |
| Tavily fails | GPT gets "Search unavailable" → answers from knowledge |
| Too many tool calls (>3) | Stop loop → GPT responds with what it has |

GPT is resilient — if a tool fails, it adapts. No crashes.

---

## Jane as Home Manager

Jane is not just a remote control — she is an **autonomous home manager**. She can observe, decide, create, and manage.

### Three levels of autonomy

**Level 1: Execute** (what she does today)
```
"תדליקי אור בסלון" → turns on light
```

**Level 2: Reason & Act** (tool calling — what we're building)
```
"מה מזג האוויר מחר?" → fetches forecast from HA → answers
"כבי הכל וספרי מה הטמפרטורה" → multiple tool calls → combined answer
```

**Level 3: Create & Manage** (full autonomy)
```
"כל יום ב-7 בבוקר תדליקי חימום" → creates HA automation
"תכיני לי סצנה לערב רומנטי" → creates HA scene (dim lights, warm temp)
"כשאני יוצא מהבית תכבי הכל" → creates presence-based automation
"מחקי את האוטומציה של הבוקר" → manages existing automations
```

### Creation & Management Tools

| Tool | What GPT can do | Example |
|------|----------------|---------|
| `create_automation` | Create HA automations from natural language | "כשיורד גשם וחלון פתוח — תתריעי" |
| `list_automations` | See what automations exist | "מה האוטומציות שיש?" |
| `update_automation` | Modify existing automations | "שני את שעת החימום ל-6:30" |
| `delete_automation` | Remove automations | "תבטלי את האוטומציה הזאת" |
| `create_scene` | Create HA scenes | "תיצרי סצנה לצפייה בסרט" |
| `create_script` | Create reusable sequences | "תיצרי סקריפט ללילה טוב" |
| `get_automations` | List all automations and their status | "מה רץ עכשיו?" |

### How it works

User says: "כל ערב ב-8 תעמעמי את האור בסלון ל-30%"

GPT thinks:
1. User wants a time-based automation
2. I need to create an automation with trigger: time 20:00, action: light.turn_on with brightness 30%
3. Call `create_automation` with the right YAML

```
→ GPT: create_automation({
    alias: "Evening dim living room",
    trigger: {platform: "time", at: "20:00"},
    action: {
      service: "light.turn_on",
      entity_id: "light.switcher_light_3708",
      data: {brightness_pct: 30}
    }
  })
→ HA creates automation
→ GPT: "יצרתי אוטומציה — כל ערב ב-8 האור בסלון יעמעם ל-30%"
```

### Safety

Destructive actions require confirmation:
- Creating automations → GPT describes what it will do → waits for "כן"
- Deleting automations → "את בטוחה שלמחוק?" → waits for confirmation
- Modifying scripts → describes the change first

This is enforced in the system prompt, not in code — GPT naturally confirms before destructive actions.

---

## All Tools (Phase 1 + Phase 2)

### Phase 1 — Core (build now)

| Tool | Type | Description |
|------|------|-------------|
| `get_entity_state` | Read | Get current state of any entity |
| `call_ha_service` | Execute | Call any HA service (control devices, get forecasts) |
| `search_web` | External | Search the internet via Tavily |

### Phase 2 — Creation & Management (build next)

| Tool | Type | Description |
|------|------|-------------|
| `create_automation` | Create | Create HA automation from natural language |
| `list_automations` | Read | List existing automations |
| `update_automation` | Update | Modify an automation |
| `delete_automation` | Delete | Remove an automation |
| `create_scene` | Create | Create HA scene |
| `create_script` | Create | Create HA script |

### Phase 3 — Extended (future)

| Tool | Type | Description |
|------|------|-------------|
| `get_calendar_events` | Read | "מה יש לנו השבוע?" |
| `send_notification` | Execute | "תזכירי לי בעוד שעה" |
| `get_entity_history` | Read | "מתי הדלקתי לאחרונה את האור?" |
| `get_person_location` | Read | "איפה יאיר?" |
| `play_media` | Execute | "תפעילי מוזיקה" |

Each tool = one function definition + one handler. No brain.py changes needed.

---

## Vision

Jane evolves from a voice remote control to an intelligent home manager:

```
Today:     "תדליקי אור"              → executes command
Next:      "מה מזג האוויר מחר?"       → fetches data, answers
Then:      "תיצרי אוטומציה ל..."      → creates automations
Future:    "שמתי לב שכל ערב את מעממת   → suggests automation proactively
            אור — רוצה שאיצור אוטומציה?"
```

The tool framework makes each step trivial to add — just another tool definition.
