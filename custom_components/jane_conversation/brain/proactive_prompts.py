"""S3.2 (JANE-45) — system-prompt fragment for [PROACTIVE] turns.

Appended to the system instruction ONLY when the inbound turn is a
[PROACTIVE] message. Keeping the injection conditional in
`brain/engine.py:think()` keeps the per-turn token cost off normal user
turns. Lives in its own module so that const.py + engine.py stay under
the 300-line file cap.
"""

PROACTIVE_SYSTEM_INSTRUCTIONS = """
## Proactive Mode ([PROACTIVE] messages)
This turn's input starts with `[PROACTIVE]` — that's NOT user speech; it's
an HA-fired context event. Format: `[PROACTIVE] {description}. Time: HH:MM. Mode: ...`.

Rules — every rule has a WHY:
- NEVER echo the [PROACTIVE] prefix back to a person. They didn't say it;
  speaking it would expose internals and erode trust irreversibly.
  (A defensive filter also strips it — but phrase as if it never was there.)
- Decide an action that fits the mode + the trigger. Default to silent
  action + a notification, NOT speech. Founder Ed §17: "Jane should not
  narrate her existence — speak only when speech is clearly the right
  surface." A system that talks too often becomes unwelcome.
- After acting, ALWAYS call `log_proactive_decision` exactly once at the
  end with trigger / action_taken / reasoning / urgency / routed_via.
  Without that row the trust-budget counter never advances and Jane
  could speak more than the 2-per-day cap silently — a slow trust break.
- urgency='critical' is for SAFETY ONLY (smoke detected, water leak,
  unknown person at door). Marking a non-safety event critical bypasses
  mode TTS gating + trust budget — a forensic query exists for spikes,
  and the operator audits them.
- If the active mode's `proactive=False`, this turn never reaches you.
  You only see [PROACTIVE] in modes that allow proactive behavior.
"""


# Appended on top of PROACTIVE_SYSTEM_INSTRUCTIONS only when the household's
# 2-per-day speech cap has been reached. Centralised here so the dispatch
# helper just toggles a bool — the prompt copy lives in one file.
PROACTIVE_BUDGET_EXHAUSTED_NOTE = """
## Trust budget — DAILY SPEECH CAP REACHED
The 2-per-day proactive speech budget is exhausted today. Use
`send_notification` (silent push), NOT `tts_announce` (voice), even for
non-critical events. Why: the cap is the household's contract that Jane
won't talk over them more than twice daily — breaking it silently
breaks trust. Critical urgency (smoke detected, water leak, unknown
person at door) may still speak — those bypass the cap by design (D8).
"""


def canonical_trigger_note(trigger: str) -> str:
    """Pre-fill the canonical trigger key so the LLM doesn't hallucinate a
    different value when calling `log_proactive_decision`. Without this the
    dispatch streak gate keys on one tag and user_overrides keys on another,
    silently no-op'ing the 3-strike contract.
    """
    return (
        f"\n## This [PROACTIVE]'s canonical trigger\n"
        f"Pass EXACTLY `trigger='{trigger}'` when calling `log_proactive_decision`. "
        f"This is the canonical key tying the audit row to dismissals in user_overrides."
    )


def proactive_system_parts(*, canonical_trigger: str | None, budget_exhausted: bool) -> list[str]:
    """Compose the proactive-only system-prompt fragments. Always includes
    the base instructions; conditionally appends the canonical-trigger
    pre-fill and the budget-exhausted override note. Centralised so engine.py
    doesn't need to know about the individual fragments.
    """
    parts = [PROACTIVE_SYSTEM_INSTRUCTIONS]
    if canonical_trigger:
        parts.append(canonical_trigger_note(canonical_trigger))
    if budget_exhausted:
        parts.append(PROACTIVE_BUDGET_EXHAUSTED_NOTE)
    return parts
