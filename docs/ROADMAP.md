# Jane — Implementation Roadmap

Prioritized list of features to implement, ordered by impact.
Each item is planned and approved before implementation begins.

---

## Completed

### 1. Memory System ✅
LLM-managed markdown memory. After each conversation, GPT decides what to remember.
7 memory files: personal, family, habits, actions, home map, corrections, routines.

See [MEMORY_ARCHITECTURE.md](MEMORY_ARCHITECTURE.md) for full design.

---

### 2. HA Conversation Agent ✅
Replaced the Docker add-on + custom card approach with a native HA custom_component.
Jane registers as a conversation agent in the Assist pipeline.

**What changed:**
- No more Docker add-on, FastAPI server, or custom Lovelace card
- Jane is a `custom_component` (`jane_conversation`) that integrates directly with HA
- Works with Assist button in Companion App (iPhone/Android), Safari, Chrome
- Same pipeline will work with Atom EchoS3R satellites via Wyoming Protocol
- User identification automatic from HA logged-in user (no hardcoded `user: yair`)
- HA service calls via native `hass.services.async_call()` instead of REST API

**Pipeline:** Whisper STT (HACS) → Jane conversation agent → OpenAI TTS (HACS)

---

### 3. Multi-turn Conversations ✅
Session history maintained in RAM. Jane understands context within a conversation.

"Turn on the bathroom light" → "Turn it off" → Jane knows what "it" is.

Last 10 turns kept per session. History cleared on session end or HA restart.
Long-term context preserved via memory files (actions.md, personal memory).

---

### 4. Auto User Identification ✅
User identity resolved from HA's logged-in user (`hass.auth.async_get_user`).
No hardcoded user names. Each HA user gets their own memory file automatically.

---

## Next Up

### 5. Tavily Web Search
Real-time information access — weather, traffic, news, business hours, exchange rates.

**Why:** Jane should answer any question, not just smart home commands. Tavily returns clean text optimized for LLMs, no HTML parsing needed.

**Scope:**
- Add Tavily API key to config flow
- Add `search_web(query)` tool for GPT
- GPT decides autonomously when to search
- Free tier: 1,000 searches/month

**Examples:**
- "מה מזג האוויר מחר?"
- "כמה זמן נסיעה לירושלים?"
- "מה קורה בחדשות?"
- "מה שער הדולר?"

---

### 6. Concise Responses
Simple commands get short answers. Conversations get full responses.

**Why:** "הדליקי אור בסלון" → "בוצע" is better than "הדלקתי את האור בתקרת הסלון בשבילך".

**Scope:**
- Update SYSTEM_PROMPT with response length rules
- Simple ha_service → one word confirmation
- Questions/conversations → full natural response
- Errors → brief explanation

---

### 7. Night Mode
23:00–07:00: quieter behavior.

**Why:** Don't wake the house with long responses at midnight.

**Scope:**
- Inject current time into GPT context
- Shorter responses during night hours
- No non-urgent proactive alerts
- Urgent alerts still go through

---

### 8. Continue Conversation
After Jane responds, keep listening for a few seconds without requiring wake word again.

**Why:** Natural multi-turn voice interaction with Atom satellite.

**Scope:**
- Set `continue_conversation=True` in ConversationResult
- Configurable timeout (default 5 seconds)
- Works with both Assist button and Wyoming satellites

---

## Data & Infrastructure

### 9. Firebase Memory Backup
Cloud backup for memory files — write-through pattern.

**Why:** If the Pi dies, SD card corrupts, or HA reinstalls — Jane's memory survives.

**Scope:**
- Firestore document per memory file
- Write-through: every local save also writes to Firebase
- On startup: if local files missing → restore from Firebase
- `actions.md` and `home.md` NOT backed up (ephemeral/regenerable)
- Service account key in integration config
- Free tier: 1GB storage, 50K reads/day

See [MEMORY_ARCHITECTURE.md](MEMORY_ARCHITECTURE.md) for full design.

---

### 10. Command History Log
Full log of all voice commands and Jane's responses.

**Why:** Debugging, auditing, and understanding usage patterns.

**Scope:**
- Log each interaction: timestamp, user, input text, response, action taken
- Permanent audit trail (separate from rolling `actions.md`)

---

## Future (Post-V2)

These require hardware (Atom EchoS3R) or major new integrations:

- **Wyoming Protocol + Atom EchoS3R** — satellite audio, same Assist pipeline
- **Wake word** — "Hey Jane" via Porcupine on ESP32
- **Multi-room satellites** — independent audio per room
- **MCP Server** — give GPT direct HA tool access via function calling
- **ElevenLabs TTS** — more natural Hebrew voice
- **Google Calendar** — "מה יש לנו השבוע?"
- **Proactive alerts** — open window + rain, gas left on
- **Voice-based user ID** — automatic speaker recognition
- **Permission matrix** — admin vs child access levels
