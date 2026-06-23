"""Shadow (log-only) trailing-stop driver.

Increment 2 of the live-trailing build: while a live position is open, compute
where a trailing-stop runner *would* move the stop this bar — and only LOG it.
Sends no orders, mutates nothing. The point is to watch the trail behave against
real fills before any active stop-replacing is built.

Uses the SAME math as the sim runner (`execution.trailing.compute_trailed_stop`),
so what we observe in shadow is exactly what active mode would do.
"""
from __future__ import annotations

from typing import List, Optional

from execution.trailing import compute_trailed_stop


def shadow_trail(
    open_pos: dict,
    bars_since_entry: List[dict],
    *,
    activation_r: float = 1.0,
    trail_r: float = 0.5,
) -> Optional[dict]:
    """Return a dict describing what the runner trail WOULD do, or None if it
    can't be computed. Pure: no I/O, no order placement, no mutation.

    ``open_pos``: {direction, entry, stop, ...}. ``bars_since_entry``: bar dicts
    with high/low, oldest→newest, covering entry → now (1-contract assumption).
    """
    direction = (open_pos.get("direction") or "").upper()
    if direction not in ("LONG", "SHORT"):
        return None
    try:
        entry = float(open_pos["entry"])
        stop = float(open_pos["stop"])
    except (KeyError, TypeError, ValueError):
        return None
    if not bars_since_entry:
        return None

    is_long = direction == "LONG"
    try:
        if is_long:
            max_favorable = max(float(b["high"]) for b in bars_since_entry)
        else:
            max_favorable = min(float(b["low"]) for b in bars_since_entry)
    except (KeyError, TypeError, ValueError):
        return None

    would_stop, trailing = compute_trailed_stop(
        is_long=is_long, entry=entry, original_stop=stop,
        max_favorable=max_favorable, activation_r=activation_r, trail_r=trail_r,
    )
    R = abs(entry - stop)
    favorable_r = (abs(max_favorable - entry) / R) if R > 0 else 0.0
    return {
        "direction": direction,
        "entry": entry,
        "original_stop": stop,
        "max_favorable": max_favorable,
        "favorable_r": round(favorable_r, 2),
        "would_stop": round(would_stop, 4),
        "trailing": trailing,
        "moved": trailing and would_stop != stop,
    }


def format_shadow_log(result: dict, instrument: str) -> str:
    """One-line human/log summary of a shadow_trail result."""
    if not result.get("trailing"):
        return (f"[trail-shadow] {instrument} {result['direction']}: "
                f"+{result['favorable_r']}R favourable — not yet armed "
                f"(stop stays {result['original_stop']})")
    return (f"[trail-shadow] {instrument} {result['direction']}: "
            f"+{result['favorable_r']}R favourable — WOULD trail stop "
            f"{result['original_stop']} → {result['would_stop']} (no order sent)")
