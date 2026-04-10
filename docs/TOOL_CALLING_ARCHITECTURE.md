# Jane Tool Calling Architecture

## Overview

Jane uses Gemini 2.5's function calling to autonomously decide what tools to use. Dual model: Gemini 2.5 Flash (fast commands) + Gemini 2.5 Pro (complex reasoning). 38 tools + Google Search built-in.

Model history: GPT-5.4 Mini → Claude Sonnet 4 → **Gemini 2.5 Pro** (current).

---

## How It Works

```
User speaks → Whisper STT → text
                              │
                              ▼
                    ┌──────────────────┐
                    │    brain.py       │
                    │                   │
                    │  System prompt    │
                    │  + Context injection (weather, people, home state)
                    │  + Memory context │
                    │  + Session history│
                    │  + User text      │
                    │                   │
                    │  GPT-5.4 Mini     │──→ Tool call?
                    │  (function calling)│      │
                    └──────────┬────────┘      │
                               │           ┌───┴───┐
                               │           │ Yes   │ No → return response
                               │           └───┬───┘
                               │               ▼
                               │    Execute tool(s) via tools.py
                               │               │
                               │               ▼
                               │    Feed result back to GPT
                               │               │
                               │    ◄──────────┘ (loop, max 10 iterations)
                               │
                               ▼
                    Final response → TTS → Speaker
```

---

## All Tools (v2.8.0 — 14 tools)

### Core — Device Control
| Tool | What it does | Example |
|------|-------------|---------|
| `get_entity_state` | Read current state + attributes | "כמה מעלות?", "האור דולק?" |
| `call_ha_service` | Call any HA service | "תדליקי אור", "תשני ל-24 מעלות" |

### Discovery — Find & Explore
| Tool | What it does | Example |
|------|-------------|---------|
| `search_entities` | Find entities by name/domain | "מה יש בחדר שינה?", "תמצאי חיישן טמפרטורה" |
| `list_areas` | List all rooms + devices | "מה החדרים בבית?" |
| `get_history` | State change history | "מתי המזגן נדלק?", "כמה זמן רץ השואב?" |
| `get_statistics` | Min/max/avg over time | "מה הטמפרטורה הממוצעת היום?" |
| `get_logbook` | Recent events | "מה קרה בבית היום?" |

### Family Life
| Tool | What it does | Example |
|------|-------------|---------|
| `check_people` | Who's home, where | "מי בבית?", "איפה אפרת?" |
| `send_notification` | Push to phone/tablet | "תשלחי ליאיר הודעה שאני מאחרת" |
| `set_timer` | Countdown + notification | "טיימר 5 דקות", "תזכירי לי בעוד 10 דקות" |
| `manage_list` | Shopping/todo lists | "תוסיפי חלב לרשימת קניות" |
| `tts_announce` | Broadcast via speaker | "תגידי לילדים שארוחת ערב מוכנה" |

### Creation & Management
| Tool | What it does | Example |
|------|-------------|---------|
| `set_automation` | Create/update automation | "תיצרי אוטומציה לחימום כל בוקר ב-7" |
| `remove_automation` | Delete automation | "תמחקי את האוטומציה" |
| `set_script` | Create/update script | "תיצרי סקריפט שמכבה טלוויזיה" |
| `remove_script` | Delete script | "תמחקי את הסקריפט" |
| `set_scene` | Create/update scene | "תיצרי סצנה של ערב סרט" |
| `remove_scene` | Delete scene | "תמחקי את הסצנה" |
| `list_config` | List all automations/scripts/scenes | "כמה אוטומציות יש לי?" |

### External
| Tool | What it does | Example |
|------|-------------|---------|
| `search_web` | Tavily web search | "מה שער הדולר?", "מה קורה בחדשות?" |

---

## Tool Implementation Details

### get_entity_state
- **Handler**: `hass.states.get(entity_id)`
- **Returns**: Entity name, state, and all useful attributes (skips internal ones)
- **GPT uses**: To check any device before acting or answering

