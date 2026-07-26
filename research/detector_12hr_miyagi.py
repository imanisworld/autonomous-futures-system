"""Pure, stateless detector for the 12HR Miyagi 1-3-1 reversal strategy.

Implements `docs/strategy-rules/Detector_Specifications.md`'s "Detector 2 --
12HR Miyagi" section verbatim (cross-checked against
`docs/strategy-rules/12HR_Miyagi_Rules.md`, the authoritative human rules doc,
sections 3-8 and 12 -- no contradiction found between the two documents).

Research-only: no imports of runtime/execution/strategy code. Mirrors the
`_usable`/`_find` helper pattern established by
`research/detector_322_first_live.py` (the immediate structural precedent for
this strategy-scoped detector build).

Candle naming (chronological): Bar A -> Bar B -> Bar C -> Bar D (live).
  Bar D (live):        12H bar opening at eval_date          4:00 AM ET
  Bar C (inside,  #3):  12H bar opening at (eval_date - 1)    4:00 PM ET
  Bar B (outside, #2):  12H bar opening at (eval_date - 1)    4:00 AM ET
  Bar A (inside,  #1):  12H bar opening at (eval_date - 2)    4:00 PM ET
  Bar Z (pre-Bar-A):    12H bar opening at (eval_date - 2)    4:00 AM ET
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")
_REQUIRED = ("ts", "open", "high", "low", "close")


def _et_dt(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET)


def _usable(bar: Any) -> bool:
    if not isinstance(bar, dict) or not all(key in bar for key in _REQUIRED):
        return False
    if not isinstance(bar["ts"], datetime) or bar["ts"].tzinfo is None:
        return False
    return all(
        isinstance(bar[key], (int, float)) and not isinstance(bar[key], bool)
        for key in ("open", "high", "low", "close")
    )


def _find(bars: list, target: datetime) -> Optional[dict]:
    return next(
        (bar for bar in bars if _usable(bar) and bar["ts"] == target),
        None,
    )


def detect_12hr_miyagi(
    bars_12h: list,
    bars_5m: list,
    bars_60m: list,
    eval_date: date,
    instrument: str,
) -> Optional[dict]:
    """Detect the 1-3-1 12-hour pattern and its 9:30 AM ET directional confirmation.

    `instrument` is accepted per the canonical function signature but the
    canonical spec (Detector_Specifications.md Detector 2) has no
    instrument-gating step (unlike Detector 3's MNQ-only Step 0) -- it is not
    used to alter detection logic here, matching the spec verbatim.
    """
    if not isinstance(bars_12h, list):
        raise TypeError("bars_12h must be a list")
    if not isinstance(bars_5m, list):
        raise TypeError("bars_5m must be a list")
    if not isinstance(bars_60m, list):
        raise TypeError("bars_60m must be a list")
    del instrument  # unused: no instrument-gating step in the canonical spec

    bar_d_ts = _et_dt(eval_date, 4)
    bar_c_ts = _et_dt(eval_date - timedelta(days=1), 16)
    bar_b_ts = _et_dt(eval_date - timedelta(days=1), 4)
    bar_a_ts = _et_dt(eval_date - timedelta(days=2), 16)
    bar_z_ts = _et_dt(eval_date - timedelta(days=2), 4)

    bar_d = _find(bars_12h, bar_d_ts)
    bar_c = _find(bars_12h, bar_c_ts)
    bar_b = _find(bars_12h, bar_b_ts)
    bar_a = _find(bars_12h, bar_a_ts)
    if any(bar is None for bar in (bar_a, bar_b, bar_c, bar_d)):
        return None

    # Step 1 -- Bar C inside Bar B
    if not (bar_c["high"] <= bar_b["high"] and bar_c["low"] >= bar_b["low"]):
        return None

    # Step 2 -- Bar B outside Bar A
    if not (bar_b["high"] > bar_a["high"] and bar_b["low"] < bar_a["low"]):
        return None

    # Step 3 -- Bar A inside Bar Z
    bar_z = _find(bars_12h, bar_z_ts)
    if bar_z is None:
        return None
    if not (bar_a["high"] <= bar_z["high"] and bar_a["low"] >= bar_z["low"]):
        return None

    # Step 4 -- trigger level
    bar_c_high = bar_c["high"]
    bar_c_low = bar_c["low"]
    trigger = (bar_c_high + bar_c_low) / 2.0

    # Step 5 -- Bar C integrity before 9:30 AM ET using 5-minute bars.
    # Single-bar engulf test: one bar's own high AND low both breach Bar C's
    # range -- not a window-aggregate breach.
    window_start = _et_dt(eval_date, 4, 0)
    window_end = _et_dt(eval_date, 9, 30)
    premarket_bars = [
        bar
        for bar in bars_5m
        if _usable(bar) and window_start <= bar["ts"] < window_end
    ]
    for bar in premarket_bars:
        if bar["high"] > bar_c_high and bar["low"] < bar_c_low:
            return {"signal": False, "invalidation": "CANDLE3_BECAME_OUTSIDE_BAR"}

    # Step 6 -- confirm Bar D direction at 9:30 AM ET
    nine_thirty = _find(bars_5m, window_end)
    if nine_thirty is None:
        return None
    price_at_open = nine_thirty["open"]
    if price_at_open > bar_c_high:
        direction = "SHORT"
    elif price_at_open < bar_c_low:
        direction = "LONG"
    else:
        # Between Bar C high/low (inclusive of exact equality) -> no setup.
        return None

    # Step 7 -- stop reference from 60-minute bars
    prior_60m = sorted(
        (bar for bar in bars_60m if _usable(bar) and bar["ts"] < window_end),
        key=lambda bar: bar["ts"],
    )
    if not prior_60m:
        return None
    stop_bar = prior_60m[-1]
    stop_price = stop_bar["low"] if direction == "LONG" else stop_bar["high"]

    # Step 8 -- targets
    t1_price = bar_c_high if direction == "LONG" else bar_c_low
    t2_price = bar_b["high"] if direction == "LONG" else bar_b["low"]

    # Step 9 -- return signal
    return {
        "signal": True,
        "direction": direction,
        "entry_trigger": trigger,
        "stop_reference": stop_price,
        "stop_reference_bar_ts": stop_bar["ts"],
        "target": t1_price,
        "target_2": t2_price,
        "setup_bar_ts": bar_d["ts"],
        "entry_window_open": window_end,
        "entry_window_close": None,
        "reference_candle_high": bar_b["high"],
        "reference_candle_low": bar_b["low"],
        "bar_c_high": bar_c_high,
        "bar_c_low": bar_c_low,
        "invalidation": None,
    }
