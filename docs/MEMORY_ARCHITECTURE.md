# Jane Memory Architecture

## Overview

Jane uses an LLM-managed memory system. GPT-5.4 Mini reads, consolidates, and rewrites concise Markdown files. No code-side deduplication or schema validation — the LLM handles all memory management in natural language.

Memory content is stored in **English** for LLM precision. Conversations with users remain in **Hebrew**.

---

## Memory Files

```
jane_memory/
├── users/
│   └── {name}.md        # Personal: preferences, facts, personality
├── family.md            # Household rules, members, shared preferences
├── habits.md            # Recurring behavioral patterns
├── actions.md           # Rolling 24h action log
├── home.md              # Home layout — rooms, devices, entity IDs
├── corrections.md       # Learned mistakes — what Jane got wrong
├── routines.md          # User-defined command sequences
└── history.log          # Permanent conversation log (never pruned)
```

### File Purposes

| File | Updates | Managed by |
|------|---------|------------|
| `users/{name}.md` | After conversations with new personal info | GPT extraction |
| `family.md` | When household rules/members are mentioned | GPT extraction |
| `habits.md` | When GPT detects recurring patterns | GPT extraction |
| `actions.md` | After every action/conversation | Code (append + prune 24h) |
| `home.md` | On startup from HA entities; manually curated | Code (auto-generated) / Manual |
| `corrections.md` | When user corrects Jane | GPT extraction |
| `routines.md` | When user defines/modifies a routine | GPT extraction |
| `history.log` | After every conversation | Code (append only, never pruned) |

---

## Storage

### Local (Primary)
- **Path**: `config/jane_memory/` (inside HA config directory)
- Persists across restarts and updates

### Firebase (Backup)
Write-through to Firestore REST API. Every local save also writes to Firebase in background.

```
Firestore collection: jane-memory
├── users_{name}: { content: "...", updated: timestamp }
├── family:       { content: "...", updated: timestamp }
├── habits:       { content: "...", updated: timestamp }
├── corrections:  { content: "...", updated: timestamp }
└── routines:     { content: "...", updated: timestamp }
```

**On startup:**
1. If local files missing → restore from Firebase
2. If local files exist → sync to Firebase (initial backup)

**Not backed up:** `actions.md` (ephemeral), `home.md` (regenerable), `history.log` (append-only, too large)

**Implementation:** `firebase.py` uses `google-auth` for credentials + `aiohttp` for HTTP. Token refresh runs in executor (blocking).

---

## Memory Extraction

After each conversation, a background GPT call decides what to remember.

### When extraction runs
After every conversation **except**:
- Whisper hallucinations (filtered before processing)
- User said "אל תזכרי" or "מצב שקט" (silent mode)

### How extraction works
1. GPT receives: all current memory files + the conversation
2. GPT decides: does anything need updating?
3. If yes: GPT rewrites affected file(s) entirely — merging new with existing
4. New information wins over contradictions
5. Each file stays concise (max ~50 lines)

### Extraction priorities
**Aggressive about saving:**
- Family members: names, ages, relationships, preferences
- Personal details: likes/dislikes, routines, job, personality
- Corrections: if user corrected Jane
- Patterns: recurring requests, time-based habits
- Routines: multi-step sequences

**Does NOT save:**
- One-time commands: "turn on the light"
- General questions: "what time is it?"
- Pleasantries: "thank you"

---

## Planned: save_memory Tool (v3.0.0)

Currently memory is only extracted after conversations (background process).
Adding an explicit `save_memory` tool lets Jane decide mid-conversation what to remember.

```
save_memory(category="family", content="Maor is 8, loves soccer and Minecraft")
```

**Why both systems?**
- Tool: intentional saves for important information
- Background extraction: safety net for things GPT didn't explicitly save

---

## Conversation Flow

```
┌──────────────────────────────────────────────┐
│   User speaks (Hebrew) via Voice Pipeline     │
└──────────────────────┬───────────────────────┘
                       ▼
         Whisper STT → text
                       │
        ┌──────────────┤ Hallucination filter
        │ phantom?     │
        │    ↓ yes     │ no ↓
        │  ignore      │
        └──────────────┤
                       ▼
         Load memory (7 files → English context)
                       │
                       ▼
         GPT-5.4 Mini (function calling loop)
           │                           │
           ▼                           ▼
    Tool calls (0–10)           Final response (Hebrew)
           │                           │
           ▼                           ▼
    Execute via tools.py        Return to HA pipeline
                                       │
                               ┌───────┤
                               │       ▼
                               │  TTS → Speaker
                               │
                               ▼ (background, parallel)
                        ┌──────────────┐
                        │ append_action │ → actions.md
                        │ append_history│ → history.log
                        │ process_memory│ → update MD files
                        │              │ → Firebase backup
                        └──────────────┘
```

---

## Planned: Context Injection (v3.0.0)

Before GPT call, automatically inject real-time context:

```python
# In brain.py
context_parts = []

# Weather
weather = hass.states.get("weather.forecast_home")
if weather:
    temp = weather.attributes.get("temperature", "?")
    context_parts.append(f"Weather: {weather.state}, {temp}°C")

# People
for person in hass.states.async_all("person"):
    name = person.attributes.get("friendly_name", "?")
    context_parts.append(f"{name}: {person.state}")

# Active devices
active = []
for state in hass.states.async_all():
    if state.domain in ("light", "climate", "media_player") and state.state not in ("off", "unavailable"):
        active.append(state.attributes.get("friendly_name", state.entity_id))
if active:
    context_parts.append(f"Active: {', '.join(active)}")

messages.append({"role": "system", "content": "Current context:\n" + "\n".join(context_parts)})
```

This gives Jane ambient awareness without tool calls. She can greet with context:
"בוקר טוב! היום 28 מעלות ושמשי. אפרת כבר יצאה."

---

## Silent Mode

User can say:
- "אל תזכרי את השיחה הזאת"
- "מצב שקט"

When detected: memory extraction is skipped. Actions and history are still logged.

---

## Home Map

`home.md` is auto-generated by GPT on first startup, then manually curated.

**Generation process:**
1. Fetch entities from relevant domains (light, climate, cover, media_player, fan, vacuum, water_heater)
2. Filter out internal/config entities
3. GPT organizes by room

**Currently includes:** All rooms, Tami4 water bar, PlayStation, HomePod mini, dishwasher, robot vacuum, etc.

Can be regenerated via `rebuild_home_map()` if devices change (only runs if home.md is empty/missing).
