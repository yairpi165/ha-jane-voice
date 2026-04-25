# Tool Calling Architecture

> Source of truth for this document is in the Notion project workspace.
> This file is kept as a quick reference only.

## Overview

Jane uses Gemini 2.5 function calling with dual-model architecture:
- **Flash** — chat + commands (fast, cheap)
- **Pro** — complex reasoning (smart, thorough)

41 tool declarations (verify with `grep -c "^TOOL_[A-Z_]* = " custom_components/jane_conversation/tools/definitions.py`) plus the built-in Google Search tool.

## Module Layout

```
custom_components/jane_conversation/
├── brain/
│   ├── engine.py          # think() loop: model selection, system instruction assembly, tool round-trips
│   ├── classifier.py      # chat | command | complex routing
│   ├── context.py         # Working-memory context builder
│   └── working_memory.py  # WorkingMemory — Redis presence/active/changes
├── tools/
│   ├── definitions.py     # 41 FunctionDeclaration dicts (TOOL_* constants)
│   ├── registry.py        # TOOL → handler dispatch; execute_tool() error contract
│   └── handlers/
│       ├── core.py        # call_ha_service, get_entity_state, query_history, search_web
│       ├── device.py      # bulk_control, get_device, update_device, rename_entity
│       ├── discovery.py   # search_entities, get_overview, list_areas/floors, get_zone, deep_search
│       ├── calendar.py    # get_calendar_events, create_calendar_event, manage_list, set_timer
│       ├── family.py      # check_people, send_notification, tts_announce
│       ├── memory_tools.py  # save_memory, read_memory, forget_memory (A5 — soft-delete)
│       ├── config.py      # set/remove_automation/script/scene, helpers, list_config, list_services
│       └── power.py       # eval_template, get_history, get_logbook, get_statistics
└── config_api.py          # Internal LLAT + REST helpers consumed by config.py handlers
```

## Tool Categories

| Domain | Handler | Tools |
|---|---|---|
| Core HA primitives | `core.py` | call_ha_service, get_entity_state, query_history, search_web |
| Device control | `device.py` | bulk_control, get_device, update_device, rename_entity |
| Discovery | `discovery.py` | search_entities, get_overview, list_areas, list_floors, get_zone, deep_search |
| Calendar / lists / timers | `calendar.py` | get_calendar_events, create_calendar_event, manage_list, set_timer |
| Family-life primitives | `family.py` | check_people, send_notification, tts_announce |
| Memory CRUD | `memory_tools.py` | save_memory, read_memory, **forget_memory** (A5 — soft-delete via `deleted_at = NOW()`) |
| Config store | `config.py` | set/remove_automation, get_automation_config, get_automation_traces, set/remove_script, get_script_config, set/remove_scene, list_config, create_helper, list_helpers, list_services |
| Analytics | `power.py` | eval_template, get_history, get_logbook, get_statistics |
| External (built-in) | — | Google Search |

## Error Contract

`tools/registry.py.execute_tool()` catches every exception from a handler and returns the error as a string back to Gemini. Handlers **must not** raise — Jane never crashes. If Gemini sees an error string, it adapts (retries, falls back, or apologizes to the user) without breaking the conversation loop.

## Adding a Tool

1. Define `TOOL_<NAME>` dict in `tools/definitions.py` with FunctionDeclaration fields (name, description, parameters JSON Schema).
2. Import + register in `tools/registry.py` (TOOLS list + dispatch in `execute_tool()`).
3. Implement `handle_<name>(hass, args)` in the appropriate `tools/handlers/<domain>.py`.
4. Return a string. Errors caught + fed back as the result — never raised.
5. Add a unit test under `tests/test_tools.py` or `tests/test_ha_handlers.py`.
6. Update `SYSTEM_PROMPT` only if the tool changes existing behavior; new tools are usually self-describing.

## Key Invariants

- **JANE-84** — any Gemini structured-output call MUST pair `response_mime_type="application/json"` with a real `response_schema`. The mime type alone returns prose like "Here is the JSON...".
- **Smart Routines (S1.5)** — top routines from `RoutineStore` are formatted into the system instruction before each call. When Gemini selects an existing routine it calls `call_ha_service` with the cached `jane_<slug>` script, collapsing N tool calls into 1.
- **think() loop cap** — at most 10 iterations per request to prevent runaway tool round-trips.
