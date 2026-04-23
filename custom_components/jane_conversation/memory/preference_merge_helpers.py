"""Pure-function helpers for B1 preference merge — winner selection + value concat.

Split out from preference_optimizer.py to stay under the 300-line file cap
(CLAUDE.md CI check #4). No side effects, no I/O — keep it that way.
"""

from __future__ import annotations

MAX_VALUE_LEN = 400


def pick_winner(a: dict, b: dict) -> tuple[dict, dict]:
    """Winner: higher confidence; tie-break by later last_reinforced."""
    ac, bc = float(a.get("confidence") or 0), float(b.get("confidence") or 0)
    if ac != bc:
        return (a, b) if ac > bc else (b, a)
    a_ts, b_ts = a.get("last_reinforced"), b.get("last_reinforced")
    if a_ts and b_ts:
        return (a, b) if a_ts >= b_ts else (b, a)
    return (a, b)


def merge_values(winner: str, loser: str) -> str:
    """Winner value + loser value concat (unless loser is already a substring)."""
    w = (winner or "").strip()
    lv = (loser or "").strip()
    if not lv or lv.lower() in w.lower():
        return w
    if not w:
        return lv
    candidate = f"{w}; {lv}"
    if len(candidate) > MAX_VALUE_LEN:
        return w  # keep winner unchanged; caller's reason should note truncation
    return candidate
