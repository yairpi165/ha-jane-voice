# Operator Playbook — Household Modes

S3.1 (JANE-42). Reference for operators wiring HA automations to flip
`input_select.jane_household_mode`. Each entry is a self-contained YAML
snippet you can paste into HA → Settings → Automations & Scenes → YAML.

This file is intentionally extensible — append new entries as new triggers
emerge (e.g. an S3.4 Travel-mode geofence). Don't inline this list into
PR descriptions; PR descriptions get lost, docs files are searchable.

---

## How modes work (one-screen summary)

- The active mode lives in `input_select.jane_household_mode` (auto-created
  by the integration on first setup).
- The 7 modes: `רגיל` / `עבודה` / `לילה` / `אורחים` / `לא בבית` /
  `ילדים ישנים` / `נסיעה`.
- Jane reads the helper at every `think()` call and injects a Hebrew block
  into the system prompt so her phrasing matches the mode.
- A hard gate at `tools/registry.py:execute_tool` enforces `MODE_RULES[mode]["tts"]`
  for `tts_announce` + `send_notification`. Other behavior is prompt-side
  (Jane chooses whether to be proactive based on the mode's `proactive` flag).
- Switching is logged to `household_mode_transitions` (PG) with `from_mode`,
  `to_mode`, `trigger`, `triggered_by`, `reason` for the Phase 4 Decision Log.
- Switching is **confirmed**, never inferred — the LLM only calls
  `set_household_mode` for explicit phrases (see SYSTEM_PROMPT §
  "Household Mode" for the trigger rules).

You can flip the mode three ways:

1. The user says it (`עברי למצב לילה` → `set_household_mode` tool).
2. An HA automation calls `input_select.select_option` directly (this file).
3. An HA automation calls the `jane_conversation.set_household_mode` service
   (S3.2 — not in this PR).

Path 2 is the recommended way for time-/presence-/sensor-driven flips —
HA-native, no LLM round-trip, fastest. The audit row is written by Jane
the next time she observes the change in `state_changed`. (S3.2 will close
this gap by listening for `input_select` state changes directly.)

---

## Entry 1 — 23:00 → לילה (time trigger)

**Trigger:** every night at 23:00 local time.

**Effect:** `MODE_NIGHT`. `tts_announce` and `send_notification` are
hard-blocked by the gate. `proactive=False` — Jane stops volunteering
information. Her replies become short and quiet (also reinforced by the
existing `## Night Mode (23:00–07:00)` block in the system prompt).

```yaml
alias: "Jane mode: לילה at 23:00"
description: Switch Jane to night mode every evening.
trigger:
  - platform: time
    at: "23:00:00"
action:
  - service: input_select.select_option
    target:
      entity_id: input_select.jane_household_mode
    data:
      option: "לילה"
mode: single
```

**Verify it fired:**

```sql
-- Run in psql or TablePlus:
SELECT * FROM household_mode_transitions
 WHERE to_mode = 'לילה' AND ts > NOW() - INTERVAL '24 hours'
 ORDER BY ts DESC LIMIT 5;
```

You should see at least one row from the most recent 23:00. If you see
zero, the automation didn't fire — check Jane's `_LOGGER` for warnings
about `input_select.select_option` or `household_mode_transitions` and
make sure the helper exists (`hass.states.get('input_select.jane_household_mode')`).

---

## Entry 2 — 08:00 → רגיל (time trigger)

**Trigger:** every morning at 08:00 local time.

**Effect:** `MODE_NORMAL`. Restores TTS + proactive behavior. The pair
{Entry 1, Entry 2} forms a complete day/night cycle — both halves required;
running Entry 1 alone leaves Jane stuck in night mode all day.

```yaml
alias: "Jane mode: רגיל at 08:00"
description: Restore Jane to normal mode every morning.
trigger:
  - platform: time
    at: "08:00:00"
action:
  - service: input_select.select_option
    target:
      entity_id: input_select.jane_household_mode
    data:
      option: "רגיל"
mode: single
```

**Verify:** same SQL as Entry 1 with `to_mode = 'רגיל'`.

---

## Entry 3 — All persons left zone "home" → לא בבית (presence trigger)

**Trigger:** every member of the family is `not_home`.

**Effect:** `MODE_AWAY`. `tts_announce` + `send_notification` go through
the silent path (gate denies in-house TTS; alerts route to phones via the
existing `notify` services Jane already uses). `proactive=True` — Jane
actively monitors for security anomalies (unusual motion, leaks, doors).

The trigger uses a `state` platform on the `group.family` entity (you
need to define this group in `groups.yaml` with all `person.*` entities).
Doing it as a group state change rather than per-person triggers fires
exactly once when the last person leaves rather than N times.

```yaml
alias: "Jane mode: לא בבית when everyone leaves"
description: Switch Jane to away mode when all family members are not_home.
trigger:
  - platform: state
    entity_id: group.family
    to: "not_home"
    for: "00:05:00"   # 5 min debounce avoids rapid in/out flicker
action:
  - service: input_select.select_option
    target:
      entity_id: input_select.jane_household_mode
    data:
      option: "לא בבית"
mode: single
```

Companion automation to flip back when *anyone* returns:

```yaml
alias: "Jane mode: רגיל when someone comes home"
description: Restore normal mode when first family member arrives home.
trigger:
  - platform: state
    entity_id: group.family
    to: "home"
action:
  - service: input_select.select_option
    target:
      entity_id: input_select.jane_household_mode
    data:
      option: "רגיל"
mode: single
```

> Note: this companion will also fire at 08:00 if someone wakes up at home,
> overriding Entry 2 redundantly — that's fine, the audit row records both
> requests. If you want to avoid the double-write, add a condition
> `state(input_select.jane_household_mode) != 'רגיל'` to either automation.

**Verify:**

```sql
SELECT to_mode, trigger, ts FROM household_mode_transitions
 WHERE to_mode IN ('לא בבית', 'רגיל')
 ORDER BY ts DESC LIMIT 10;
```

---

## Adding new triggers (template)

```yaml
alias: "Jane mode: <MODE> when <TRIGGER>"
trigger:
  - platform: <state | time | numeric_state | sun | …>
    # …trigger config…
action:
  - service: input_select.select_option
    target:
      entity_id: input_select.jane_household_mode
    data:
      option: "<one of: רגיל / עבודה / לילה / אורחים / לא בבית / ילדים ישנים / נסיעה>"
```

Keep the `option:` value in Hebrew exactly as listed — the gate matches by
string equality. A typo silently falls back to `רגיל` (failure-closed) and
you'll see a warning in the log:

```
Unknown household mode value '...' — falling back to רגיל
```

### Anti-patterns

- **Don't** flip the helper from inside Jane's own response handler
  (e.g. via `call_ha_service`) — use `set_household_mode`. The handler
  routes through `set_active_mode` which writes the audit row; bypassing
  it means the transition isn't logged.
- **Don't** flip to `ילדים ישנים` from a presence-only trigger (e.g.
  "child arrives in their room"). That's an inferred state — the right
  trigger is an explicit goodnight routine or a parent toggling the helper.
  Confirmed > inferred (D17 rationale, see SYSTEM_PROMPT § Household Mode).
- **Don't** rate-limit by adding `delay:` after `select_option` — the helper
  flip is instantaneous and idempotent. Use a `condition:` block on the
  current state instead.
