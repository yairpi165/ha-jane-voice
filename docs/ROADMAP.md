# Jane — Implementation Roadmap

Prioritized list of features to implement, ordered by impact.
Each item is planned and approved before implementation begins.

---

## Completed

### 1. Voice Pipeline + HA Control ✅
Basic voice conversation in Hebrew. GPT processes commands, controls HA devices.

### 2. HA Conversation Agent ✅
Jane is a native custom_component integrated with the Assist pipeline.

### 3. Memory System ✅
LLM-managed markdown memory. 7 files: personal, family, habits, actions, home map, corrections, routines.
See [MEMORY_ARCHITECTURE.md](MEMORY_ARCHITECTURE.md).

### 4. Multi-turn Conversations ✅
Session history in RAM. Last 10 turns per session.

### 5. Phase 1 Tools — Core ✅
GPT uses OpenAI function calling to autonomously decide what tools to use.
- `get_entity_state` — read any HA entity
- `call_ha_service` — control devices, get forecasts, trigger scripts
- `search_web` — Tavily web search (optional)

### 6. Custom Wake Word ✅
"Hey Jane" microWakeWord model (62KB tflite, probability_cutoff=0.5).
Used with Voice Satellite Card on Android tablet via WallPanel.

### 7. OpenAI TTS ✅
OpenAI TTS (voice: nova) via HACS. Profile "jane", model tts-1.

### 8. Concise Responses ✅
Simple commands get short answers. Conversations get full responses.

### 9. Night Mode ✅
23:00–07:00: shorter responses, whisper-friendly. Current time injected into GPT context.

### 10. Continue Conversation ✅
Keep listening when Jane asks a question (response ends with ?).
Uses `continue_conversation` flag in ConversationResult.

### 11. Phase 2 Tools — Create & Manage ✅
`ha_config_api(resource, operation, config, item_id)` — generic CRUD for automations, scenes, scripts.
Writes to YAML config files + reloads domain. asyncio.Lock per resource type.

### 12. Firebase Memory Backup ✅
Write-through Firestore backup. Save locally → push to Firebase in background.
On startup, restore missing files from cloud. Optional — works without Firebase too.

### 13. Command History Log ✅
Permanent `history.log` — every command + response with timestamp. Never pruned.

### 14. HACS Deployment ✅
Public GitHub repo → HACS installation. Version bumps via manifest.json + GitHub Releases.

### 15. GPT-5.4 Mini Upgrade ✅
Upgraded from GPT-4o Mini. Uses `max_completion_tokens` (not `max_tokens`). 400K context, 2x faster.

### 16. Whisper Hallucination Filter ✅
gpt-4o-mini-transcribe with Hebrew prompt hints + code-level hallucination set filter.
Catches phantom phrases from silence/noise ("תודה רבה", "thanks for watching", etc.).

### 17. System Prompt v2 — Personality ✅ (v2.6.0)
Complete rewrite for warm, curious, conversational personality.
- Natural colloquial Hebrew
- Autonomous thinking: understand → find → do → confirm
- Curiosity — asks follow-up questions about family
- Memory management instructions
- Night mode behavior

### 18. Phase 3 Tools — Discovery ✅ (v2.7.0)
- `search_entities` — find devices by name/room/type
- `get_history` — entity state change history
- `list_areas` — rooms and devices from HA registries

### 19. Phase 4 Tools — Family Life ✅ (v2.8.0)
- `send_notification` — push notifications to family phones
- `check_people` — who's home, where are they
- `set_timer` — countdown timer with notification on expiry
- `manage_list` — shopping/todo list management
- `get_statistics` — sensor min/max/average over time
- `get_logbook` — recent events and state changes
- `tts_announce` — broadcast messages through speakers

### 20. Context Injection ✅ (v3.0.0)
Every conversation starts with real-time awareness — injected automatically as system message:
- **Weather**: current conditions + temperature from `weather.forecast_home`
- **People**: who's home, who's away from `person.*` entities
- **Active devices**: lights/climate/media that are ON

