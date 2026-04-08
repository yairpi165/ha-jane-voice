# Jane Memory Architecture

## Overview

Jane uses an LLM-managed memory system. GPT-4o Mini reads, consolidates, and rewrites concise Markdown files. No code-side deduplication, confidence scoring, or schema validation — the LLM handles all memory management in natural language.

Memory content is stored in **English** for LLM precision. Conversations with users remain in **Hebrew**.

---

## Memory Files

```
memory/
├── users/
│   └── yair.md          # Personal: preferences, facts, emotional context
├── family.md            # Household rules, events, shared preferences
├── habits.md            # Recurring behavioral patterns
├── actions.md           # Rolling 24h action log
├── home.md              # Home layout — rooms, devices, entity IDs (static)
├── corrections.md       # Learned mistakes — what Jane got wrong and how to fix
└── routines.md          # User-defined command sequences ("goodnight", "good morning")
```

### File Purposes

| File | Updates | Managed by |
|------|---------|------------|
| `users/{name}.md` | After conversations with new personal info | GPT extraction |
| `family.md` | When household rules/events are mentioned | GPT extraction |
| `habits.md` | When GPT detects recurring patterns | GPT extraction |
| `actions.md` | After every action/conversation | Code (append + prune) |
| `home.md` | On startup from HA entities; rarely changes | Code (auto-generated) |
| `corrections.md` | When user corrects Jane | GPT extraction |
| `routines.md` | When user defines/modifies a routine | GPT extraction |

---

## Storage

### Local (Primary)
- **HA (production):** `config/jane_memory/` (inside HA config directory, persists across restarts and updates)

### Firebase (Backup) — Phase 2
```
jane-memory/
├── users/{name}: { content: "...", updated: timestamp }
├── family:       { content: "...", updated: timestamp }
├── habits:       { content: "...", updated: timestamp }
├── corrections:  { content: "...", updated: timestamp }
└── routines:     { content: "...", updated: timestamp }
```

Write-through: every local save also writes to Firebase. On startup, if local files are missing, restore from Firebase.

Note: `actions.md` and `home.md` are NOT backed up to Firebase — they are ephemeral/regenerable.

---

## Conversation Flow

```
┌──────────────────────────────────────────────────────────────────┐
│   User speaks (Hebrew) via Assist / Satellite / Voice Card       │
└──────────────────────────┬───────────────────────────────────────┘
                           ▼
              HA Assist Pipeline: Whisper STT → text
                           │
                           ▼
         Jane conversation agent (custom_component)
                           │
                           ▼
              ┌────────────────────────────────────┐
              │        Load memory (English)        │
              │                                     │
              │  users/{name}.md  — who is this?    │
              │  family.md        — household rules  │
              │  habits.md        — patterns         │
              │  actions.md       — recent actions   │
              │  home.md          — device map       │
              │  corrections.md   — past mistakes    │
              │  routines.md      — "goodnight" etc  │
              └────────────────┬───────────────────┘
                               ▼
              ┌────────────────────────────────────┐
              │           GPT-4o Mini               │
              │                                     │
              │  System: personality (Hebrew)        │
              │  System: memory context (English)    │
              │  System: session history              │
              │  System: current device states (HA)  │
              │  User: Hebrew text                   │
              └────────────────┬───────────────────┘
                               │
                    ┌──────────┴──────────┐
                    ▼                     ▼
              Response (HE)        [Background]
              returned to HA       Memory extraction
                    │              GPT analyzes conversation
                    ▼              Updates relevant MD files
              HA Pipeline TTS      Appends to actions.md
                    │                     │
                    ▼                     ▼
              User hears           Save to config/jane_memory/
              response             + Firebase backup (Phase 2)
```

---

## Memory File Examples

### users/yair.md
```markdown
# Yair — Personal Memory

## Preferences
- Prefers dim lighting (40%) in the living room during evenings
- Likes AC at 23°C (updated from 22°C)
- Prefers concise responses — "done" over long confirmations

## Facts
- Works from home
- Admin user — full access to all devices
- Morning routine: heating on at 07:00, then coffee

## Emotional Context
- Last interaction mood: neutral
```

