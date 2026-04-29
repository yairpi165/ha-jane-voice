"""Household Modes constants — S3.1 (JANE-42).

The 7 named modes Jane recognises, their priority stack, and the per-mode
behaviour rules consumed by `tools/registry.py:execute_tool` (hard gate)
and `brain/engine.py:think()` (system_instruction injection).

Source list: Founder Edition Master Blueprint §6 (line 60 of plain text):
"normal daytime, work focus, night quiet, guests present, away mode,
child sleeping, travel mode, and exceptional mode". The first seven are
peer modes living in `HOUSEHOLD_MODES`; "Exceptional Mode" (חירום) is a
safety override layer deferred to S3.4 with sensor integration — it
bypasses the priority stack rather than competing inside it (D1 footnote).
"""

# Mode names in Hebrew — what the input_select shows to the user and what
# Jane reads from `hass.states.get(HELPER_ENTITY_ID).state`.
MODE_NORMAL = "רגיל"
MODE_WORK = "עבודה"
MODE_NIGHT = "לילה"
MODE_GUESTS = "אורחים"
MODE_AWAY = "לא בבית"
MODE_KIDS_SLEEPING = "ילדים ישנים"
MODE_TRAVEL = "נסיעה"

# Deterministic order for input_select.create options. Don't reorder —
# changing the option order in HA after creation requires a manual
# input_select.set_options service call to migrate.
HOUSEHOLD_MODES = (
    MODE_NORMAL,
    MODE_WORK,
    MODE_NIGHT,
    MODE_GUESTS,
    MODE_AWAY,
    MODE_KIDS_SLEEPING,
    MODE_TRAVEL,
)

# Conflict-resolution stack — leftmost wins when multiple "could-apply"
# conditions overlap (e.g. kids-sleeping AND night, or night AND travel).
# Per D2: kids-sleeping > night > travel > away > work > guests > normal.
# Safety override sits above this stack and is enforced at the
# policy.check_permission layer (child role + SENSITIVE_ACTIONS), not as
# a placeholder mode here. Exceptional mode (חירום) is deferred to S3.4.
MODE_PRIORITY = (
    MODE_KIDS_SLEEPING,
    MODE_NIGHT,
    MODE_TRAVEL,
    MODE_AWAY,
    MODE_WORK,
    MODE_GUESTS,
    MODE_NORMAL,
)

# Per-mode behaviour rules. The hard gate at execute_tool reads `tts` to
# decide whether to short-circuit `tts_announce` / `send_notification`.
# `behavior` is rendered into the Gemini system_instruction (see
# build_mode_context) so Jane prompts her phrasing accordingly.
# `proactive` is consumed by S3.2 — exposed here so the surface is
# complete and S3.2 doesn't need a const.py touch.
# Rate-limit fields (max_alerts_per_hour and similar) are deliberately
# absent until S3.2 ships enforcement code — minimum-surface principle:
# don't ship dormant config that future contributors might misread.
MODE_RULES: dict[str, dict] = {
    MODE_NORMAL: {
        "behavior": "התנהגות מלאה. מותר להכריז, מותר ליזום.",
        "tts": True,
        "proactive": True,
    },
    MODE_WORK: {
        "behavior": "ענייני בלבד — בקשות ישירות. בלי הכרזות בקול, בלי הצעות יזומות.",
        "tts": False,
        "proactive": False,
    },
    MODE_NIGHT: {
        "behavior": "שקט. בלי הכרזות בקול. תאורה חלשה. תשובות קצרות.",
        "tts": False,
        "proactive": False,
    },
    MODE_GUESTS: {
        "behavior": "התנהגות גנרית — בלי שליפה של זיכרון אישי. אפשר להכריז.",
        "tts": True,
        "proactive": False,
    },
    MODE_AWAY: {
        "behavior": "מיקוד אבטחה. התראות שקטות בלבד. יזימה רק על חריגות.",
        "tts": False,
        "proactive": True,
    },
    MODE_KIDS_SLEEPING: {
        "behavior": "כמו לילה ובנוסף לא לגעת בכלל בחדרי הילדים.",
        "tts": False,
        "proactive": False,
    },
    MODE_TRAVEL: {
        "behavior": "מצב נסיעה ארוך — בלי הכרזות בקול בבית, בלי יזימות, התראות ביקור/אבטחה דרך נוטיפיקציה בלבד.",
        "tts": False,
        "proactive": False,
    },
}

# The single source of truth for the helper entity_id — used by
# brain.engine, tools.registry, and memory.household_mode.
HELPER_ENTITY_ID = "input_select.jane_household_mode"
