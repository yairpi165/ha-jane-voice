# Jane — Implementation Roadmap

Prioritized list of features to implement, ordered by impact.
Each item is planned and approved before implementation begins.

---

## High Impact

### 1. Memory System ✅ Implemented
LLM-managed markdown memory. After each conversation, GPT decides what to remember.
7 memory files: personal, family, habits, actions, home map, corrections, routines.

See [MEMORY_ARCHITECTURE.md](MEMORY_ARCHITECTURE.md) for full design.

**Files:** `memory.py`, `brain.py` (context injection), `web_api.py` (background tasks)

---

### 2. MCP Server
Replace the REST API (`ha_client.py`) with Home Assistant's native MCP Server integration.

**Why:** MCP gives GPT direct tool access to HA — call services, query states, list entities — without us manually building API wrappers. Smarter, more flexible device control.

**Scope:**
- Replace `ha_client.py` REST calls with MCP tool calls
- GPT gets HA tools natively via function calling
- Remove manual entity fetching — GPT queries what it needs
- `home.md` still provides static context, MCP provides live interaction

**Endpoint:** `http://homeassistant.local:8123/api/mcp` (available since HA 2025.2)

---

### 3. Tavily Web Search
Real-time information access — weather, traffic, news, business hours, exchange rates.

**Why:** Jane should answer any question, not just smart home commands. Tavily returns clean text optimized for LLMs, no HTML parsing needed.

**Scope:**
- Add Tavily API key to config
- Add `search_web(query)` tool for GPT
- GPT decides autonomously when to search
- Free tier: 1,000 searches/month

**Examples:**
- "מה מזג האוויר מחר?"
- "כמה זמן נסיעה לירושלים?"
- "מה קורה בחדשות?"
- "מה שער הדולר?"

---

## UX Improvements

### 4. Concise Responses
Simple commands get short answers. Conversations get full responses.

**Why:** "הדליקי אור בסלון" → "בוצע" is better than "הדלקתי את האור בתקרת הסלון בשבילך".

**Scope:**
- Update SYSTEM_PROMPT with response length rules
- Simple ha_service → one word confirmation
- Questions/conversations → full natural response
- Errors → brief explanation

---

### 5. User Identification
"Who's speaking?" at session start. Different permissions per user.

**Why:** Kids shouldn't unlock the front door. Each user gets personalized memory and responses.

**Scope:**
- Phase 1: `user` config in dashboard card (already implemented in memory system)
- Phase 2: Jane asks "מי מדבר?" if user is unknown
- Permission matrix: admin (full access) vs child (restricted)
- Communication style adapts: admin → concise, child → warm and simple

---

### 6. Night Mode
23:00–07:00: quieter behavior.

**Why:** Don't wake the house with long responses at midnight.

**Scope:**
- Detect time in SYSTEM_PROMPT context or via code
- Shorter responses during night hours
- Lower TTS volume (if supported)
- No non-urgent proactive alerts
- Urgent alerts (gas, open window in rain) still go through

---

## Infrastructure

### 7. Command History Log
Full log of all voice commands and Jane's responses.

**Why:** Debugging, auditing, and understanding usage patterns.

**Scope:**
- Log each interaction: timestamp, user, input text, response, action taken
- Store as append-only file or simple SQLite
- Different from `actions.md` (which is rolling 24h for GPT context)
- This is a permanent audit trail

---

### 8. Health Check Endpoint
Simple HTTP endpoint to verify Jane is alive.

**Why:** HA can monitor addon health. Watchdog can ping it.

**Scope:**
- `GET /health` → `{"status": "ok", "uptime": "...", "memory_files": 7}`
- Add to `web_api.py`
- Can be monitored by HA's RESTful sensor

---

### 9. Watchdog
Auto-restart on failure.

**Status:** Already enabled in addon config (`watchdog: true`).
HAOS Supervisor automatically restarts the addon if it crashes.

No additional work needed unless we want custom health-check-based watchdog logic (would use #8).

---

## Future (Post-V2)

These require hardware (Atom EchoS3R) or major new integrations:

- **Wyoming Protocol** — satellite audio streaming
- **Wake word** — "Hey Jane" via Porcupine
- **Multi-room satellites** — independent audio per room
- **ElevenLabs TTS** — more natural Hebrew voice
- **Google Calendar** — "מה יש לנו השבוע?"
- **Proactive alerts** — open window + rain, gas left on
- **Voice-based user ID** — automatic speaker recognition
- **Firebase backup** — cloud backup for memory files
