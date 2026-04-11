# Tool Calling Architecture

> Source of truth for this document is in the Notion project workspace.
> This file is kept as a quick reference only.

## Overview

Jane uses Gemini 2.5 function calling with dual model architecture:
- **Flash** — chat + commands (fast, cheap)
- **Pro** — complex reasoning (smart, thorough)

38 tools + Google Search built-in.

## Tool Categories

- **Core:** get_entity_state, call_ha_service
- **Discovery:** search_entities, list_areas, get_history, get_statistics, get_logbook, get_overview, list_floors, get_zone
- **Family:** check_people, send_notification, set_timer, manage_list, tts_announce
- **Calendar:** get_calendar_events, create_calendar_event
- **Power:** eval_template, bulk_control, save_memory, read_memory
- **Device mgmt:** get_device, rename_entity, update_device, list_services, list_helpers, create_helper
- **Config:** set/remove automation/script/scene, list_config, get_automation_config, get_script_config, get_automation_traces, deep_search
- **External:** search_web

## Key Files

- `brain.py` — model selection, context assembly, LLM calls
- `tools.py` — tool definitions + handlers
- `config_api.py` — Config Store REST API client