~50-100 tokens of ambient context. Jane can say "בוקר טוב! חם היום, 34 מעלות" without tool calls.

Implementation: `_build_context()` in `brain.py` reads HA state and injects as system message.

### 21. Dynamic Temperature Strategy ✅ (v3.0.0)
Different GPT parameters based on request type:
- **Commands** ("הדלק", "כבה"): temperature=0.4 — precise tool calls
- **Conversation** ("מה שלומך", "ספרי"): temperature=0.9, frequency_penalty=1.5, presence_penalty=0.6 — warm and varied
- **Default**: temperature=0.7, frequency_penalty=0.5

Implementation: `_get_model_params()` in `brain.py` detects Hebrew keywords and returns appropriate params.

### 22. Higher Thinking Limits ✅ (v3.0.0)
- `max_completion_tokens`: 1000 → 2000 (more room for complex reasoning)
- `MAX_TOOL_ITERATIONS`: 5 → 10 (more steps for multi-action tasks)

Enables complex requests: "תסדרי לי את הבית לשבת" (multiple lights, AC, shutters, scenes).

### 23. System Prompt v3 — Autonomous Agent ✅ (v3.0.0)
Based on GPT-5.4 prompt engineering research (ReAct pattern, persistence reminders):
- **Autonomous thinking**: "Keep working until done, don't ask to continue"
- **Tool usage rules**: "NEVER guess — always use tools. If unsure, search first."
- **Emotional awareness**: detect frustration/rush/relaxation, adapt tone
- Time injection now includes day of week

### 24. Anti-Repetition System ✅ (v3.0.0)
Track last 10 response openings in memory and inject as "don't repeat these" context.
Forces GPT to vary greetings, confirmations, and conversation starters.

Implementation: `track_response()` and `get_recent_responses()` in `memory.py`, injected in `brain.py`.

---

### 25. Switch to Claude Sonnet 4 ✅ (v3.1.0)
Brain switched from GPT-5.4 Mini to Claude Sonnet 4 (Anthropic API).
- Reliable tool calling — no more "technical limitation" excuses
- Better Hebrew, better instruction following
- Tool definitions converted from OpenAI to Anthropic format (input_schema)
- System prompt as separate parameter (not in messages array)

### 26. 33 Tools ✅ (v3.2.0)
Added 19 new tools (was 14), total 33:
- Discovery: eval_template, get_overview, list_floors, get_zone
- Family: get_calendar_events, create_calendar_event
- Device mgmt: get_device, rename_entity, update_device, list_services, list_helpers, create_helper
- Config: get_automation_config, get_script_config, get_automation_traces, deep_search
- Memory: save_memory, read_memory
- Control: bulk_control

### 27. Dual Model — Haiku + Sonnet ✅ (v3.2.0)
Request classification determines which model handles the request:
- **Chat** ("מה שלומך", "בוקר טוב"): Haiku 4.5 — fast, max_tokens=500, no tools
- **Command** ("הדלק", "כבה"): Haiku 4.5 — fast, max_tokens=500, all tools
- **Complex** (automations, questions, multi-step): Sonnet 4 — smart, max_tokens=2000, all tools

### 28. Smart Memory ✅ (v3.2.0)
Only `home.md` loaded every request. All other memory accessed via `read_memory` tool on demand.
Jane decides what memory she needs — like Claude with its memory files.

### 29. Prompt Caching + Tool Filtering ✅ (v3.2.0)
- System prompt cached with Anthropic `cache_control: ephemeral` (5 min TTL)
- Chat requests get 2 tools (save_memory, read_memory) instead of 33
- Dynamic max_tokens: Haiku=500, Sonnet=2000

### 30. Config API Safety ✅ (v3.2.0)
- Backup file created before every YAML write (automations.yaml.bak)
- Refuses to write if read fails (prevents data loss from overwriting)
- Detailed logging at every step

---

## Planned — v3.3.0