### family.md
```markdown
# Family Memory

## Members
- Yair (admin, adult)
- [Other members added as identified]

## Household Rules
- No lights in kids rooms after 21:00
- Do not unlock front door without confirmation

## Events
- [Child] birthday: April 15 (recurring)

## Shared Preferences
- Shabbat mode: Friday 18:00 — dim all lights to 30%
```

### habits.md
```markdown
# Behavioral Patterns

## Morning
- Yair turns on living room heating on weekdays around 07:00 (frequent)

## Evening
- Yair dims living room lights to 40% most evenings (frequent)
- Family turns off all lights by 23:00 (common)
```

### actions.md
```markdown
# Recent Actions (rolling 24h)

- 2026-04-06 07:00 — Turned on living room heating (Yair)
- 2026-04-06 08:30 — Weather query: 18°C, partly cloudy (Yair)
- 2026-04-06 20:00 — Dimmed living room lights to 40% (Yair)
- 2026-04-06 20:15 — Turned off kids room light (Yair)
```

### home.md
```markdown
# Home Layout

## Living Room
- Ceiling light (light.switcher_light_3708)
- LED strip (light.switcher_light_3f25)
- Wall light (light.switcher_light_3708)
- AC (climate.yair_s_device)
- Roller shutter (cover.switcher_runner_7995)
- Sony TV (media_player.sony_kd_65x85j)

## Kitchen
- Main light (light.switcher_light_2fa2_light_2)
- Island light (light.switcher_light_4158)
- Counter light (light.switcher_light_2fa2_light_1)

## Hallway
- Entrance light (light.switcher_light_4026)
- Hallway light (light.switcher_light_3cc5)
- Corridor light (light.switcher_light_3bba)

## Master Bedroom
- Bedroom light (light.switcher_light_3d9b)
- Heater (climate.zhimi_zb1a_4b1b_heater)

## Kids Room
- Night light (light.mnvrt_lylh)

## Other
- Robot vacuum (vacuum.x40_ultra)
- Electric heater switch (switch.dvd_khshml)
- Tami4 water bar (button.myny_br_boil_water)
```

### corrections.md
```markdown
# Corrections — What Jane Learned

## Entity Confusion
- "the light" in living room context = ceiling light, not LED strip
- "heater" = Xiaomi heater (climate.zhimi_zb1a_4b1b_heater), not AC heat mode

## Command Interpretation
- "turn off everything" = all lights only, NOT AC or TV
- "it's cold" = turn on heater, don't just report temperature

## Response Style
- Yair prefers "done" over "I've turned on the living room ceiling light for you"
```

### routines.md
```markdown
# Routines

## לילה טוב (Goodnight)
1. Turn off all lights
2. Lock front door
3. Close all shutters
4. Set AC to 22°C

## בוקר טוב (Good Morning)
1. Open living room shutters
2. Turn on heating to 23°C
3. Give brief weather summary

## יוצא מהבית (Leaving Home)
1. Turn off all lights
2. Turn off AC and heater
3. Lock front door
```

---

## Memory Extraction

After each conversation, a background GPT call decides what to remember.

### When extraction runs
- After every conversation **except**:
  - Silence (no speech detected)
  - Simple device commands ("turn on light" → "done") — unless a correction occurs
  - User said "don't remember this" (silent mode)

### How extraction works
1. GPT receives: all current memory files + the conversation that just happened
2. GPT decides: does anything need updating?
3. If yes: GPT rewrites the affected file(s) entirely — merging new info with existing
4. If no: returns null for unchanged files

### What GPT updates vs what code updates

| File | Updated by |
|------|-----------|
| `users/{name}.md` | GPT — rewrites when personal info changes |
| `family.md` | GPT — rewrites when household info changes |
| `habits.md` | GPT — rewrites when patterns are detected |
| `corrections.md` | GPT — rewrites when Jane is corrected |
| `routines.md` | GPT — rewrites when routines are defined/changed |
| `actions.md` | **Code** — appends after each action, prunes entries >24h |
| `home.md` | **GPT** — generated from HA entities on first run, organized by room |

### Extraction prompt behavior
- Merge new information with existing memory
- Resolve contradictions (new info wins)
- Remove stale or irrelevant information
- Keep each file concise (max ~50 lines)
- Detect corrections: if user corrected Jane, update corrections.md
- Detect routine definitions: if user defined a sequence, update routines.md
- Detect habits: if action matches an emerging pattern, update habits.md

