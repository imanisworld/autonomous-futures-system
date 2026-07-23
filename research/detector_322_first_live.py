"""Pure, stateless detector for the MNQ 60M 3-2-2 First Live strategy."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")
_REQUIRED = ("ts", "open", "high", "low", "close")


def _et_dt(day: date, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=ET)


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


def detect_322_first_live(
    bars_60m: list,
    eval_date: date,
    instrument: str,
) -> Optional[dict]:
    """Detect the completed 7AM/8AM/9AM pattern and 10AM live break."""
    if not isinstance(bars_60m, list):
        raise TypeError("bars_60m must be a list")
    if str(instrument).upper() != "MNQ":
        return None

    seven = _find(bars_60m, _et_dt(eval_date, 7))
    eight = _find(bars_60m, _et_dt(eval_date, 8))
    nine = _find(bars_60m, _et_dt(eval_date, 9))
    ten = _find(bars_60m, _et_dt(eval_date, 10))
    if any(bar is None for bar in (seven, eight, nine, ten)):
        return None

    if not (eight["high"] > seven["high"] and eight["low"] < seven["low"]):
        return None

    nine_high_broke = nine["high"] > eight["high"]
    nine_low_broke = nine["low"] < eight["low"]
    if nine_high_broke and not nine_low_broke:
        direction = "SHORT"
        trigger = nine["low"]
        stop = nine["high"]
        target = eight["low"]
        gap_open = ten["open"] < trigger
        broke = ten["low"] < trigger
    elif nine_low_broke and not nine_high_broke:
        direction = "LONG"
        trigger = nine["high"]
        stop = nine["low"]
        target = eight["high"]
        gap_open = ten["open"] > trigger
        broke = ten["high"] > trigger
    else:
        return None

    if nine["high"] == nine["low"]:
        return None
    if not gap_open and not broke:
        return {"signal": False, "invalidation": "NO_BREAK_BY_11AM"}

    # Gap-open execution is an explicit rule: the opening price is the fill.
    # Otherwise First Live fills at the crossed 9AM boundary.
    entry_price = ten["open"] if gap_open else trigger
    return {
        "signal": True,
        "direction": direction,
        "entry_trigger": trigger,
        "entry_price": entry_price,
        "entry_bar_ts": ten["ts"],
        "gap_open": gap_open,
        "stop_reference": stop,
        "stop_reference_bar_ts": nine["ts"],
        "target": target,
        "setup_bar_ts": nine["ts"],
        "entry_window_open": ten["ts"],
        "entry_window_close": _et_dt(eval_date, 11),
        "nine_am_range_points": nine["high"] - nine["low"],
        "reference_candle_high": eight["high"],
        "reference_candle_low": eight["low"],
        "invalidation": None,
    }
