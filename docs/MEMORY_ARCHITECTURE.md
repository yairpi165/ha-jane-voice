# Memory Architecture

> Source of truth for this document is in the Notion project workspace.
> This file is kept as a quick reference only.

## Overview

Jane uses an LLM-managed memory system. Gemini 2.5 Flash reads, consolidates, and rewrites concise Markdown files. Memory content stored in English, conversations in Hebrew.

## Memory Files

```
jane_memory/
├── users/{name}.md   # Personal preferences, facts
├── family.md         # Household rules, members
├── habits.md         # Recurring patterns
├── actions.md        # Rolling 24h action log
├── home.md           # Home layout — rooms, devices
├── corrections.md    # Learned mistakes
├── routines.md       # Smart Routines (jane_ scripts/scenes)
└── history.log       # Permanent conversation log
```

## Storage

- **Local:** `config/jane_memory/` (primary)
- **Firebase:** Firestore write-through backup

## Key Files

- `memory.py` — read/write/consolidation
- `firebase.py` — Firestore backup
