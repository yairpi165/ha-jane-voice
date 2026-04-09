# Jane — Implementation Roadmap

Prioritized list of features to implement, ordered by impact.
Each item is planned and approved before implementation begins.

---

## Completed

### 1. Voice Pipeline + HA Control ✅
Basic voice conversation in Hebrew. GPT-4o Mini processes commands, controls HA devices.

---

### 2. HA Conversation Agent ✅
Jane is a native custom_component integrated with the Assist pipeline.
Works with Assist button, Companion App, Safari, Chrome, and future satellites.

---

### 3. Memory System ✅
LLM-managed markdown memory. 7 files: personal, family, habits, actions, home map, corrections, routines.

See [MEMORY_ARCHITECTURE.md](MEMORY_ARCHITECTURE.md).

---

### 4. Multi-turn Conversations ✅
Session history in RAM. Jane understands context within a conversation. Last 10 turns per session.

---

### 5. Tool Calling Architecture ✅ (code ready, deploying)
GPT uses OpenAI function calling to autonomously decide what tools to use.
Replaces hardcoded JSON response parsing.

**Phase 1 tools (built):**
- `get_entity_state` — read any HA entity
- `call_ha_service` — control devices, get forecasts, trigger scripts
- `search_web` — Tavily web search (optional)

See [TOOL_CALLING_ARCHITECTURE.md](TOOL_CALLING_ARCHITECTURE.md).

---

### 6. Custom Wake Word ✅
"Hey Jane" microWakeWord model trained on Apple Silicon M4 Pro.
62KB tflite model, 99.7% accuracy, 100% precision.
Used with Voice Satellite Card integration.

---

### 7. OpenAI TTS ✅
Replaced HA Cloud TTS with OpenAI TTS (voice: nova) for more natural Hebrew speech.

---

### 8. Concise Responses ✅
Simple commands get short answers ("done"). Conversations get full responses.
In the system prompt. Tested and working.

---

### 9. Night Mode ✅
23:00–07:00: shorter responses, whisper-friendly.
Current time injected into GPT context. Night mode instructions in system prompt.

---

### 10. Continue Conversation ✅
After Jane responds, keep listening when she asks a question (ends with ?).
Uses `continue_conversation` flag in ConversationResult.

---

### 11. Phase 2 Tools — Create & Manage ✅
Jane becomes a home manager, not just a remote control.
One generic tool: `ha_config_api(resource, operation, config, item_id)`.
GPT autonomously creates/updates/deletes automations, scenes, and scripts.
Writes to YAML config files + reloads the domain.

---

## Data & Infrastructure

### 12. Firebase Memory Backup ✅
Write-through Firestore backup. Save locally → push to Firebase in background.
On startup, missing files restored from cloud. Optional — works without Firebase too.

---

### 13. Command History Log ✅
Permanent `history.log` — every command + response with timestamp. Never pruned.

---

## User Recognition

### 14. Voice Recognition (Speaker ID)
Identify who is speaking without asking — from voice alone.
Azure Speaker Recognition API or local model.

---

### 15. Face Recognition
Identify who is in the room via camera + Frigate.
Presence-based context for proactive behavior.

---

## Future (Post-V2)

- **Wyoming Protocol + Atom EchoS3R** — satellite audio, same Assist pipeline
- **Multi-room satellites** — independent audio per room
- **ElevenLabs TTS** — even more natural Hebrew voice
- **Google Calendar** — "what's on this week?"
- **Proactive alerts** — open window + rain, gas left on
- **Permission matrix** — admin vs child access levels
- **Proactive suggestions** — "I noticed you dim lights every evening — want me to create an automation?"
- **Latency optimization** — streaming STT, faster models, local TTS
