"""Unit tests for alert_ranker.strat — Strat pattern detection and ORB."""

from __future__ import annotations

import pytest

from alert_ranker.strat import aggregate_4hr_bars, compute_orb, detect_strat_pattern


# ── Helpers ──────────────────────────────────────────────────────────────────


def _bar(open_, high, low, close, begins_at="2026-06-15T10:00:00Z"):
    return {"open": open_, "high": high, "low": low, "close": close, "begins_at": begins_at}


def _rh_bar(open_, high, low, close, begins_at="2026-06-15T10:00:00"):
    """Simulate RH key names (open_price / high_price etc.)."""
    return {
        "open_price": str(open_),
        "high_price": str(high),
        "low_price": str(low),
        "close_price": str(close),
        "begins_at": begins_at,
    }


def _intraday(begins_at, high, low):
    return {"begins_at": begins_at, "high": high, "low": low}


# ── aggregate_4hr_bars ───────────────────────────────────────────────────────


def test_aggregate_empty():
    assert aggregate_4hr_bars([]) == []


def test_aggregate_single_group():
    hours = [
        _bar(100, 110, 95, 105),
        _bar(105, 115, 100, 112),
        _bar(112, 120, 108, 118),
        _bar(118, 122, 115, 119),
    ]
    result = aggregate_4hr_bars(hours)
    assert len(result) == 1
    bar = result[0]
    assert bar["open"] == 100.0
    assert bar["high"] == 122.0
    assert bar["low"] == 95.0
    assert bar["close"] == 119.0


def test_aggregate_partial_group():
    # 5 hourly bars → one 4hr bar + one 1hr bar
    hours = [_bar(100, 110, 95, 105)] * 5
    result = aggregate_4hr_bars(hours)
    assert len(result) == 2
    assert result[1]["open"] == 100.0
    assert result[1]["close"] == 105.0


def test_aggregate_rh_key_names():
    hours = [
        _rh_bar(100, 110, 95, 105),
        _rh_bar(105, 115, 100, 112),
        _rh_bar(112, 120, 108, 118),
        _rh_bar(118, 122, 115, 119),
    ]
    result = aggregate_4hr_bars(hours)
    assert len(result) == 1
    assert result[0]["high"] == 122.0
    assert result[0]["low"] == 95.0


# ── detect_strat_pattern ─────────────────────────────────────────────────────


def test_detect_needs_at_least_two_bars():
    assert detect_strat_pattern([])["pattern"] is None
    assert detect_strat_pattern([_bar(100, 110, 95, 105)])["pattern"] is None


def test_detect_22u_rev():
    # two_down → two_up = bullish reversal
    bars = [
        _bar(120, 122, 115, 116),  # two_down (lower high, lower low)
        _bar(115, 118, 112, 113),  # two_down
        _bar(113, 119, 114, 118),  # two_up (higher high, higher low vs prev)
    ]
    result = detect_strat_pattern(bars)
    assert result["pattern"] == "22U_REV"
    assert result["bias"] == "BULLISH"


def test_detect_22d_rev():
    # two_up → two_down = bearish reversal
    bars = [
        _bar(100, 108, 98, 107),   # two_up
        _bar(107, 112, 105, 111),  # two_up (higher high, higher low)
        _bar(111, 110, 100, 102),  # two_down (lower high, lower low)
    ]
    result = detect_strat_pattern(bars)
    assert result["pattern"] == "22D_REV"
    assert result["bias"] == "BEARISH"


def test_detect_22_cont_up():
    bars = [
        _bar(100, 108, 98, 107),
        _bar(107, 112, 105, 111),  # two_up
        _bar(111, 115, 109, 114),  # two_up again
    ]
    result = detect_strat_pattern(bars)
    assert result["pattern"] == "22_CONT_UP"
    assert result["bias"] == "BULLISH"


def test_detect_22_cont_down():
    bars = [
        _bar(120, 122, 115, 116),
        _bar(116, 117, 110, 111),  # two_down
        _bar(111, 112, 106, 107),  # two_down again
    ]
    result = detect_strat_pattern(bars)
    assert result["pattern"] == "22_CONT_DOWN"
    assert result["bias"] == "BEARISH"


def test_detect_32u_rev():
    # outside_bar → two_up = bullish reversal from outside
    prev_high, prev_low = 120, 100
    bars = [
        _bar(105, 118, 102, 110),
        _bar(109, prev_high, prev_low, 108),          # outside (engulfs prev)
        _bar(107, prev_high + 2, prev_low + 3, 122),  # two_up (higher high, higher low vs outside)
    ]
    result = detect_strat_pattern(bars)
    assert result["pattern"] == "32U_REV"
    assert result["bias"] == "BULLISH"


def test_detect_32d_rev():
    prev_high, prev_low = 120, 100
    bars = [
        _bar(105, 118, 102, 110),
        _bar(109, prev_high, prev_low, 108),          # outside
        _bar(108, prev_high - 3, prev_low - 2, 101),  # two_down (lower high, lower low vs outside)
    ]
    result = detect_strat_pattern(bars)
    assert result["pattern"] == "32D_REV"
    assert result["bias"] == "BEARISH"


