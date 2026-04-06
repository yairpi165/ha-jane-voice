# Jane — AI-Powered Smart Home Assistant
## Product Requirements Document (PRD) & Architecture Specification

**Version:** 1.0  
**Date:** March 2026  
**Status:** 🟢 Active Development  
**Owner:** Yair Pinchasi

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Goals & Non-Goals](#2-goals--non-goals)
3. [System Architecture](#3-system-architecture)
4. [Hardware Specification](#4-hardware-specification)
5. [Software Architecture](#5-software-architecture)
6. [Memory Architecture](#6-memory-architecture)
7. [User Management & Permissions](#7-user-management--permissions)
8. [Feature Specification](#8-feature-specification)
9. [API & External Services](#9-api--external-services)
10. [Security & Privacy](#10-security--privacy)
11. [Development Roadmap](#11-development-roadmap)
12. [Open Questions](#12-open-questions)

---

## 1. Executive Summary

Jane is a private, AI-powered voice assistant built specifically for smart home control. Unlike commercial alternatives (Alexa, Google Home, Siri), Jane is designed around three core principles:

- **Natural Hebrew conversation** — no fixed commands, no rigid syntax
- **Persistent memory** — Jane learns and remembers each family member over time
- **Privacy-first** — wake word detection is fully local; cloud is used only for STT and LLM inference

Jane runs on a Raspberry Pi 5 with Home Assistant and uses ESP32-based satellites (M5Stack Atom EchoS3R) placed throughout the home. Each satellite has a built-in microphone and speaker, connected via USB-C power and communicating over Wi-Fi.

---

## 2. Goals & Non-Goals

### Goals

| # | Goal |
|---|------|
| G1 | Support fully natural Hebrew conversation without predefined commands |
| G2 | Control all Home Assistant devices via voice |
| G3 | Maintain persistent memory per user, shared family memory, and behavioral patterns |
| G4 | Support multiple users with different permission levels |
| G5 | Use a custom wake word ("Hey Jane") that activates Jane hands-free |
| G6 | Deliver natural-sounding Hebrew TTS responses |
| G7 | Support multiple room satellites with independent audio |
| G8 | Send proactive alerts from sensors via voice |
| G9 | Learn household patterns and proactively suggest automations |
| G10 | Support family routines (morning, goodnight, leaving home, arriving home) |
| G11 | Be reliable — auto-restart on failure, fallback notifications if actions fail |
| G12 | Access real-time information (weather, news, traffic) via web search |

### Non-Goals

| # | Non-Goal | Reason |
|---|----------|--------|
| NG1 | Multi-language support (beyond Hebrew/English) | Out of scope for v1 |
| NG2 | Video or camera integration | Future consideration |
| NG3 | External internet-facing API | Local network only |
| NG4 | Music streaming | Not the primary use case |

---

## 3. System Architecture

### 3.1 High-Level Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                        Satellite (ESP32)                        │
│  Microphone → Wake Word Detection (Porcupine) → Audio Stream   │
└────────────────────────────┬────────────────────────────────────┘
                             │ Wi-Fi (Wyoming Protocol)
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Raspberry Pi 5 — Jane Core                  │
│                                                                 │
│  Audio In → Whisper STT → GPT-4o Mini ←→ Memory Engine        │
│                                  │                              │
│                                  ├──→ HA MCP Server            │
│                                  │       │                      │
│                                  │       ▼                      │
│                                  │   Home Assistant             │
│                                  │   (lights, climate,          │
│                                  │    shutters, sensors)        │
│                                  │                              │
│                            Response Text                        │
│                                  │                              │
│                                  ▼                              │
│                          ElevenLabs TTS                         │
│                                  │                              │
└──────────────────────────────────┼──────────────────────────────┘
                                   │ Audio Out
                                   ▼
                         Satellite Speaker
```

### 3.2 Technology Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Voice Satellite | M5Stack Atom EchoS3R (ESP32-S3) | Built-in mic + speaker, Wi-Fi, ESPHome support |
| Wake Word | Porcupine (Picovoice) | Custom name support, Hebrew, runs locally on ESP32 |
| Speech-to-Text | OpenAI Whisper API | Best Hebrew STT available |
| LLM | OpenAI GPT-4o Mini | Best cost/quality ratio for conversational use |
| Smart Home | Home Assistant + MCP Server | Full device control, automation, sensor access |
| Text-to-Speech | ElevenLabs | Most natural Hebrew voice quality |
| Server | Raspberry Pi 5 | Low power, always-on, runs HA + Jane |
| Language | Python 3.14 | Best ecosystem for audio + AI libraries |
| Protocol | Wyoming Protocol | Standard HA satellite communication |

---

## 4. Hardware Specification

### 4.1 Current Inventory

| Component | Model | Status | Notes |
|-----------|-------|--------|-------|
| Server | Raspberry Pi 5 | ✅ Active | Runs Home Assistant OS |
| Satellite | M5Stack Atom EchoS3R | 🔲 Ordered | Built-in mic + speaker, USB-C power |

### 4.2 Satellite Specification — Atom EchoS3R

| Spec | Value |
|------|-------|
| SoC | ESP32-S3-PICO-1-N8R8 |
| CPU | Dual-core LX7 @ 240MHz |
| Flash | 8MB |
| PSRAM | 8MB |
| Audio Codec | ES8311 (24-bit) |
| Microphone | MEMS, SNR 65dB |
| Amplifier | NS4150B, 1W |
| Connectivity | 2.4GHz Wi-Fi |
| Power | USB-C 5V |
| Price | ~$14/unit |

### 4.3 Deployment Plan

```
Living Room    → Atom EchoS3R #1   (Phase 1)
Kitchen        → Atom EchoS3R #2   (Phase 2)
Master Bedroom → Atom EchoS3R #3   (Phase 2)
Kids Room      → Atom EchoS3R #4   (Phase 3)
```

Each satellite is powered by USB-C wall adapter and communicates wirelessly. No audio wiring required.

---

## 5. Software Architecture

### 5.1 Project Structure

```
jane/
├── jane.py               # Entry point, main conversation loop
├── brain.py              # LLM integration, intent parsing, action execution
├── voice.py              # Audio recording, Whisper STT, TTS playback
├── ha_client.py          # Home Assistant REST API client
├── memory.py             # Memory read/write engine (v2)
├── config.py             # Settings, environment vars, system prompt
├── requirements.txt
├── .env                  # API keys (gitignored)
└── memory/
    ├── family.json        # Shared family memory
    ├── habits.json        # Learned behavioral patterns
    └── users/
        ├── yair.json
        ├── user2.json
        └── user3.json
```

### 5.2 Core Modules

#### `jane.py` — Main Loop
- Initializes all modules
- Manages conversation loop
- Handles wake word trigger (keyboard for dev, ESP32 satellite for prod)
- Calls `voice.py` → `brain.py` → `voice.py`

#### `voice.py` — Audio Engine
- Records audio with silence detection (stops after 1.5s of silence)
- Sends audio to Whisper API with `language="he"`
- Receives response text and plays via ElevenLabs TTS

#### `brain.py` — Intelligence Layer
- Loads memory context for identified user
- Queries Home Assistant for current entity states via MCP
- Sends full context to GPT-4o Mini
- Parses structured JSON response
- Executes HA actions or returns conversational reply
- Triggers memory update after each conversation

#### `ha_client.py` — HA Integration
- Authenticates via Long-Lived Access Token
- Fetches entity states filtered by relevant domains
- Calls HA services (turn_on, turn_off, set_temperature, etc.)
- Tests connection on startup

#### `memory.py` — Memory Engine *(v2)*
- Loads user profile, family memory, and habits at conversation start
- After each conversation, asks GPT: "What should be remembered?"
- Classifies new information: personal / family / habit
- Updates correct JSON file
- Applies confidence decay over time
- Supports silent mode (no memory saved)

### 5.3 LLM Response Schema

Jane always responds in structured JSON:

```json
// Smart home action
{
  "action": "ha_service",
  "domain": "light",
  "service": "turn_on",
  "entity_id": "light.living_room",
  "data": { "brightness_pct": 70 },
  "response": "Turning on the living room lights."
}

// Conversational reply
{
  "action": "speak",
  "response": "Good morning! The temperature outside is 18°C."
}

// Clarification needed
{
  "action": "clarify",
  "question": "Which room would you like me to heat?",
  "options": ["Living room", "Bedroom", "Both"]
}
```

---

## 6. Memory Architecture

### 6.1 Memory Layers

```
┌─────────────────────────────────────────────┐
│              Conversation Context           │  (in-memory, current session)
├─────────────────────────────────────────────┤
│              Personal Memory               │  users/{name}.json
├─────────────────────────────────────────────┤
│              Family Memory                 │  family.json
├─────────────────────────────────────────────┤
│              Habit Memory                  │  habits.json
└─────────────────────────────────────────────┘
```

### 6.2 Personal Memory Schema

```json
{
  "identity": {
    "name": "Yair",
    "role": "admin",
    "voice_profile": null
  },
  "preferences": {
    "living_room_light_evening": "70%",
    "morning_heating_time": "07:00",
    "preferred_tts_tone": "friendly"
  },
  "facts": [
    {
      "fact": "Works from home",
      "confidence": 0.95,
      "source": "conversation",
      "timestamp": "2026-03-09",
      "expires": null
    }
  ],
  "emotional_context": {
    "last_mood": "neutral",
    "last_updated": "2026-03-09"
  },
  "history_summary": "Yair typically asks for lights in the evening and heating in the morning. Prefers concise responses.",
  "last_updated": "2026-03-09"
}
```

### 6.3 Family Memory Schema

```json
{
  "members": ["Yair", "Name2", "Child1", "Child2"],
  "household_rules": [
    "No lights in kids rooms after 21:00",
    "Do not unlock front door without asking"
  ],
  "events": [
    { "event": "Child1 birthday", "date": "04-15", "recurring": true }
  ],
  "shared_preferences": {
    "shabbat_mode": "Friday 18:00 — dim all lights to 30%"
  },
  "last_updated": "2026-03-09"
}
```

### 6.4 Habits Memory Schema

```json
{
  "patterns": [
    {
      "trigger": "Weekday, 07:00",
      "action": "Turn on living room heating",
      "confidence": 0.87,
      "occurrences": 14,
      "last_seen": "2026-03-09"
    },
    {
      "trigger": "Evening, Yair home",
      "action": "Dim living room lights to 40%",
      "confidence": 0.73,
      "occurrences": 9,
      "last_seen": "2026-03-08"
    }
  ],
  "suggested_automations": [
    "Consider automating: living room heating on weekdays at 07:00"
  ]
}
```

### 6.5 Memory Management Rules

| Rule | Description |
|------|-------------|
| Auto-save | After every conversation, GPT decides what to remember |
| Classification | GPT classifies: personal / family / habit |
| Deduplication | New facts update existing ones, never duplicate |
| Confidence decay | Confidence drops 0.05/month if not reinforced |
| Expiry | Facts with confidence < 0.3 are archived |
| Silent mode | User can say "don't remember this conversation" |
| Delete | Each user can delete their personal memory |
| Correction learning | If Jane makes a mistake and is corrected, she updates immediately |

---

## 7. User Management & Permissions

### 7.1 User Profiles

| User | Role | Age Profile | Permissions |
|------|------|-------------|-------------|
| Yair | Admin | Adult | Full access |
| User 2 | Admin | Adult | Full access |
| Child 1 | Child | Kid | Restricted |
| Child 2 | Child | Kid | Restricted |

### 7.2 Permission Matrix

| Action | Admin | Child |
|--------|-------|-------|
| Control lights | ✅ | ✅ |
| Control climate | ✅ | ✅ (read only) |
| Open/close shutters | ✅ | ✅ |
| Lock/unlock door | ✅ | ❌ |
| Disable alarms | ✅ | ❌ |
| Create automations | ✅ | ❌ |
| View family memory | ✅ | ❌ |
| Delete personal memory | ✅ | ✅ (own only) |

### 7.3 User Identification

**Phase 1 (current):** Jane asks "Who's speaking?" at the start of each session.

**Phase 3 (future):** Automatic voice recognition — Jane identifies the user from voice profile without asking.

### 7.4 Communication Style by Profile

| Profile | Jane's Tone |
|---------|-------------|
| Admin | Natural, concise, professional |
| Child | Warm, simple language, encouraging, age-appropriate |

---

## 8. Feature Specification

### 8.1 Smart Home Control

| Feature | Description | Status |
|---------|-------------|--------|
| Lights | On/off, dim, color via natural language | ✅ v1 |
| Climate | AC, heating, temperature control | ✅ v1 |
| Shutters | Open, close, percentage | ✅ v1 |
| Appliances | Smart switches (Switcher) | ✅ v1 |
| Media | Play, pause, volume | 🔲 v2 |
| Status queries | "Is the kitchen light on?" | ✅ v1 |
| Scheduling | "Turn on heating tomorrow at 7am" | ✅ v1 |
| Multi-action | "Turn off all lights and set AC to 22" | ✅ v1 |

### 8.2 Proactive Alerts

| Alert | Trigger | Example |
|-------|---------|---------|
| Open window | Rain forecast + window sensor | "Yair, it's about to rain and the bedroom window is open" |
| Gas left on | Stove sensor active > 60 min | "The stove has been on for an hour — everything ok?" |
| Forgot lights | User left home, lights still on | "You left the living room lights on" |
| Temperature | Room temp below/above threshold | "The kids room dropped below 18°C" |
| Door bell | Door sensor trigger | "Someone's at the door — want me to check the camera?" |

### 8.3 Conversation Features

| Feature | Description | Status |
|---------|-------------|--------|
| Natural Hebrew | No fixed commands required | ✅ v1 |
| Clarification | Jane asks when intent is ambiguous | ✅ v1 |
| Session memory | Remembers what was said during a conversation | ✅ v1 |
| Persistent memory | Remembers across sessions | 🚧 v2 |
| Cross-session continuity | "As we discussed yesterday..." | 🔲 v3 |
| Proactive initiation | Jane starts conversations | 🔲 v4 |
| Mood detection | Detects tone from voice | 🔲 v4 |
| Correction learning | Learns from user corrections | 🔲 v3 |
| Concise responses | Simple commands → "בוצע", complex queries → full response | 🚧 v2 |
| Night mode | Quiet hours (23:00–07:00): lower volume, shorter replies, no non-urgent alerts | 🔲 v2 |
| Destructive action confirmation | "כיבוי כל הבית" or "ביטול אזעקה" require voice confirmation | 🔲 v2 |
| Intra-day context | Jane remembers earlier events of the same day across sessions | 🔲 v3 |
| Family messaging | "תגידי לאמא שאני בדרך" — delivered next time she speaks to Jane | 🔲 v3 |

### 8.4 Intelligence & Learning

| Feature | Description | Status |
|---------|-------------|--------|
| Pattern learning | After repeated behavior, Jane suggests an automation | 🔲 v3 |
| Anomaly detection | Alerts on unusual device usage (e.g. AC on for 6 hours) | 🔲 v3 |
| Presence awareness | Jane knows who is home via HA presence detection and adapts responses | 🔲 v3 |
| Routine support | Single command triggers a sequence (e.g. "לילה טוב" → lights off, lock door, close shutters, set AC) | 🔲 v3 |

**Example routines:**

| Routine | Trigger | Actions |
|---------|---------|---------|
| לילה טוב | Voice command | Turn off all lights, lock door, close shutters, set AC to 22°C, activate kids white noise |
| בוקר טוב | Voice command | Open shutters, set heating, brief weather summary |
| יוצא מהבית | Presence leave | Turn off all lights and AC, lock door, notify user |
| הגעתי הביתה | Presence arrive | Turn on entrance light, set preferred temperature |

### 8.5 Family Features

| Feature | Description | Status |
|---------|-------------|--------|
| Kids reward mode | Jane praises kids for good actions ("כיבית את האור — כל הכבוד! 🌟") | 🔲 v3 |
| Google Calendar integration | "מה יש לנו השבוע?" — Jane summarizes upcoming events aloud | 🔲 v4 |
| Family messaging | Voice messages relayed between family members via Jane | 🔲 v3 |

### 8.6 Reliability & Operations

| Feature | Description | Status |
|---------|-------------|--------|
| Watchdog process | Monitors Jane and auto-restarts if it crashes | 🔲 v2 |
| Fallback notification | If action fails, sends WhatsApp/Telegram message to user | 🔲 v3 |
| Command history log | Full log of all voice commands and actions taken | 🔲 v2 |
| Health check endpoint | Local HTTP endpoint to verify Jane is alive | 🔲 v2 |

---

## 9. API & External Services

### 9.1 Cost Estimate (Monthly)

Assuming ~20 interactions/day, average 500 input + 300 output tokens per interaction:

| Service | Usage | Est. Monthly Cost |
|---------|-------|-------------------|
| OpenAI Whisper | 20 clips/day × ~30s | ~$0.18 |
| OpenAI GPT-4o Mini | 20 interactions/day | ~$0.12 |
| ElevenLabs TTS | ~3,000 chars/day | Free tier (90K/mo) |
| Tavily Search API | ~100 searches/month | Free tier (1K/mo) |
| **Total** | | **~$0.30/month** |

### 9.2 Web Search Integration

Jane uses **Tavily API** for real-time web search. Tavily is purpose-built for LLMs — it returns clean, structured text without HTML noise, making it ideal for GPT to summarize.

**Why Tavily over specific APIs (weather, maps, etc.):**
> Jane can search for anything just like a human would Google it. No need to maintain separate API integrations per data source.

**Examples of what Jane searches for:**
- Weather: "What's the weather tomorrow in Tel Aviv?"
- Traffic: "How long to drive to Jerusalem right now?"
- News: "What happened in Israel today?"
- Business hours: "Is the supermarket open now?"
- Exchange rates: "What's the dollar rate today?"
- Any general knowledge question

**Implementation:**

```python
# Jane's tool — called by GPT-4o Mini when needed
def search_web(query: str) -> str:
    results = tavily.search(query, max_results=3)
    return "\n".join([r["content"] for r in results["results"]])
```

GPT decides autonomously when to call this tool based on the user's question. No hardcoded triggers required.

**Free tier:** 1,000 searches/month — sufficient for home use.  
**Paid tier:** $20/month for 10,000 searches (only if needed).

### 9.3 Home Assistant Integration

Jane connects to HA via the native MCP Server integration (available since HA 2025.2):

- **Endpoint:** `http://homeassistant.local:8123/api/mcp`
- **Auth:** Long-Lived Access Token (Bearer)
- **Entity access:** Configurable via HA Exposed Entities page
- **Domains used:** `light`, `switch`, `climate`, `cover`, `media_player`, `fan`, `lock`, `alarm_control_panel`

---

## 10. Security & Privacy

| Concern | Mitigation |
|---------|-----------|
| Wake word privacy | Processed 100% locally on ESP32 — no audio sent to cloud until wake word detected |
| API key exposure | Stored in `.env` file, never committed to version control |
| HA access | HA Token scoped to exposed entities only |
| Local network only | Jane's server not exposed to the internet |
| Child protection | Permission matrix blocks sensitive actions for child profiles |
| Memory privacy | Silent mode available; users can delete personal memory |
| Data storage | All memory stored locally on Raspberry Pi |

---

## 11. Development Roadmap

### Version 1 — Foundation ✅ Complete

- [x] Full voice pipeline: mic → Whisper → GPT-4o Mini → TTS → speaker
- [x] Home Assistant integration via REST API
- [x] Hebrew system prompt and natural conversation
- [x] Silence detection (auto-stop recording)
- [x] Modular code architecture

### Version 2 — Memory 🚧 In Progress

- [ ] Personal memory per user (JSON)
- [ ] Family shared memory
- [ ] Habit memory
- [ ] Auto memory management by GPT
- [ ] User identification ("Who's speaking?")
- [ ] Permission matrix enforcement
- [ ] Silent mode
- [ ] Concise responses (simple command → "בוצע")
- [ ] Night mode (23:00–07:00 quiet behavior)
- [ ] Destructive action voice confirmation
- [ ] Watchdog auto-restart process
- [ ] Command history log
- [ ] Health check endpoint

### Version 3 — Hardware & Intelligence 🔲 Waiting for Equipment

- [ ] Flash Atom EchoS3R with ESPHome
- [ ] Configure Wyoming Protocol satellite
- [ ] Integrate Porcupine wake word ("Hey Jane")
- [ ] Deploy to Raspberry Pi (always-on)
- [ ] Multi-satellite support
- [ ] Intra-day context across sessions
- [ ] Pattern learning → automation suggestions
- [ ] Anomaly detection on device usage
- [ ] Presence awareness (who is home)
- [ ] Routine support ("לילה טוב", "בוקר טוב", etc.)
- [ ] Kids reward mode
- [ ] Family messaging between members
- [ ] Fallback WhatsApp/Telegram notification

### Version 4 — Polish & Expansion 🔲 Future

- [ ] ElevenLabs TTS for natural Hebrew voice
- [ ] Cross-session conversation continuity
- [ ] Proactive alerts from sensors
- [ ] Mood detection from voice tone
- [ ] Automatic voice-based user identification
- [ ] Mobile notification fallback
- [ ] Google Calendar integration ("מה יש לנו השבוע?")
- [ ] Proactive pattern suggestions
- [ ] Internet access: weather, traffic, news, business hours via Tavily

---

## 12. Open Questions

| # | Question | Priority | Status |
|---|----------|----------|--------|
| OQ1 | Which Hebrew voice on ElevenLabs sounds most natural? | Medium | Open |
| OQ2 | How to handle simultaneous satellite triggers (two rooms hear wake word)? | High | Open |
| OQ3 | Should Jane support English commands as fallback? | Low | Open |
| OQ4 | What is the right confidence decay rate for memory? | Medium | Open |
| OQ5 | Should habit suggestions be proactive or only on request? | Medium | Open |
| OQ6 | Which messaging platform for fallback notifications — WhatsApp or Telegram? | Medium | Open |
| OQ7 | Should routines be hardcoded or user-definable via voice? | High | Open |
| OQ8 | How to handle night mode when an urgent alert occurs (e.g. open window in rain)? | Medium | Open |
| OQ9 | At what threshold of pattern repetitions should Jane suggest an automation? | Medium | Open |

---

*Jane PRD v1.1 — Living document, updated with project progress.*  
*Next review: After Version 2 memory implementation.*