### call_ha_service
- **Handler**: `hass.services.async_call(domain, service, data, blocking=True)`
- **Returns**: "Success" or response data (for weather/calendar/todo services)
- **Special**: `return_response=True` for services that return data (weather.get_forecasts, todo.get_items, calendar.get_events)
- **Data examples**: brightness_pct, temperature, volume_level (0.0–1.0), position (0–100)

### search_entities
- **Handler**: Iterates `hass.states.async_all()`, fuzzy match on friendly_name and entity_id
- **Returns**: JSON array of matching entities with state (max 15 results)
- **Why needed**: Jane can find devices without knowing exact entity_ids

### get_history
- **Handler**: `recorder.history.get_significant_states()` via executor
- **Returns**: Last 25 state changes with timestamps and key attributes
- **Fallback**: Graceful error if recorder not loaded

### list_areas
- **Handler**: `area_registry` + `entity_registry` + `device_registry`
- **Returns**: Rooms with all their entities and current states
- **Includes**: Unassigned devices section for devices not in any area

### send_notification
- **Handler**: Dynamically finds notify service matching target name
- **Returns**: Confirmation or list of available targets
- **Services**: `notify.mobile_app_yair_phone_14`, `notify.mobile_app_home`, `notify.notify`

### check_people
- **Handler**: Reads all `person.*` entities
- **Returns**: Name + location (home/away/zone) + GPS if available

### set_timer
- **Handler**: `asyncio.sleep(minutes * 60)` → persistent notification + push
- **Limits**: Max 120 minutes (longer → use ha_config_api for automation)
- **Note**: In-memory, does not survive HA restart

### manage_list
- **Handler**: Dynamically finds todo entity by name matching
- **Services**: `todo.get_items`, `todo.add_item`, `todo.remove_item`
- **Lists**: רשימת קניות, יאיר, אפרת, משפחתי, אלון, יערה

### get_statistics
- **Handler**: `recorder.history.get_significant_states()` → extract numeric values → calculate min/max/avg
- **Returns**: Average, min, max, current value with unit of measurement

### get_logbook
- **Handler**: `recorder.history.get_significant_states()` across interesting domains
- **Domains**: light, climate, cover, media_player, switch, vacuum, lock, person, fan, water_heater
- **Returns**: Last 30 state changes sorted by time

### tts_announce
- **Handler**: Finds TTS entity + media player dynamically → `tts.speak`
- **Priority**: Prefers HomePod mini (`media_player.slvn_2`), falls back to any speaker

### set_automation / set_script / set_scene
- **Handler**: `config_api.set_config()` → HA Config Store REST API (`POST /api/config/{resource}/config/{id}`)
- **Auth**: Internal LLAT created on first use via `hass.auth`
- **Validation**: Required fields (alias, trigger, action), blueprint support, duplicate guard
- **Normalization**: triggers→trigger, actions→action, trigger→platform (round-trip safe)
- **Polling**: Verifies entity was created after POST (3 attempts)
- **Scripts**: slug from English alias, no `id` in body (HA rejects it)

### remove_automation / remove_script / remove_scene
- **Handler**: `config_api.remove_config()` → `DELETE /api/config/{resource}/config/{id}`
- **Resolution**: entity_id → unique_id via state attributes (automations) or entity registry (scripts)

### list_config
- **Handler**: `config_api.list_config()` → `hass.states.async_all(resource)`
- **Returns**: id + alias for each item

### search_web
- **Handler**: Tavily REST API (`search_web.py`)
- **Condition**: Only available when Tavily API key is configured
- **Returns**: Clean text answer + source snippets

---

## Planned Tools (v3.0.0)

### save_memory
Explicit memory management during conversation. Jane calls this when she learns something important.
```
save_memory(category="family", content="Maor is 8 years old, likes soccer")
```
Coexists with background memory extraction.

---

## Planned: Context Injection (v3.0.0)

Not a tool — automatically injected as system message before every conversation:

```python
# In brain.py, before GPT call:
context = []
context.append(f"Weather: {weather_state} {temperature}°C")
context.append(f"People: {', '.join(people_at_home)} at home")
context.append(f"Active devices: {', '.join(active_devices)}")
context.append(f"Calendar: {today_events}")

messages.append({"role": "system", "content": "Current context:\n" + "\n".join(context)})
```

This gives Jane ambient awareness without needing tool calls for basic context.

