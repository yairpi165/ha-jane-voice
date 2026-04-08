# Jane — Implementation Roadmap

Prioritized list of features to implement, ordered by impact.
Each item is planned and approved before implementation begins.

---

## Completed

### 1. Voice Pipeline + HA Control ✅
Basic voice conversation in Hebrew. GPT-4o Mini processes commands, controls HA devices via `hass.services.async_call()`.

---

### 2. HA Conversation Agent ✅
Jane is a native HA custom_component that integrates with the Assist pipeline.
Works with Assist button in Companion App, Safari, Chrome, and future satellites.
User identification automatic from HA auth.

---

### 3. Memory System ✅
LLM-managed markdown memory. 7 files: personal, family, habits, actions, home map, corrections, routines.
GPT reads, consolidates, and rewrites — no code-side dedup.

See [MEMORY_ARCHITECTURE.md](MEMORY_ARCHITECTURE.md) for full design.

---

### 4. Multi-turn Conversations ✅
Session history in RAM. Jane understands context within a conversation ("turn it off" after "turn on the light"). Last 10 turns per session.

---

## In Progress

### 5. Tool Calling Architecture 🚧
Major refactor: replace hardcoded JSON response parsing with OpenAI function calling.
GPT gets tools and decides autonomously what to use.

**This is the foundation for everything below.**

See [TOOL_CALLING_ARCHITECTURE.md](TOOL_CALLING_ARCHITECTURE.md) for full design.

**Phase 1 tools (build now):**

| Tool | What it does |
|------|-------------|
| `get_entity_state` | Read any HA entity state |
| `call_ha_service` | Call any HA service (control devices, get forecasts) |
| `search_web` | Search the internet via Tavily |

**Phase 2 tools (build next):**

| Tool | What it does |
|------|-------------|
| `create_automation` | Create HA automations from natural language |
| `list_automations` | See existing automations |
| `update_automation` | Modify automations |
| `delete_automation` | Remove automations |
| `create_scene` | Create HA scenes |
| `create_script` | Create HA scripts |

---

### 6. Custom Wake Word 🚧
Training "Hey Jane" microWakeWord model for Voice Satellite Card.
Using [microWakeWord Trainer for Apple Silicon](https://github.com/TaterTotterson/microWakeWord-Trainer-AppleSilicon).
Training in progress.

---

## Next Up

### 7. Concise Responses
Simple commands get short answers. Conversations get full responses.
Handled naturally by updated system prompt in tool calling architecture.

---

### 8. Night Mode
23:00–07:00: shorter responses, no non-urgent alerts.
Inject current time into GPT context.

---

### 9. Continue Conversation
After Jane responds, keep listening for a few seconds without requiring wake word.
Set `continue_conversation=True` in ConversationResult.

---

## Data & Infrastructure

### 10. Firebase Memory Backup
Cloud backup for memory files — write-through pattern.
If the Pi dies, Jane's memory survives.

See [MEMORY_ARCHITECTURE.md](MEMORY_ARCHITECTURE.md) for design.

---

### 11. Command History Log
Permanent audit trail of all voice commands and responses.
Separate from rolling `actions.md`.

---

## User Recognition

### 12. Voice Recognition (Speaker ID)
Identify who is speaking without asking — from voice alone.
Azure Speaker Recognition API or local model.
Critical for satellite use where there's no HA login.

---

### 13. Face Recognition
Identify who is in the room via camera + Frigate.
Presence-based context for proactive behavior.

---

## Future (Post-V2)

- **Wyoming Protocol + Atom EchoS3R** — satellite audio, same Assist pipeline
- **Multi-room satellites** — independent audio per room
- **ElevenLabs TTS** — more natural Hebrew voice
- **Google Calendar** — "מה יש לנו השבוע?"
- **Proactive alerts** — open window + rain, gas left on
- **Permission matrix** — admin vs child access levels
- **Proactive suggestions** — "I noticed you dim lights every evening — want me to create an automation?"
