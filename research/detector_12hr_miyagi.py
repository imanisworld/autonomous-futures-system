"""Pure, stateless detector for the executable 12HR Miyagi setup.

Research/reconciliation only: no strategy-engine, broker, risk, configuration,
or deployment imports. Candle 4 direction is determined exclusively from the
9:30 AM ET open, and the reference stop uses the last *completed* hourly bar.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")
_REQUIRED_BAR_KEYS = ("ts", "open", "high", "low", "close")


def _et_dt(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET)


def _usable_bar(bar: Any) -> bool:
    if not isinstance(bar, dict):
        return False
    if not all(key in bar for key in _REQUIRED_BAR_KEYS):
        return False
    ts = bar.get("ts")
    if not isinstance(ts, datetime) or ts.tzinfo is None:
        return False
    return all(
        isinstance(bar[key], (int, float)) and not isinstance(bar[key], bool)
        for key in ("open", "high", "low", "close")
    )


def _find_exact(bars: list, target: datetime) -> Optional[dict]:
    return next(
        (
            bar
            for bar in bars
            if _usable_bar(bar) and bar["ts"] == target
        ),
        None,
    )


def _inside(inner: dict, outer: dict) -> bool:
    return inner["high"] <= outer["high"] and inner["low"] >= outer["low"]


def _outside(outer: dict, inner: dict) -> bool:
    return outer["high"] > inner["high"] and outer["low"] < inner["low"]


def detect_12hr_miyagi(
    bars_12h: list,
    bars_5m: list,
    bars_60m: list,
    eval_date: date,
    instrument: str,
) -> Optional[dict]:
    """Return the Miyagi setup at ``eval_date`` or ``None``.

    A ``signal=False`` result is reserved for the explicit pre-open outside-bar
    invalidation. Missing data and non-pattern dates fail closed with ``None``.
    """
    if not all(isinstance(value, list) for value in (bars_12h, bars_5m, bars_60m)):
        raise TypeError("bars_12h, bars_5m, and bars_60m must each be a list")

    instrument = str(instrument).upper()
    prior_day = eval_date - timedelta(days=1)
    two_days_prior = eval_date - timedelta(days=2)

    bar_d = _find_exact(bars_12h, _et_dt(eval_date, 4))
    bar_c = _find_exact(bars_12h, _et_dt(prior_day, 16))
    bar_b = _find_exact(bars_12h, _et_dt(prior_day, 4))
    bar_a = _find_exact(bars_12h, _et_dt(two_days_prior, 16))
    bar_z = _find_exact(bars_12h, _et_dt(two_days_prior, 4))
    if any(bar is None for bar in (bar_z, bar_a, bar_b, bar_c, bar_d)):
        return None

    if not _inside(bar_c, bar_b):
        return None
    if not _outside(bar_b, bar_a):
        return None
    if not _inside(bar_a, bar_z):
        return None

    trigger = (bar_c["high"] + bar_c["low"]) / 2
    preopen_start = _et_dt(eval_date, 4)
    open_time = _et_dt(eval_date, 9, 30)
    preopen = sorted(
        (
            bar
            for bar in bars_5m
            if _usable_bar(bar) and preopen_start <= bar["ts"] < open_time
        ),
        key=lambda bar: bar["ts"],
    )
    if not preopen:
        return None

    # A developing 12H candle is outside once its cumulative range has broken
    # both sides of Candle 3. The two breaks may occur on different 5m bars.
    live_high = max(bar["high"] for bar in preopen)
    live_low = min(bar["low"] for bar in preopen)
    if live_high > bar_c["high"] and live_low < bar_c["low"]:
        return {
            "signal": False,
            "invalidation": "CANDLE3_BECAME_OUTSIDE_BAR",
        }

    nine_thirty = _find_exact(bars_5m, open_time)
    if nine_thirty is None:
        return None
    price_at_open = nine_thirty["open"]
    if price_at_open > bar_c["high"]:
        direction = "SHORT"
    elif price_at_open < bar_c["low"]:
        direction = "LONG"
    else:
        return None

    # A 60m bar is completed only after its full hour has elapsed. At 09:30,
    # the 09:00 bar is still forming, so the latest eligible bar is 08:00.
    completed_hourly = sorted(
        (
            bar
            for bar in bars_60m
            if _usable_bar(bar) and bar["ts"] + timedelta(hours=1) <= open_time
        ),
        key=lambda bar: bar["ts"],
    )
    if not completed_hourly:
        return None
    stop_bar = completed_hourly[-1]

    is_long = direction == "LONG"
    return {
        "signal": True,
        "direction": direction,
        "entry_trigger": trigger,
        "stop_reference": stop_bar["low"] if is_long else stop_bar["high"],
        "stop_reference_bar_ts": stop_bar["ts"],
        "target": bar_c["high"] if is_long else bar_c["low"],
        "target_2": bar_b["high"] if is_long else bar_b["low"],
        "setup_bar_ts": bar_d["ts"],
        "entry_window_open": open_time,
        "entry_window_close": None,
        "reference_candle_high": bar_b["high"],
        "reference_candle_low": bar_b["low"],
        "bar_c_high": bar_c["high"],
        "bar_c_low": bar_c["low"],
        "invalidation": None,
        "instrument": instrument,
    }
