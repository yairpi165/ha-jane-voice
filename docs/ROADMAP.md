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

### 19. Phase 4 Tools — Family Life (v2.8.0, code ready)
- `send_notification` — push notifications to family phones
- `check_people` — who's home, where are they
- `set_timer` — countdown timer with notification on expiry
- `manage_list` — shopping/todo list management
- `get_statistics` — sensor min/max/average over time
- `get_logbook` — recent events and state changes
- `tts_announce` — broadcast messages through speakers

---

## In Progress — v3.0.0: Intelligence Layer

### 20. Context Injection
Every conversation starts with real-time awareness — injected automatically before the user's message:
- **Weather**: current conditions + forecast
- **People**: who's home, who's away
- **Home state**: what's on, what's off, anything unusual
- **Calendar**: today's events and upcoming items

This lets Jane be naturally aware: "בוקר טוב! היום חם, 34 מעלות. אפרת כבר יצאה."
No tools needed — the context is pre-loaded.

**Implementation**: In `brain.py`, before the GPT call:
1. Read weather entity state
2. Read all person entities
3. Read key device states (lights, AC, covers)
4. Read today's calendar events
5. Inject as system message: "Current context: ..."

### 21. save_memory Tool
Explicit memory management during conversation — Jane decides what to remember, when.
Current system: background GPT call after conversation extracts memories.
New: GPT calls `save_memory` tool mid-conversation when it learns something important.

Both systems coexist — tool for intentional saves, background extraction as safety net.

### 22. Higher Thinking Limits
- `max_completion_tokens`: 1000 → 2000 (more room for complex reasoning)
- `MAX_TOOL_ITERATIONS`: 5 → 10 (more steps for multi-action tasks)

Enables complex requests: "תסדרי לי את הבית לשבת" (multiple lights, AC, shutters, scenes).

---

## Planned — v3.1.0: Personality Depth

### 23. Per-User Behavior
Jane adapts personality per family member:
- **Yair (admin)**: direct, tech-aware, brief confirmations
- **Kids**: gentler, explains more, restricted late-night access
- **Guests**: polite but limited device control

Implementation: user_name-based prompt injection + permission matrix in memory.

### 24. Routine Execution
Named multi-step routines stored in routines.md:
- "לילה טוב" → turn off lights, close shutters, set AC to 24, lock door
- "יוצא מהבית" → turn off everything, lock up
- "בוקר טוב" → open shutters, report weather, turn on heating

Jane chains multiple tool calls automatically from routine definitions.

### 25. Proactive Behavior (Background Loop)
Jane monitors the home and speaks up when relevant:
- "המזגן דולק כבר 5 שעות, לכבות?"
- "אפרת עזבה את הבית"
- "חלון פתוח ויורד גשם"
- "כבר 23:00 והאורות בחדר ילדים עדיין דולקים"

Implementation: Background async loop checking entity states every N minutes.
Triggers notification or TTS when conditions are met.

---

## Future — v4.0.0

### 26. Voice Recognition (Speaker ID)
Identify who is speaking from voice alone — no "who is this?" needed.
Azure Speaker Recognition API or local model.

### 27. Face Recognition
Identify who is in the room via camera + Frigate.
Presence-based context for proactive behavior.

### 28. ElevenLabs TTS
More natural Hebrew voice. Multilingual v2 model with Hebrew support.

### 29. Multi-Room Satellites
Wyoming Protocol + ESP32 devices — independent audio per room.
Jane knows which room you're in and responds on the right speaker.

### 30. Learning & Suggestions
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
| v3.0.0 | + save_memory, (context injection is not a tool) | 15 |