---

## Implementation

Jane runs as a **custom_component** (`jane_conversation`) inside Home Assistant.
Memory files are stored in `config/jane_memory/` on the Pi.

### Modules (in `custom_components/jane_conversation/`)

**conversation.py** — Conversation entity + session history
```
JaneConversationEntity
    - Registers as HA conversation agent
    - Maintains per-session history (last 10 turns in RAM)
    - Resolves user name from HA auth
    - Calls brain.think() with tool calling
    - Triggers memory extraction in background
    - Returns response for TTS
```

**brain.py** — LLM integration with autonomous tool calling
```
think(client, user_text, user_name, hass, history, tavily_api_key)
    - Loads memory context + conversation history
    - Sends to GPT-4o Mini with tools (get_entity_state, call_ha_service, search_web)
    - GPT decides what tools to call autonomously
    - Executes tools, feeds results back to GPT
    - Returns final response text (no more JSON parsing)
```

**tools.py** — Tool definitions + execution handlers
```
get_tools(tavily_api_key) → list
    Returns available tools based on config.

execute_tool(hass, tool_name, arguments, tavily_key) → str
    Routes to handler: HA states, HA services, or Tavily web search.
```

**memory.py** — Memory management
```
load_all_memory(user_name) → str
    Combines all 7 files into context block for GPT.

process_memory(client, user_name, user_text, jane_response, action)
    Background GPT call to analyze conversation and update memory files.

append_action(user_name, description)
    Appends timestamped line to actions.md, prunes >24h entries.

rebuild_home_map(client, hass)
    GPT-organized home layout from HA entities (first run only).
```

### User identification
Automatic from HA's logged-in user via `hass.auth.async_get_user()`.
No hardcoded user names. Each HA user gets their own memory file.

---

## Silent Mode

User can say (in Hebrew):
- "אל תזכרי את השיחה הזאת"
- "מצב שקט"

When detected, memory extraction is skipped entirely. Actions are still logged (actions.md), but no personal/family/habit memory is updated.

---

## Initial State

First-ever startup:
1. `rebuild_home_map()` runs → creates `home.md` from HA entities
2. All other files don't exist → `load_all_memory()` returns "No prior memory" for each section
3. GPT treats this as a fresh installation, responds normally
4. Background extraction creates first versions of relevant files
5. Next conversation — memory is loaded and used

No seed files or bootstrap needed. The system is self-initializing.

---

## Home Map Generation

`home.md` is generated by GPT on first startup. The process:

1. Fetch all entities from HA via `hass.states.async_all()`
2. Send raw entity list to GPT with a prompt: "organize by room"
3. GPT writes a clean, concise home layout grouped by room (not domain)
4. Includes entity_id for each device (GPT needs this for ha_service actions)
5. Only runs once — if `home.md` already exists, skips

Can be regenerated via the `rebuild_home_map()` function if devices change.

---

## Action Log Management

`actions.md` is append-only, managed by code:

### On every interaction
```python
append_action("yair", "Turned on living room ceiling light")
```

### Pruning
On each append, remove lines older than 24 hours. Keep the file small (~20-50 lines max).

### Why code, not GPT
- Actions are factual and timestamped — no interpretation needed
- Appending a line is instant; no GPT call required
- Pruning is a simple timestamp comparison

---

## Firebase Integration (Phase 2)

### Pattern: Write-Through Cache
```
Save:
    1. Write to local file (fast, primary)
    2. Write to Firebase (background, backup)

Load:
    1. Read local file
    2. If missing → restore from Firebase → write locally
```

### What gets backed up
| File | Firebase backup |
|------|----------------|
| `users/*.md` | Yes |
| `family.md` | Yes |
| `habits.md` | Yes |
| `corrections.md` | Yes |
| `routines.md` | Yes |
| `actions.md` | No (ephemeral, regenerated) |
| `home.md` | No (regenerated from HA on startup) |

### Requirements
- Firebase project with Firestore
- Service account key in addon options
- `firebase-admin` in requirements.txt

### Why Firestore
- Simple document storage fits our use case (one doc per memory file)
- Free tier: 1GB storage, 50K reads/day — more than enough
- Auto-scales, no maintenance