### 31. ha_config_api → HA Config Store API
**Critical upgrade:** Replace direct YAML file writing with HA's internal Config Store API.
Currently ha_config_api writes to `automations.yaml` directly — this is fragile and caused
data loss (overwriting existing automations). HA's Config Store API (used by the UI and MCP)
writes to `.storage/` which is safer, supports undo, and doesn't conflict with UI-created automations.

This is the same API the MCP tools (ha_config_set_automation) use — proven reliable.

### 32. Per-User Behavior
Jane adapts personality per family member:
- **Yair (admin)**: direct, tech-aware, brief confirmations
- **Kids**: gentler, explains more, restricted late-night access
- **Guests**: polite but limited device control

Implementation: user_name-based prompt injection + permission matrix in memory.

### 33. Routine Execution
Named multi-step routines stored in routines.md:
- "לילה טוב" → turn off lights, close shutters, set AC to 24, lock door
- "יוצא מהבית" → turn off everything, lock up
- "בוקר טוב" → open shutters, report weather, turn on heating

Jane chains multiple tool calls automatically from routine definitions.

### 34. Proactive Behavior (Background Loop)
Jane monitors the home and speaks up when relevant:
- "המזגן דולק כבר 5 שעות, לכבות?"
- "אפרת עזבה את הבית"
- "חלון פתוח ויורד גשם"
- "כבר 23:00 והאורות בחדר ילדים עדיין דולקים"

Implementation: `proactive.py` with `async_track_state_change_event` + `async_track_time_interval`.
AlertManager with cooldowns (15min door, 1h AC, 1d suggestions) to avoid spam.

---

## Future — v4.0.0

### 35. Voice Recognition (Speaker ID)
Identify who is speaking from voice alone — no "who is this?" needed.
Azure Speaker Recognition API or local model.

### 36. Face Recognition
Identify who is in the room via camera + Frigate.
Presence-based context for proactive behavior.

### 37. ElevenLabs TTS
More natural Hebrew voice. Multilingual v2 model with Hebrew support.

### 38. Multi-Room Satellites
Wyoming Protocol + ESP32 devices — independent audio per room.
Jane knows which room you're in and responds on the right speaker.

### 39. Learning & Suggestions
Jane notices patterns and suggests automations:
- "שמתי לב שכל ערב אתה מעמעם אור — רוצה שאיצור אוטומציה?"
- "כל בוקר אתה מדליק חימום ב-7, רוצה שזה יהיה אוטומטי?"

---

## Tool Evolution

| Version | Tools | Total |
|---------|-------|-------|
| v2.0.0 | get_entity_state, call_ha_service, search_web | 3 |
| v2.3.0 | + ha_config_api | 4 |
| v2.7.0 | + search_entities, get_history, list_areas | 7 |
| v2.8.0 | + send_notification, check_people, set_timer, manage_list, get_statistics, get_logbook, tts_announce | 14 |
| v3.0.0 | Context injection, dynamic temperature, anti-repetition (no new tools) | 14 |
| v3.1.0 | Switched to Claude Sonnet 4 (Anthropic API) | 14 |
| v3.2.0 | + 19 tools (eval_template, bulk_control, save_memory, read_memory, get_device, calendars, helpers, config readers, etc.) + dual model Haiku/Sonnet + smart memory + prompt caching + config safety | 33 |

## Intelligence Evolution

| Version | Feature |
|---------|---------|
| v2.6.0 | Personality rewrite — warm, curious, conversational |
| v2.7.0 | Discovery tools — search, history, areas |
| v2.8.0 | Family tools — notifications, timers, lists, TTS |
| v3.0.0 | Context injection, dynamic temperature, anti-repetition, autonomous thinking, emotional awareness |
| v3.1.0 | Claude Sonnet 4 replaces GPT-5.4 Mini — reliable tool calling, better Hebrew |
| v3.2.0 | 33 tools, dual model (Haiku fast / Sonnet smart), smart memory (read_memory on demand), prompt caching, config API safety (backup + read validation) |
| v3.3.0 | ha_config_api → HA Config Store API (planned) |
