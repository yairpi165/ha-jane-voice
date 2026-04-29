# Operator Playbook — Proactive Triggers Tier 1

S3.2 (JANE-45). Reference for operators wiring HA automations that fire
`[PROACTIVE]` events into Jane via `conversation.process`. Each entry is
a self-contained YAML snippet you can paste into HA → Settings →
Automations & Scenes → YAML, plus a verification SQL.

This file is intentionally extensible — append new entries as new
proactive surfaces emerge. Tier-2 triggers (anomalies, automation
suggestions) ship in S3.3 (JANE-43 / JANE-44).

---

## How [PROACTIVE] works (one-screen summary)

- HA automation crafts a string starting with `[PROACTIVE] `, then calls
  `conversation.process` with `agent_id: conversation.jane`.
- `conversation.py` detects the prefix, parses the payload (description /
  Time / Mode), enforces the mode gate (`MODE_RULES[mode]["proactive"]`),
  invokes `think()` with `is_proactive=True`, and writes one row to
  `events` with `event_type='proactive_decision'` per decision —
  including suppressions and parse drops.
- The LLM decides voice / notification / silent via `route_alert(...)` and
  `log_proactive_decision(...)`. The trust budget caps speech at 2/day.
- `[PROACTIVE]` turns are NOT appended to conversation history or
  `working_memory.record_interaction` — they aren't user speech.
- Modes with `proactive=False` (`עבודה`, `לילה`, `אורחים`,
  `ילדים ישנים`, `נסיעה`) short-circuit BEFORE `think()` — no LLM call,
  one `suppressed_by_mode` audit row, no speech, no notification.

The string format is:

```
[PROACTIVE] {description}. Time: HH:MM. Mode: {mode}.
```

Required: `description`, `Time`. Optional: `Mode` (Jane reads from HA
if missing or invalid). Bare `[PROACTIVE]` with no description AND no
Time → dropped + `dropped_malformed_payload` audit row.

---

## Entry 1 — Arrival home (state trigger on `person.*`)

**Trigger:** any tracked person transitions to `home`.

**Effect:** Jane greets in modes that allow proactive behaviour
(`רגיל`, `לא בבית`); silently audits + skips speech in the rest.

```yaml
alias: "Jane proactive: arrival home"
description: Notify Jane when a tracked person arrives home.
trigger:
  - platform: state
    entity_id:
      - person.alice
      - person.bob
    to: "home"
action:
  - service: conversation.process
    data:
      agent_id: conversation.jane
      text: >-
        [PROACTIVE] {{ trigger.to_state.attributes.friendly_name }} arrived.
        Time: {{ now().strftime('%H:%M') }}.
        Mode: {{ states('select.jane_household_mode') }}.
mode: single
```

**Verify it fired:**

```sql
SELECT id, timestamp, description, metadata
  FROM events
 WHERE event_type = 'proactive_decision'
   AND metadata->>'trigger' ILIKE '%arrived%'
   AND timestamp > NOW() - INTERVAL '1 hour'
 ORDER BY timestamp DESC
 LIMIT 5;
```

Expected: one row per arrival. `metadata->>'routed_via'` is `voice`,
`notification`, or `null` (suppressed). Zero rows = the automation
didn't fire — check Jane's `_LOGGER` for parse warnings or for the
`Suppressing [PROACTIVE]` line if the mode gate caught it.

---

## Entry 2 — All away for 30 minutes (group + duration trigger)

**Trigger:** `group.family` is `not_home` continuously for 30 minutes.

**Effect:** Jane considers whether anything was left running (lights /
appliances) and either notifies or stays silent. Active in modes that
allow proactive behaviour; suppressed in `אורחים` / `ילדים ישנים` /
`נסיעה`.

```yaml
alias: "Jane proactive: all away 30min"
description: Tell Jane the house has been empty for 30 minutes.
trigger:
  - platform: state
    entity_id: group.family
    to: "not_home"
    for: "00:30:00"
action:
  - service: conversation.process
    data:
      agent_id: conversation.jane
      text: >-
        [PROACTIVE] House empty for 30 minutes — review running devices.
        Time: {{ now().strftime('%H:%M') }}.
        Mode: {{ states('select.jane_household_mode') }}.
mode: single
```

**Verify it fired:**

```sql
SELECT id, timestamp, description, metadata
  FROM events
 WHERE event_type = 'proactive_decision'
   AND metadata->>'trigger' ILIKE '%empty%'
   AND timestamp > NOW() - INTERVAL '24 hours'
 ORDER BY timestamp DESC;
```

If you see only `suppressed_by_mode` rows for a mode you expected to be
active: the gate caught it as designed (mode has `proactive=False`).
That's a feature — silent in-house presence is worth more than the
review.

---

## Entry 3 — Voice-triggered "לילה טוב" (NOT an HA automation)

This isn't an automation — it's the third Tier-1 surface, included here
because operators need to know it exists. When a household member says
`לילה טוב` (and similar variants), the LLM detects the intent and
treats the turn AS IF it were a `[PROACTIVE]` event, calling
`set_household_mode("לילה")` and `log_proactive_decision(...)` to keep
the audit trail consistent across all three Tier-1 surfaces.

There is no YAML here — this path is internal to Jane. Documented for
completeness so the audit query below is interpretable.

**Verify the goodnight flow audited correctly:**

```sql
SELECT id, timestamp, user_name, description, metadata
  FROM events
 WHERE event_type = 'proactive_decision'
   AND metadata->>'trigger' ILIKE '%goodnight%'
   AND timestamp > NOW() - INTERVAL '7 days'
 ORDER BY timestamp DESC;
```

Expected: one row per "לילה טוב" the household said in the last week,
each paired with a row in `household_mode_transitions` (S3.1) for the
flip to `לילה`.

---

## Trust-budget verification

Voice routes consume one daily speech token (cap = 2). Critical urgency
bypasses the cap (safety always speaks). Inspect the live counter:

```bash
# From the dev VM:
docker exec -it jane_db redis-cli \
  GET "jane:proactive:speech_count:$(date +%Y-%m-%d)"
```

`(nil)` = no proactive speech today. `2` = at cap; the next non-critical
voice route will be downgraded to notification by the LLM following the
rules in `PROACTIVE_SYSTEM_INSTRUCTIONS`.

---

## When something goes wrong

| Symptom | Where to look |
|---|---|
| `[PROACTIVE]` reaches Jane but no `events` row | `_LOGGER` for `record_proactive_decision` failure; check `pg_pool` health |
| Audit shows `dropped_malformed_payload` | YAML pasted with broken Jinja; re-render the template + re-trigger |
| Audit shows `suppressed_by_mode` for an unexpected mode | Confirm `select.jane_household_mode` value; the mode gate is correct, the mode is wrong |
| Speech count stuck at the cap | TTL is 26h; `redis-cli TTL <key>` to confirm; `DEL` to manually reset for testing |
| Critical-urgency rate spike | Run KPI #5 in `docs/kpi_queries.sql`; LLM may be drifting on the SAFETY-ONLY rule |