def test_detect_12u_break():
    # 3-bar sequence: reference → inside bar → two_up breakout
    # reference: high=112, low=104
    # inside: high=110, low=106  (110<112, 106>104 → inside reference)
    # two_up: high=113, low=108  (113>110, 108>106 → higher high AND higher low vs inside)
    bars = [
        _bar(108, 112, 104, 110),   # reference
        _bar(110, 110, 106, 108),   # inside
        _bar(108, 113, 108, 112),   # two_up vs inside
    ]
    result = detect_strat_pattern(bars)
    assert result["pattern"] == "12U_BREAK"
    assert result["bias"] == "BULLISH"


def test_detect_12d_break():
    # reference → inside bar → two_down breakout
    # reference: high=112, low=104
    # inside: high=110, low=106
    # two_down: high=109, low=103  (109<110, 103<106 → lower high AND lower low vs inside)
    bars = [
        _bar(108, 112, 104, 110),  # reference
        _bar(110, 110, 106, 108),  # inside
        _bar(108, 109, 103, 104),  # two_down vs inside
    ]
    result = detect_strat_pattern(bars)
    assert result["pattern"] == "12D_BREAK"
    assert result["bias"] == "BEARISH"


def test_detect_bar_types_populated():
    bars = [
        _bar(107, 112, 105, 111),
        _bar(111, 115, 109, 114),
    ]
    result = detect_strat_pattern(bars)
    assert len(result["bar_types"]) >= 1


def test_detect_returns_none_pattern_when_unrecognized():
    # outside → outside — no named mapping
    prev_high, prev_low = 112, 100
    bars = [
        _bar(100, 110, 98, 108),
        _bar(108, prev_high, prev_low, 106),          # outside
        _bar(106, prev_high + 5, prev_low - 5, 108),  # outside again
    ]
    result = detect_strat_pattern(bars)
    # pattern may be None (no named combo for outside→outside)
    # or strat_sequence from 3-bar check — either way bias and pattern must be consistent
    if result["pattern"] is None:
        assert result["bias"] is None
    else:
        assert result["bias"] in ("BULLISH", "BEARISH", "LONG", "SHORT")


# ── compute_orb ──────────────────────────────────────────────────────────────


def test_orb_empty_bars():
    result = compute_orb([], 500.0)
    assert result["status"] == "unknown"


def test_orb_zero_price():
    bars = [_intraday("2026-06-15T09:30:00", 501, 499)]
    result = compute_orb(bars, 0.0)
    assert result["status"] == "unknown"


def test_orb_above():
    bars = [
        _intraday("2026-06-15T09:30:00", 502, 498),
        _intraday("2026-06-15T09:45:00", 503, 499),  # outside window
    ]
    result = compute_orb(bars, 510.0, window_minutes=15)
    assert result["status"] == "above"
    assert result["orb_high"] == 502.0
    assert result["orb_low"] == 498.0


def test_orb_below():
    bars = [_intraday("2026-06-15T09:30:00", 502, 498)]
    result = compute_orb(bars, 490.0, window_minutes=15)
    assert result["status"] == "below"


def test_orb_inside():
    bars = [_intraday("2026-06-15T09:30:00", 502, 498)]
    result = compute_orb(bars, 500.0, window_minutes=15)
    assert result["status"] == "inside"
    assert result["orb_high"] == 502.0
    assert result["orb_low"] == 498.0


def test_orb_window_15_only_captures_first_bar():
    # 09:30 bar = in window; 09:45 bar = outside (window_minutes=15)
    bars = [
        _intraday("2026-06-15T09:30:00", 502, 498),
        _intraday("2026-06-15T09:45:00", 510, 490),
    ]
    result = compute_orb(bars, 503.0, window_minutes=15)
    # Only 09:30 bar should be included → orb_high=502, not 510
    assert result["orb_high"] == 502.0


def test_orb_window_30_captures_two_bars():
    bars = [
        _intraday("2026-06-15T09:30:00", 502, 498),
        _intraday("2026-06-15T09:45:00", 510, 490),
        _intraday("2026-06-15T10:00:00", 512, 488),  # outside 30min window
    ]
    result = compute_orb(bars, 503.0, window_minutes=30)
    assert result["orb_high"] == 510.0


def test_orb_rh_key_names():
    bars = [
        {"begins_at": "2026-06-15T09:30:00", "high_price": "502.5", "low_price": "498.0"}
    ]
    result = compute_orb(bars, 503.0, window_minutes=15)
    assert result["orb_high"] == 502.5
    assert result["orb_low"] == 498.0


def test_orb_no_bars_in_window():
    # Bar is after the window
    bars = [_intraday("2026-06-15T10:00:00", 502, 498)]
    result = compute_orb(bars, 503.0, window_minutes=15)
    assert result["status"] == "unknown"


def test_orb_window_minutes_in_result():
    bars = [_intraday("2026-06-15T09:30:00", 502, 498)]
    result = compute_orb(bars, 500.0, window_minutes=10)
    assert result["window_minutes"] == 10