---

## Flow Examples

### Simple command
```
User: "תדליקי אור בסלון"
  → GPT sees home.md → knows light.switcher_light_3708
  → call_ha_service("light", "turn_on", "light.switcher_light_3708")
  → "הדלקתי"
```

### Discovery + action
```
User: "תרתיחי מים"
  → GPT searches home.md for "מים" or "tami" → finds button.myny_br_boil_water
  → call_ha_service("button", "press", "button.myny_br_boil_water")
  → "המים ברזי מרתיחים"
```

### Multi-tool reasoning
```
User: "יוצא מהבית, תסגרי הכל"
  → GPT calls list_areas or uses home.md
  → call_ha_service: turn_off lights (multiple)
  → call_ha_service: turn_off AC
  → call_ha_service: close covers
  → "סגרתי הכל — אורות, מזגן, תריסים. יום טוב!"
```

### Family interaction
```
User: "מי בבית?"
  → check_people()
  → "יאיר בבית, אפרת לא בבית."
```

### Timer + notification
```
User: "תזכירי לי בעוד 10 דקות לצאת"
  → set_timer(10, "תזכורת: הגיע הזמן לצאת!")
  → "בסדר, אזכיר לך בעוד 10 דקות."
  ... 10 minutes later → push notification
```

### Context-aware greeting (v3.0.0)
```
User: "בוקר טוב ג'יין"
  → Context already injected: 28°C, sunny, Yair home, Efrat left at 7:30
  → "בוקר טוב יאיר! היום חם, 28 מעלות ושמשי. אפרת כבר יצאה ב-7:30."
  (No tool calls needed — context was pre-loaded)
```

---

## Configuration

### Gemini Settings
- **Models**: gemini-2.5-flash (chat/commands) + gemini-2.5-pro (complex)
- **max_output_tokens**: 500 (Flash) / 2000 (Pro)
- **MAX_TOOL_ITERATIONS**: 10
- **Temperature**: Dynamic:
  - Commands ("הדלק", "כבה"): 0.4
  - Conversation ("מה שלומך"): 0.8
  - Default: 0.7
- **Google Search**: Built-in via `types.Tool(google_search=types.GoogleSearch())`
  - Can't combine with function_declarations in same request
  - search_web handler calls Gemini separately with GoogleSearch

### Context Injection
Every Gemini call receives home awareness via `system_instruction`:
- Weather (state + temperature from `weather.forecast_home`)
- People (home/away from `person.*` entities)
- Active devices (lights/climate/media that are ON, cameras filtered)

### Anti-Repetition
Last 10 response openings tracked in memory. Injected in system_instruction.

### Config Store API (v3.4.0)
- ha_config_api uses HA's REST API (`/api/config/{resource}/config/{id}`) — same as MCP and HA UI
- No direct YAML file reading/writing — HA handles all serialization
- Internal LLAT (Long-Lived Access Token) created via `hass.auth` on first use
- Normalizes plural keys (triggers→trigger, actions→action) before sending to API

### API Keys
- **Gemini**: Required (config flow)
- **Firebase**: Optional (options flow, enables memory backup)
- **Tavily**: No longer needed (Google Search replaces it)

### Tests
98 tests in 7 files, run with `pytest tests/ -v`:
- Brain: classification, context, text extraction
- Tools: config key normalization, format validation, routing
- HA Handlers: all 33 tool handlers
- Memory: tracking, file I/O, logs
- Gemini API: history conversion, model selection, tool loop
- E2E: full conversation flows
- Conversation: hallucination filter

---

## Error Handling

| Error | Behavior |
|-------|----------|
| Tool execution fails | Error message returned to GPT → GPT adapts response |
| Entity not found | GPT gets "Entity not found" → tries search_entities or asks user |
| Service call fails | GPT gets "Service failed: {error}" → tells user |
| Recorder not loaded | History/stats/logbook return "not available" → GPT answers from knowledge |
| Notify target not found | Returns available targets → GPT can retry |
| Timer >120 min | Error suggests using ha_config_api instead |
| Max iterations reached | Force final response without tools |

GPT is resilient — if a tool fails, it adapts. No crashes.
