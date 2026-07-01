"""Strat pattern detection and ORB computation for the options advisory scanner.

Wraps strategy.strat_classifier (shared with the futures system) and adds:
  - 4-hour bar aggregation from hourly RH historicals
  - Named pattern mapping to user-facing names (22 Rev, 32 Rev, continues)
  - ORB computation from intraday bars

No imports from execution.*, risk.*, or risk_engine.
"""

from __future__ import annotations

from typing import Any

from strategy.strat_classifier import (
    INSIDE_BAR,
    OUTSIDE_BAR,
    TWO_DOWN,
    TWO_UP,
    classify_from_ohlc,
)


def aggregate_4hr_bars(hourly_bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate hourly OHLC bars into 4-hour candles (groups of 4, chronological).

    Each input bar must have: begins_at, and high/high_price, low/low_price,
    open/open_price, close/close_price in any combination.
    """
    if not hourly_bars:
        return []

    def _f(bar: dict[str, Any], key: str, alt: str) -> float:
        return float(bar.get(key) or bar.get(alt) or 0)

    result = []
    for i in range(0, len(hourly_bars), 4):
        group = hourly_bars[i : i + 4]
        if not group:
            break
        result.append(
            {
                "begins_at": group[0].get("begins_at", ""),
                "open": _f(group[0], "open_price", "open"),
                "high": max(_f(b, "high_price", "high") for b in group),
                "low": min(_f(b, "low_price", "low") for b in group),
                "close": _f(group[-1], "close_price", "close"),
            }
        )
    return result


def detect_strat_pattern(bars_4hr: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify the last three 4-hour bars and return a named Strat pattern.

    Named patterns (user-facing):
      22D_REV / 22U_REV  — 2-bar reversal
      32D_REV / 32U_REV  — outside-bar reversal
      22_CONT_UP / 22_CONT_DOWN — continuation
      212_UP / 212_DOWN  — classic 2-1-2
      12U_BREAK / 12D_BREAK — inside-bar breakout

    Returns {pattern, bias, bar_types, strat_sequence}.
    pattern is None when fewer than 2 bars are available.
    """
    empty: dict[str, Any] = {
        "pattern": None,
        "bias": None,
        "bar_types": [],
        "strat_sequence": None,
    }
    if len(bars_4hr) < 2:
        return empty

    curr = bars_4hr[-1]
    prev = bars_4hr[-2]
    two_back = bars_4hr[-3] if len(bars_4hr) >= 3 else None

    ctx = classify_from_ohlc(
        current_high=float(curr["high"]),
        current_low=float(curr["low"]),
        previous_high=float(prev["high"]),
        previous_low=float(prev["low"]),
        two_bars_back_high=float(two_back["high"]) if two_back else None,
        two_bars_back_low=float(two_back["low"]) if two_back else None,
    )

    bar_types = [
        t
        for t in [ctx.two_bars_back_type, ctx.previous_bar_type, ctx.current_bar_type]
        if t is not None
    ]

    pattern: str | None = None
    bias: str | None = None

    # 3-bar named sequences take priority (212 is the most powerful setup)
    if ctx.strat_sequence == "strat_212":
        pattern = "212_UP" if ctx.strat_direction == "LONG" else "212_DOWN"
        bias = ctx.strat_direction
    elif ctx.previous_bar_type and ctx.current_bar_type:
        p, c = ctx.previous_bar_type, ctx.current_bar_type
        _map: dict[tuple[str, str], tuple[str, str]] = {
            (TWO_UP, TWO_DOWN): ("22D_REV", "BEARISH"),
            (TWO_DOWN, TWO_UP): ("22U_REV", "BULLISH"),
            (OUTSIDE_BAR, TWO_UP): ("32U_REV", "BULLISH"),
            (OUTSIDE_BAR, TWO_DOWN): ("32D_REV", "BEARISH"),
            (TWO_UP, TWO_UP): ("22_CONT_UP", "BULLISH"),
            (TWO_DOWN, TWO_DOWN): ("22_CONT_DOWN", "BEARISH"),
            (INSIDE_BAR, TWO_UP): ("12U_BREAK", "BULLISH"),
            (INSIDE_BAR, TWO_DOWN): ("12D_BREAK", "BEARISH"),
        }
        if (p, c) in _map:
            pattern, bias = _map[(p, c)]

    return {
        "pattern": pattern,
        "bias": bias,
        "bar_types": bar_types,
        "strat_sequence": ctx.strat_sequence,
    }


def compute_orb(
    intraday_bars: list[dict[str, Any]],
    current_price: float,
    *,
    window_minutes: int = 15,
    session_open: str = "09:30",
) -> dict[str, Any]:
    """Compute opening range and price status from intraday bars.

    intraday_bars: list with begins_at (ISO timestamp) and high/low fields.
    window_minutes: ORB window — 10, 15, or 30 work well for equities.
    session_open: HH:MM of market open in the bar timestamps' timezone (NY = '09:30').

    Status values: 'above' | 'below' | 'inside' | 'unknown'
    """
    unknown: dict[str, Any] = {
        "orb_high": None,
        "orb_low": None,
        "window_minutes": window_minutes,
        "status": "unknown",
    }
    if not intraday_bars or current_price <= 0:
        return unknown

    try:
        open_h, open_m = (int(x) for x in session_open.split(":"))
    except ValueError:
        return unknown

    open_mins = open_h * 60 + open_m
    end_mins = open_mins + window_minutes

    orb_bars = []
    for bar in intraday_bars:
        begins_at = str(bar.get("begins_at", ""))
        try:
            time_str = begins_at.split("T")[-1] if "T" in begins_at else begins_at
            bar_h, bar_m = int(time_str[:2]), int(time_str[3:5])
        except (ValueError, IndexError):
            continue
        bar_mins = bar_h * 60 + bar_m
        if open_mins <= bar_mins < end_mins:
            orb_bars.append(bar)

    if not orb_bars:
        return unknown

    def _h(b: dict[str, Any]) -> float:
        return float(b.get("high_price", b.get("high", 0)) or 0)

    def _l(b: dict[str, Any]) -> float:
        return float(b.get("low_price", b.get("low", 0)) or 0)

    orb_high = max(_h(b) for b in orb_bars)
    orb_low = min(_l(b) for b in orb_bars)

    if orb_high <= 0 or orb_low <= 0:
        return unknown

    if current_price > orb_high:
        status = "above"
    elif current_price < orb_low:
        status = "below"
    else:
        status = "inside"

    return {
        "orb_high": orb_high,
        "orb_low": orb_low,
        "window_minutes": window_minutes,
        "status": status,
    }
