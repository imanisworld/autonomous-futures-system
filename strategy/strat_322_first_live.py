"""Canonical executable state machine for MNQ 60M 3-2-2 First Live.

Rules: docs/strategy-rules/60M_322_FirstLive_Rules.md
Evidence: docs/strategy-rules/60M_322_EXPANDED_EVIDENCE_2026-07-26.md
          (34 candidates, 21 fills, 20 resolved, net $1,595.70, PF 10.36)

The module is deliberately pure: callers provide already-arrived 5-minute
bars and the last persisted state, and receive the next state plus an
optional entry candidate — same contract as strategy/four_hr_retrigger.py,
whose ``aggregate_et_bars`` helper this module reuses verbatim to build
causal 60-minute bars from the 5-minute stream.

Rule-parity note (verified against the canonical evidence's own generation
path, docs/strategy-rules/60M_322_EXPANDED_EVIDENCE_2026-07-26.md §0.1-0.3):
the evidence was produced by ``research/replay_322_honest_fill.py``, which
recovers the entry as the FIRST 5-minute bar in [10:00, 11:00) ET whose
high/low crosses the 9AM trigger (or the exact 10:00 bar's open on a gap) —
NOT ``research/detector_322_first_live.py``'s simpler completed-60m-bar
range check, which is lookahead-unsafe for live use (it would require
waiting for the 10AM candle to CLOSE at 11:00 to know whether it crossed
the trigger, then retroactively claiming a same-hour fill). This module
follows the honest-fill replay's 5-minute-granularity entry recovery, the
same fidelity the evidence itself was measured at — not the detector's
end-of-hour shortcut. Setup detection (7AM/8AM/9AM classification) uses
only fully-closed 60-minute bars, which carries no such hazard.

Known fidelity limit (documented, not silently approximated): the rules
text ("no candle close required — enter on the first live break") implies
tick-level reaction. Both the canonical evidence and this live build
resolve entries at 5-minute granularity — a live/demo fill can differ from
a hypothetical tick-level fill by up to just under 5 minutes of price
movement within the triggering bar. This is evidence-consistent, not a new
gap introduced by this module.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

from strategy.four_hr_retrigger import _as_bar, aggregate_et_bars

ET = ZoneInfo("America/New_York")
_SUPPORTED = {"MNQ"}  # rules doc §1: "MNQ ONLY" — hard rule, no exceptions


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET)


def _exact(bars: list[dict], target: datetime) -> Optional[dict]:
    return next((bar for bar in bars if bar["ts"] == target), None)


def _terminal(day: date, reason: str, *, status: str = "INVALIDATED") -> dict:
    return {
        "trading_date": day.isoformat(),
        "status": status,
        "invalidation": reason,
    }


def advance_strat_322_first_live(
    *,
    bars_5m: Iterable[dict],
    current_bar_ts: datetime,
    instrument: str,
    persisted_state: Optional[dict] = None,
) -> tuple[dict, Optional[dict]]:
    """Advance one completed 5-minute evaluation.

    ``current_bar_ts`` is the current bar's open timestamp. Entry time is its
    close timestamp, matching the executable bar-arrival clock (same
    convention as ``advance_4hr_retrigger``).
    """
    root = str(instrument or "").upper().replace("1!", "").replace("2!", "")
    current_open = current_bar_ts.astimezone(ET)
    day = current_open.date()
    if root not in _SUPPORTED:
        return _terminal(day, "UNSUPPORTED_INSTRUMENT"), None

    previous = dict(persisted_state or {})
    if previous.get("trading_date") != day.isoformat():
        previous = {}

    available: list[dict] = []
    current_close = current_open + timedelta(minutes=5)
    for raw in bars_5m:
        bar = _as_bar(raw)
        if bar is not None and bar["ts"] + timedelta(minutes=5) <= current_close:
            available.append(bar)
    available.sort(key=lambda bar: bar["ts"])

    status = previous.get("status")
    if status in {"TRIGGERED", "INVALIDATED", "EXPIRED"}:
        return previous, None

    window_open = _at(day, 10, 0)
    window_close = _at(day, 11, 0)

    if status != "ARMED":
        # Setup detection uses only fully-closed 60m bars (7AM closes 8:00,
        # 8AM closes 9:00, 9AM closes 10:00 = window_open), but must evaluate
        # EXACTLY ONCE, at exactly current_open == window_open — the same
        # one-shot exact-boundary gate advance_4hr_retrigger uses for its own
        # 09:30 setup check. Evaluating on every bar leading up to that
        # boundary would read a PARTIAL 9AM bucket (aggregate_et_bars has no
        # "bucket complete" concept — it aggregates whatever is available)
        # and could latch a spurious terminal state from incomplete data.
        if current_open < window_open:
            return {
                "trading_date": day.isoformat(),
                "status": "FORMING",
                "invalidation": None,
            }, None
        if current_open > window_open:
            return _terminal(day, "SETUP_NOT_ESTABLISHED_BY_1000"), None

        bars_60m = aggregate_et_bars(available, 60)
        seven = _exact(bars_60m, _at(day, 7, 0))
        eight = _exact(bars_60m, _at(day, 8, 0))
        nine = _exact(bars_60m, _at(day, 9, 0))
        if seven is None or eight is None or nine is None:
            return _terminal(day, "REFERENCE_DATA_MISSING"), None

        # Step 1: 8AM must be an outside bar relative to 7AM.
        if not (eight["high"] > seven["high"] and eight["low"] < seven["low"]):
            return _terminal(day, "EIGHT_AM_NOT_OUTSIDE_BAR"), None

        # Step 2: 9AM must be directional (2U or 2D) relative to 8AM.
        nine_high_broke = nine["high"] > eight["high"]
        nine_low_broke = nine["low"] < eight["low"]
        if nine_high_broke and not nine_low_broke:
            direction = "SHORT"
            trigger = nine["low"]
            stop = nine["high"]
        elif nine_low_broke and not nine_high_broke:
            direction = "LONG"
            trigger = nine["high"]
            stop = nine["low"]
        else:
            return _terminal(day, "NINE_AM_NOT_DIRECTIONAL"), None
        if nine["high"] == nine["low"]:
            return _terminal(day, "NINE_AM_ZERO_RANGE"), None

        target = eight["low"] if direction == "SHORT" else eight["high"]

        previous = {
            "trading_date": day.isoformat(),
            "status": "ARMED",
            "direction": direction,
            "trigger": trigger,
            "stop": stop,
            "target": target,
            "eight_am_high": eight["high"],
            "eight_am_low": eight["low"],
            "nine_am_range_points": round(nine["high"] - nine["low"], 4),
            "setup_bar_ts": nine["ts"].isoformat(),
            "expires_at": window_close.isoformat(),
            "invalidation": None,
        }

    if current_open >= window_close:
        expired = dict(previous)
        expired.update(status="EXPIRED", invalidation="NO_BREAK_BY_11AM")
        return expired, None
    if current_open < window_open:
        return previous, None

    # Step 3 (rules §4): First Live entry — the first 5-minute bar in
    # [10:00, 11:00) ET whose high/low crosses the opposite 9AM boundary,
    # or the exact 10:00 bar's OPEN if it gaps through without trading
    # through it tick by tick. Both are causal: only ``available`` bars
    # (already fully closed as of current_bar_ts) are ever inspected.
    current = _exact(available, current_open)
    if current is None:
        return previous, None
    direction = str(previous["direction"])
    trigger = float(previous["trigger"])
    is_window_open_bar = current_open == window_open
    gap_open = is_window_open_bar and (
        current["open"] < trigger if direction == "SHORT" else current["open"] > trigger
    )
    crossed = (
        current["low"] < trigger if direction == "SHORT" else current["high"] > trigger
    )
    if not (gap_open or crossed):
        return previous, None

    entry_price = float(current["open"]) if gap_open else trigger
    stop = float(previous["stop"])
    target = float(previous["target"])
    bracket_valid = (
        target < entry_price < stop
        if direction == "SHORT"
        else stop < entry_price < target
    )
    if not bracket_valid:
        invalid = dict(previous)
        invalid.update(status="INVALIDATED", invalidation="INVALID_ENTRY_BRACKET")
        return invalid, None

    triggered_state = dict(previous)
    triggered_state.update(
        status="TRIGGERED",
        entry_time=current_close.isoformat(),
        entry_price=entry_price,
        gap_open=gap_open,
    )
    candidate = {
        "direction": direction,
        "entry": entry_price,
        "stop": stop,
        "target": target,
        "entry_time": current_close,
        "gap_open": gap_open,
        "state": triggered_state,
    }
    return triggered_state, candidate
