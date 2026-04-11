# Jane — AI-Powered Smart Home Voice Assistant

A private, Hebrew-speaking voice assistant for smart home control. Built on Gemini 2.5 Pro + Flash with Home Assistant integration, running on a Raspberry Pi 5.

**Version:** 3.5.0 | **Tools:** 38 | **Tests:** 98

## What Jane Does

- **Natural Hebrew conversation** — warm, curious personality. Part of the family, not a robot.
- **38 autonomous tools** — device control, automations, scripts, scenes, discovery, notifications, timers, lists, calendar, memory, web search
- **Smart Routines** — caches multi-step commands as scripts/scenes for instant reuse
- **Persistent memory** — learns preferences, remembers family, detects patterns
- **Dual model brain** — Flash for speed, Pro for complex reasoning
- **Firebase backup** — memory backed up to Firestore

## Pipeline

```
Voice → HA Cloud STT (Nabu Casa) → Jane (Gemini 2.5) → Gemini TTS (callirrhoe)
```

## Architecture

```
jane_conversation/
├── conversation.py     # HA ConversationEntity interface
├── brain.py            # Dual model, classifier, context injection
├── tools.py            # 38 tool definitions + handlers
├── config_api.py       # Config Store REST API (automations/scripts/scenes)
├── memory.py           # 7 MD files + Gemini extraction
├── firebase.py         # Firestore backup
├── const.py            # System prompt + constants
├── web_search.py       # Google Search via Gemini
├── config_flow.py      # HA UI config
└── strings.json        # UI translations
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Brain | Gemini 2.5 Pro + Flash (dual model, function calling) |
| STT | Home Assistant Cloud (Nabu Casa) |
| TTS | Gemini TTS — callirrhoe voice ([ha-gemini-tts](https://github.com/yairpi165/ha-gemini-tts)) |
| Platform | Home Assistant OS on Raspberry Pi 5 |
| Memory | 7 LLM-managed MD files + Firebase Firestore |
| Search | Google Search (built-in via Gemini) |

## Setup

### Prerequisites
- Raspberry Pi 5 running Home Assistant OS
- Gemini API key
- Gemini TTS: [ha-gemini-tts](https://github.com/yairpi165/ha-gemini-tts)
- Firebase service account key (optional — memory backup)

### Installation via HACS
1. Add custom repository: `https://github.com/yairpi165/ha-jane-voice`
2. Install "Jane Voice Assistant"
3. Restart HA
4. Settings → Integrations → Add → Jane Voice Assistant
5. Enter Gemini API key

### Voice Pipeline
1. Install [ha-gemini-tts](https://github.com/yairpi165/ha-gemini-tts) via HACS
2. Create Voice Assistant: Settings → Voice Assistants → Add
   - **Conversation Agent:** Jane
   - **STT:** Home Assistant Cloud
   - **TTS:** Gemini TTS (voice: callirrhoe)
   - **Language:** Hebrew

## License

Private project. Not open source.
