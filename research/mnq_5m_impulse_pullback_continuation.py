"""MNQ 5-minute impulse -> pullback -> continuation — research-only, UNPROVEN.

Follow-up to the REJECTED structural-level break/retest study
(research/mnq_structural_level_5m.py, docs/mnq-structural-level-5m-study-
2026-07-13.md). That study's failure mode was: enter immediately on a
mapped-level break/retest with a very tight structural stop against a
distant next-level target. This module is a genuinely different setup
family, not a retuned version of that one:

  1. Require an ESTABLISHED directional impulse -- reused, not reinvented:
     the current bar's own trend_direction/trend_strength/market_condition
     fields (already computed upstream by the real state-builder/Pine
     pipeline) must read STRONG + TRENDING in the trade direction.
  2. Wait for a PULLBACK after the impulse -- a contiguous run of bars
     against the impulse direction (non-increasing closes for a long
     pullback, non-decreasing for a short pullback), bounded in length so a
     genuine reversal isn't mistaken for a pullback.
  3. Require CONTINUATION CONFIRMATION -- the current bar's close breaks
     back beyond the pullback's own high (long) / low (short).
  4. Stop is placed beyond the PULLBACK's structure (its swing low/high),
     not a few points beyond a mapped level.
  5. Target is capped at a configurable R-multiple (e.g. 1.5R/2R/3R), never
     "whichever mapped level happens to be next" -- the specific defect the
     prior study's target logic had.
  6. Long and short are symmetric but scored/reported separately by the
     caller.
  7. Session bucketing reuses the same asian/london/new_york ->
     overnight/premarket/rth taxonomy as the rejected study.

Deliberately placed under `research/`, not `context/` -- this has not been
replay-validated yet. No shadow/live integration exists. Pure decision
logic only: no I/O, no journal, no risk/broker calls.
"""
from __future__ import annotations

from typing import Optional

DEFAULT_MAX_PULLBACK_BARS = 8  # 8 x 5m = 40 minutes; longer runs are a reversal, not a pullback
DEFAULT_MIN_PULLBACK_BARS = 1
DEFAULT_STOP_BUFFER_POINTS = 2.0
DEFAULT_TRIGGER_COOLDOWN_BARS = 3  # avoid near-duplicate retriggers in a choppy continuation

_SESSION_MAP = {"asian": "overnight", "london": "premarket", "new_york": "rth"}

REJECTION_REASONS = (
    "NO_ESTABLISHED_IMPULSE",
    "NO_PULLBACK",
    "PULLBACK_TOO_LONG",
    "NO_CONTINUATION_CLOSE",
    "COOLDOWN_ACTIVE",
    "INVALID_STOP",
    "STOP_TOO_WIDE",
    "SESSION_DISABLED",
)


def session_bucket(session: str) -> Optional[str]:
    return _SESSION_MAP.get((session or "").strip().lower())


def _trend_ok(bar: dict, direction: str) -> bool:
    want = "UP" if direction == "long" else "DOWN"
    return (
        (bar.get("trend_direction") or "").upper() == want
        and (bar.get("trend_strength") or "").upper() == "STRONG"
        and (bar.get("market_condition") or "").upper() == "TRENDING"
    )


def _find_pullback(window: list, direction: str, *, max_bars: int, min_bars: int) -> Optional[list]:
    """Longest contiguous suffix of `window` (chronological, most recent last)
    moving against `direction`, provided the bar just before that suffix
    shows an established impulse in `direction`. Returns the pullback bars
    (chronological) or None if no qualifying pullback ends at the window's
    tail."""
    if len(window) < min_bars + 1:
        return None

    n = 0
    for bar in reversed(window):
        prev_idx = len(window) - n - 2
        if prev_idx < 0:
            break
        prev_close = float(window[prev_idx]["close"])
        cur_close = float(bar["close"])
        against = (cur_close <= prev_close) if direction == "long" else (cur_close >= prev_close)
        if not against:
            break
        n += 1
        if n > max_bars:
            return None  # too long -- looks like a reversal, not a pullback

    if n < min_bars:
        return None

    impulse_idx = len(window) - n - 1
    if impulse_idx < 0:
        return None
    if not _trend_ok(window[impulse_idx], direction):
        return None

    return window[len(window) - n:]


def detect_candidates(
    *,
    window: list,
    current_bar: dict,
    session: str,
    r_multiple: float = 2.0,
    stop_buffer: float = DEFAULT_STOP_BUFFER_POINTS,
    max_pullback_bars: int = DEFAULT_MAX_PULLBACK_BARS,
    min_pullback_bars: int = DEFAULT_MIN_PULLBACK_BARS,
    bars_since_last_trigger: Optional[dict] = None,
    cooldown_bars: int = DEFAULT_TRIGGER_COOLDOWN_BARS,
) -> list:
    """Pure, stateless-per-call evaluation (the only external state is the
    caller-owned `bars_since_last_trigger` cooldown counter, passed in and
    read-only here). `window` is strictly-prior, already-closed bars
    (chronological, most recent last) -- no lookahead by construction.
    Returns one candidate dict per direction considered (ACCEPTED or
    REJECTED with a reason), so every evaluated event is auditable."""
    out = []
    bucket = session_bucket(session)
    if bucket is None:
        return [{
            "direction": None, "decision": "REJECTED", "rejection_reason": "SESSION_DISABLED",
            "session": session,
        }]

    close = float(current_bar["close"])

    for direction in ("long", "short"):
        base = {"instrument": "MNQ", "timeframe": "5", "session": session, "direction": direction,
                "setup_type": "impulse_pullback_continuation", "current_price": close,
                "trigger_bar": current_bar.get("timestamp") or current_bar.get("ts")}

        if bars_since_last_trigger is not None:
            since = bars_since_last_trigger.get(direction)
            if since is not None and since < cooldown_bars:
                out.append({**base, "decision": "REJECTED", "rejection_reason": "COOLDOWN_ACTIVE"})
                continue

        pullback = _find_pullback(
            window, direction, max_bars=max_pullback_bars, min_bars=min_pullback_bars,
        )
        if pullback is None:
            out.append({**base, "decision": "REJECTED", "rejection_reason": "NO_PULLBACK"})
            continue

        if direction == "long":
            pullback_extreme_high = max(float(b["high"]) for b in pullback)
            pullback_extreme_low = min(float(b["low"]) for b in pullback)
            continuation = close > pullback_extreme_high
            stop = pullback_extreme_low - stop_buffer
        else:
            pullback_extreme_low = min(float(b["low"]) for b in pullback)
            pullback_extreme_high = max(float(b["high"]) for b in pullback)
            continuation = close < pullback_extreme_low
            stop = pullback_extreme_high + stop_buffer

        if not continuation:
            out.append({**base, "decision": "REJECTED", "rejection_reason": "NO_CONTINUATION_CLOSE"})
            continue

        entry = close
        risk = (entry - stop) if direction == "long" else (stop - entry)
        if risk <= 0:
            out.append({**base, "decision": "REJECTED", "rejection_reason": "INVALID_STOP"})
            continue

        target = entry + r_multiple * risk if direction == "long" else entry - r_multiple * risk
        reward = abs(target - entry)

        out.append({
            **base, "decision": "ACCEPTED", "rejection_reason": None,
            "entry": entry, "stop": stop, "target": target,
            "risk_points": risk, "reward_points": reward, "rr": r_multiple,
            "pullback_bars": len(pullback),
        })

    return out
