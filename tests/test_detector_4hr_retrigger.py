"""Tests for research/detector_4hr_retrigger.py — the pure 4HR Re-Trigger detector.

Covers, per the required test list: calls/puts setups; Monday MNQ/MES Sunday-4PM
reference; QQQ Monday-Friday reference; missing Sunday reference with no
fallback; 4AM inside/outside-bar rejection; 8AM reversal requirement; 5-minute
retrace requiring a close not a wick; break-then-retrace chronological ordering
(a pre-break close-through must not count); retrace deadline before 9:30 AM ET;
price already through the trigger at 9:30; entry window; target; fixed
structural references; timezone/DST behavior; missing bars and malformed
inputs.

The 8AM candle is evaluated via its DEVELOPING state (5-minute bars from 8:00
through 9:30 AM ET only) per the 2026-07-23 operator ruling — there is no
discrete bars_4h 8AM entry for the detector to read, so no fixture here builds
one.

No replay fills, slippage, commissions, broker logic, paper lanes, config, env
vars, or deployment wiring — this module and its tests are detector-only.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from research.detector_4hr_retrigger import detect_4hr_retrigger

ET = ZoneInfo("America/New_York")


def _bar(day: date, hour: int, minute: int, o, h, l, c, v=1000.0):
    return {
        "ts": datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET),
        "open": o, "high": h, "low": l, "close": c, "volume": v, "timeframe": "irrelevant",
    }


# ── Shared fixture builders ────────────────────────────────────────────────────
# Tuesday eval date (ordinary Tue-Fri prior-day path). Prior 4PM candle: Monday
# 4:00-8:00 PM ET, high=100 low=90.
_MON = date(2026, 1, 5)   # Monday
_TUE = date(2026, 1, 6)   # Tuesday


def _prior_4pm(day, high=100.0, low=90.0):
    return _bar(day, 16, 0, (high + low) / 2, high, low, (high + low) / 2)


def _calls_4am(day, high=95.0, low=85.0):
    # 2DOWN vs prior(100,90): high<=100, low<90
    return _bar(day, 4, 0, 90.0, high, low, 88.0)


def _puts_4am(day, high=105.0, low=92.0):
    # 2UP vs prior(100,90): low>=90, high>100
    return _bar(day, 4, 0, 95.0, high, low, 100.0)


def _five_min_seq(day, entries):
    """entries: list of (hour, minute, open, high, low, close)."""
    return [_bar(day, h, m, o, hi, lo, c) for (h, m, o, hi, lo, c) in entries]


def _basic_calls_bundle(day=_TUE, prior_day=_MON, instrument="MNQ"):
    # four_am high=95. Developing 8AM state: no break, then break, then retrace.
    bars_4h = [_prior_4pm(prior_day), _calls_4am(day)]
    bars_5m = _five_min_seq(day, [
        (8, 0, 90, 93, 89, 93),     # high=93 < 95 -> no break yet
        (8, 5, 93, 96, 92, 96),     # BREAK: high=96 > 95; close=96 not below 95 either
        (8, 10, 96, 97, 93, 93),    # AFTER break: close=93 < 95 -> FIRST retrace confirmation
        (8, 15, 93, 94, 90, 91),
        (9, 25, 91, 92, 89, 90),
        (9, 30, 90, 91, 88, 90),    # 9:30 bar, open=90 < 95 -> state OK
    ])
    bars_1h = [_bar(day, 8, 0, 90, 94, 88, 91)]  # last completed 1H bar before 9:30
    return bars_4h, bars_5m, bars_1h


def _basic_puts_bundle(day=_TUE, prior_day=_MON, instrument="MNQ"):
    # four_am low=92. Developing 8AM state: no break, then break, then retrace.
    bars_4h = [_prior_4pm(prior_day), _puts_4am(day)]
    bars_5m = _five_min_seq(day, [
        (8, 0, 95, 96, 92, 91),     # low=92, not strictly < 92 -> no break yet
        (8, 5, 91, 93, 90, 90),     # BREAK: low=90 < 92; close=90 not above 92 either
        (8, 10, 90, 94, 89, 93),    # AFTER break: close=93 > 92 -> FIRST retrace confirmation
        (8, 15, 93, 95, 91, 94),
        (9, 25, 94, 96, 93, 95),
        (9, 30, 95, 97, 94, 96),    # 9:30 bar, open=95 > 92 -> state OK
    ])
    bars_1h = [_bar(day, 8, 0, 95, 99, 93, 97)]
    return bars_4h, bars_5m, bars_1h


# ── 1. calls and puts setups ───────────────────────────────────────────────────

def test_calls_setup_fires_full_signal():
    bars_4h, bars_5m, bars_1h = _basic_calls_bundle()
    result = detect_4hr_retrigger(bars_4h, bars_5m, bars_1h, _TUE, "MNQ")
    assert result is not None
    assert result["signal"] is True
    assert result["direction"] == "LONG"
    assert result["entry_trigger"] == 95.0          # 4AM high
    assert result["invalidation"] is None


def test_puts_setup_fires_full_signal():
    bars_4h, bars_5m, bars_1h = _basic_puts_bundle()
    result = detect_4hr_retrigger(bars_4h, bars_5m, bars_1h, _TUE, "MNQ")
    assert result is not None
    assert result["signal"] is True
    assert result["direction"] == "SHORT"
    assert result["entry_trigger"] == 92.0          # 4AM low


# ── 2. Monday MNQ/MES uses Sunday 4PM reference ────────────────────────────────

def test_monday_mnq_uses_sunday_reference():
    monday = date(2026, 1, 12)   # a Monday
    sunday = monday - timedelta(days=1)
    bars_4h = [_prior_4pm(sunday, high=200.0, low=180.0), _calls_4am(monday, high=195.0, low=175.0)]
    bars_5m = _five_min_seq(monday, [
        (8, 5, 196, 198, 194, 197),   # BREAK: high 198 > 4AM high 195
        (8, 10, 197, 198, 190, 190),  # after break, close 190 < 195 -> retrace
        (9, 30, 190, 191, 188, 190),
    ])
    bars_1h = [_bar(monday, 8, 0, 190, 194, 188, 191)]
    result = detect_4hr_retrigger(bars_4h, bars_5m, bars_1h, monday, "MNQ")
    assert result is not None
    assert result["reference_candle_high"] == 200.0   # from the SUNDAY bar, not Friday


def test_monday_mes_uses_sunday_reference():
    monday = date(2026, 1, 12)
    sunday = monday - timedelta(days=1)
    bars_4h = [_prior_4pm(sunday, high=200.0, low=180.0), _calls_4am(monday, high=195.0, low=175.0)]
    bars_5m = _five_min_seq(monday, [
        (8, 5, 196, 198, 194, 197),
        (8, 10, 197, 198, 190, 190),
        (9, 30, 190, 191, 188, 190),
    ])
    bars_1h = [_bar(monday, 8, 0, 190, 194, 188, 191)]
    result = detect_4hr_retrigger(bars_4h, bars_5m, bars_1h, monday, "MES")
    assert result is not None
    assert result["reference_candle_high"] == 200.0


# ── 3. QQQ Monday uses Friday reference ────────────────────────────────────────

def test_monday_qqq_uses_friday_reference():
    monday = date(2026, 1, 12)
    friday = monday - timedelta(days=3)
    sunday = monday - timedelta(days=1)
    # Plant BOTH a Friday bar (correct ref) and a Sunday bar (must be ignored for QQQ)
    bars_4h = [
        _prior_4pm(friday, high=300.0, low=280.0),
        _prior_4pm(sunday, high=999.0, low=999.0),   # decoy — must not be used
        _calls_4am(monday, high=295.0, low=275.0),
    ]
    bars_5m = _five_min_seq(monday, [
        (8, 5, 296, 298, 294, 297),    # BREAK: high 298 > 4AM high 295
        (8, 10, 297, 298, 290, 290),   # after break, close 290 < 295 -> retrace
        (9, 30, 290, 291, 288, 290),
    ])
    bars_1h = [_bar(monday, 8, 0, 290, 294, 288, 291)]
    result = detect_4hr_retrigger(bars_4h, bars_5m, bars_1h, monday, "QQQ")
    assert result is not None
    assert result["reference_candle_high"] == 300.0   # Friday, never the Sunday decoy


# ── 4. missing Sunday reference — no fallback to Friday ────────────────────────

def test_monday_mnq_missing_sunday_reference_does_not_fall_back_to_friday():
    monday = date(2026, 1, 12)
    friday = monday - timedelta(days=3)
    # Only a Friday bar exists — no Sunday bar at all.
    bars_4h = [
        _prior_4pm(friday, high=300.0, low=280.0),
        _calls_4am(monday, high=295.0, low=275.0),
    ]
    bars_5m = _five_min_seq(monday, [(8, 10, 296, 297, 290, 290), (9, 30, 290, 291, 288, 290)])
    bars_1h = [_bar(monday, 8, 0, 290, 294, 288, 291)]
    result = detect_4hr_retrigger(bars_4h, bars_5m, bars_1h, monday, "MNQ")
    assert result is None   # must NOT substitute Friday for the missing Sunday bar


# ── 5. 4AM inside / outside bar rejection ──────────────────────────────────────

def test_4am_inside_bar_rejected():
    prior = _prior_4pm(_MON, high=100.0, low=90.0)
    inside = _bar(_TUE, 4, 0, 92, 98, 92, 95)   # high<=100, low>=90 -> inside
    bars_4h = [prior, inside]
    result = detect_4hr_retrigger(bars_4h, [], [], _TUE, "MNQ")
    assert result is None


def test_4am_outside_bar_rejected():
    prior = _prior_4pm(_MON, high=100.0, low=90.0)
    outside = _bar(_TUE, 4, 0, 95, 105, 85, 90)  # high>100, low<90 -> outside
    bars_4h = [prior, outside]
    result = detect_4hr_retrigger(bars_4h, [], [], _TUE, "MNQ")
    assert result is None


def test_4am_exact_equality_boundaries_are_inside_not_calls_or_puts():
    """high==prior_high and low==prior_low exactly -> INSIDE (not CALLS, since
    CALLS requires low STRICTLY less than prior low)."""
    prior = _prior_4pm(_MON, high=100.0, low=90.0)
    exact = _bar(_TUE, 4, 0, 95, 100.0, 90.0, 95)
    bars_4h = [prior, exact]
    result = detect_4hr_retrigger(bars_4h, [], [], _TUE, "MNQ")
    assert result is None


# ── 6. 8AM reversal requirement (developing state never breaks the level) ──────

def test_8am_never_breaks_4am_high_rejects_calls_setup():
    prior = _prior_4pm(_MON)
    four_am = _calls_4am(_TUE)  # high=95
    bars_4h = [prior, four_am]
    bars_5m = _five_min_seq(_TUE, [
        (8, 5, 90, 94, 89, 91),      # high=94 < 95 -> no break
        (8, 45, 91, 94.5, 88, 90),   # high=94.5 < 95 -> still no break
        (9, 25, 90, 93, 87, 89),     # high=93 < 95 -> never breaks
    ])
    result = detect_4hr_retrigger(bars_4h, bars_5m, [], _TUE, "MNQ")
    assert result is None


def test_8am_never_breaks_4am_low_rejects_puts_setup():
    prior = _prior_4pm(_MON)
    four_am = _puts_4am(_TUE)  # low=92
    bars_4h = [prior, four_am]
    bars_5m = _five_min_seq(_TUE, [
        (8, 5, 100, 105, 93.0, 96),    # low=93 > 92 -> no break
        (8, 45, 96, 104, 93.5, 95),    # low=93.5 > 92 -> still no break
        (9, 25, 95, 103, 94.0, 94),    # low=94 > 92 -> never breaks
    ])
    result = detect_4hr_retrigger(bars_4h, bars_5m, [], _TUE, "MNQ")
    assert result is None


# ── 7. 5-minute retrace requires a CLOSE, not a wick ───────────────────────────

def test_retrace_wick_only_does_not_confirm():
    """After a genuine break, a later bar whose LOW pokes below the 4AM high
    intrabar, but whose CLOSE stays above it, must NOT confirm the retrace."""
    bars_4h = [_prior_4pm(_MON), _calls_4am(_TUE)]  # four_am high=95
    bars_5m = _five_min_seq(_TUE, [
        (8, 5, 96, 97, 95, 96),    # BREAK: high=97 > 95
        (8, 10, 96, 97, 90, 96),   # AFTER break: low=90 wicks below 95, but CLOSE=96 stays above
        (9, 25, 96, 97, 95, 96),   # never closes below 95
    ])
    result = detect_4hr_retrigger(bars_4h, bars_5m, [_bar(_TUE, 8, 0, 90, 94, 88, 91)], _TUE, "MNQ")
    assert result is None   # never retraced by a close, so no setup at all


def test_retrace_close_confirms():
    bars_4h = [_prior_4pm(_MON), _calls_4am(_TUE)]
    bars_5m = _five_min_seq(_TUE, [
        (8, 5, 96, 97, 95, 96),    # BREAK: high=97 > 95
        (8, 10, 96, 96, 93, 94),   # AFTER break: close=94 < 95 -> confirms
        (9, 30, 94, 95, 93, 94),
    ])
    result = detect_4hr_retrigger(bars_4h, bars_5m, [_bar(_TUE, 8, 0, 90, 94, 88, 91)], _TUE, "MNQ")
    assert result is not None
    assert result["signal"] is True


def test_calls_break_bar_close_can_confirm_same_bar_retrace():
    bars_4h = [_prior_4pm(_MON), _calls_4am(_TUE)]  # four_am high=95
    bars_5m = _five_min_seq(_TUE, [
        (8, 5, 94, 97, 92, 94),    # high breaks 95, then final close is back below 95
        (9, 30, 94, 95, 93, 94),
    ])
    bars_1h = [_bar(_TUE, 8, 0, 90, 94, 88, 91)]
    result = detect_4hr_retrigger(bars_4h, bars_5m, bars_1h, _TUE, "MNQ")
    assert result is not None
    assert result["signal"] is True
    assert result["setup_bar_ts"] == datetime(2026, 1, 6, 8, 5, tzinfo=ET)


def test_puts_break_bar_close_can_confirm_same_bar_retrace():
    bars_4h = [_prior_4pm(_MON), _puts_4am(_TUE)]  # four_am low=92
    bars_5m = _five_min_seq(_TUE, [
        (8, 5, 93, 95, 90, 93),    # low breaks 92, then final close is back above 92
        (9, 30, 93, 95, 92, 94),
    ])
    bars_1h = [_bar(_TUE, 8, 0, 95, 99, 93, 97)]
    result = detect_4hr_retrigger(bars_4h, bars_5m, bars_1h, _TUE, "MNQ")
    assert result is not None
    assert result["signal"] is True
    assert result["setup_bar_ts"] == datetime(2026, 1, 6, 8, 5, tzinfo=ET)


def test_close_through_level_before_any_break_does_not_count_as_retrace():
    """A bar whose CLOSE is already back through the level, occurring BEFORE
    any break bar, must not satisfy the retrace — break must come first,
    chronologically. This is the case an independent (non-sequential)
    break-anywhere / retrace-anywhere check would incorrectly confirm."""
    bars_4h = [_prior_4pm(_MON), _calls_4am(_TUE)]  # four_am high=95
    bars_5m = _five_min_seq(_TUE, [
        (8, 0, 90, 91, 88, 89),     # closes at 89, well below 95 -- but NO break has occurred yet
        (8, 5, 89, 96, 88, 95.5),   # NOW breaks: high=96 > 95; close=95.5 not below 95 either
        (9, 25, 95.5, 96, 94, 95),  # still hasn't closed below 95 after the break
    ])
    result = detect_4hr_retrigger(bars_4h, bars_5m, [_bar(_TUE, 8, 0, 90, 94, 88, 91)], _TUE, "MNQ")
    assert result is None   # the pre-break close-below bar must not be mistaken for a retrace


# ── 8. retrace deadline before 9:30 AM ET ──────────────────────────────────────

def test_retrace_at_or_after_930_does_not_count():
    """A close-below-trigger bar at exactly 9:30 (or later) is outside the
    [8:00, 9:30) window and must not confirm the retrace."""
    bars_4h = [_prior_4pm(_MON), _calls_4am(_TUE)]
    bars_5m = _five_min_seq(_TUE, [
        (8, 5, 96, 97, 94, 96),    # BREAK: high=97>95; stays above 95, no retrace yet
        (9, 30, 96, 97, 90, 90),   # closes below 95 but AT 9:30 -- too late, excluded from window
    ])
    result = detect_4hr_retrigger(bars_4h, bars_5m, [_bar(_TUE, 8, 0, 90, 94, 88, 91)], _TUE, "MNQ")
    assert result is None


# ── 9. price already through the trigger at 9:30 ───────────────────────────────

def test_price_through_trigger_at_930_returns_invalidation_dict_not_none():
    bars_4h, bars_5m, bars_1h = _basic_calls_bundle()
    # Overwrite the 9:30 bar so price has moved back above the 4AM high (95).
    bars_5m = bars_5m[:-1] + [_bar(_TUE, 9, 30, 96, 97, 95, 96)]
    result = detect_4hr_retrigger(bars_4h, bars_5m, bars_1h, _TUE, "MNQ")
    assert result is not None                 # NOT bare None -- an informative dict
    assert result["signal"] is False
    assert result["invalidation"] == "PRICE_THROUGH_TRIGGER_AT_OPEN"


# ── 10. correct entry window ───────────────────────────────────────────────────

def test_entry_window_is_930_to_1100_on_eval_date():
    bars_4h, bars_5m, bars_1h = _basic_calls_bundle()
    result = detect_4hr_retrigger(bars_4h, bars_5m, bars_1h, _TUE, "MNQ")
    assert result["entry_window_open"] == datetime(2026, 1, 6, 9, 30, tzinfo=ET)
    assert result["entry_window_close"] == datetime(2026, 1, 6, 11, 0, tzinfo=ET)


# ── 11. correct target ─────────────────────────────────────────────────────────

def test_target_is_prior_4pm_high_for_calls_and_low_for_puts():
    bars_4h, bars_5m, bars_1h = _basic_calls_bundle()
    calls_result = detect_4hr_retrigger(bars_4h, bars_5m, bars_1h, _TUE, "MNQ")
    assert calls_result["target"] == 100.0   # prior 4PM high

    bars_4h_p, bars_5m_p, bars_1h_p = _basic_puts_bundle()
    puts_result = detect_4hr_retrigger(bars_4h_p, bars_5m_p, bars_1h_p, _TUE, "MNQ")
    assert puts_result["target"] == 90.0     # prior 4PM low


# ── 12. fixed structural references ────────────────────────────────────────────

def test_reference_candle_high_low_match_prior_4pm_bar():
    bars_4h, bars_5m, bars_1h = _basic_calls_bundle()
    result = detect_4hr_retrigger(bars_4h, bars_5m, bars_1h, _TUE, "MNQ")
    assert result["reference_candle_high"] == 100.0
    assert result["reference_candle_low"] == 90.0


def test_stop_reference_uses_last_completed_1h_bar_before_930():
    bars_4h, bars_5m, bars_1h = _basic_calls_bundle()
    # Two 1H bars: 7-8AM and 8-9AM. Only the 8-9AM one is "last completed before 9:30".
    bars_1h = [_bar(_TUE, 7, 0, 80, 84, 78, 82), _bar(_TUE, 8, 0, 82, 94, 88, 91)]
    result = detect_4hr_retrigger(bars_4h, bars_5m, bars_1h, _TUE, "MNQ")
    assert result["stop_reference"] == 88.0   # low of the 8-9AM bar, not the 7-8AM bar
    assert result["stop_reference_bar_ts"] == datetime(2026, 1, 6, 8, 0, tzinfo=ET)


# ── 13. timezone and DST behavior ──────────────────────────────────────────────

def test_dst_spring_forward_boundary():
    """2026-03-08 is the US spring-forward Sunday; 2026-03-09 (Monday) is the
    first EDT trading day. The 4AM/8AM ET wall-clock anchors must resolve
    correctly across the EST->EDT change (zoneinfo handles this; this test
    proves the detector's datetime construction does too)."""
    monday = date(2026, 3, 9)     # first Monday after DST start
    sunday = date(2026, 3, 8)     # the DST-transition Sunday itself
    bars_4h = [_prior_4pm(sunday, high=100.0, low=90.0), _calls_4am(monday)]
    bars_5m = _five_min_seq(monday, [
        (8, 5, 96, 97, 94, 96),    # BREAK: high=97 > 95
        (8, 10, 96, 96, 92, 93),   # AFTER break: close=93 < 95 -> retrace
        (9, 30, 93, 94, 91, 93),
    ])
    bars_1h = [_bar(monday, 8, 0, 90, 94, 88, 91)]
    result = detect_4hr_retrigger(bars_4h, bars_5m, bars_1h, monday, "MNQ")
    assert result is not None
    assert result["entry_window_open"].utcoffset() == timedelta(hours=-4)   # EDT


def test_dst_fall_back_boundary():
    """2026-11-01 is the US fall-back Sunday; 2026-11-02 (Monday) is the first
    EST trading day after the change."""
    monday = date(2026, 11, 2)
    sunday = date(2026, 11, 1)
    bars_4h = [_prior_4pm(sunday, high=100.0, low=90.0), _calls_4am(monday)]
    bars_5m = _five_min_seq(monday, [
        (8, 5, 96, 97, 94, 96),    # BREAK: high=97 > 95
        (8, 10, 96, 96, 92, 93),   # AFTER break: close=93 < 95 -> retrace
        (9, 30, 93, 94, 91, 93),
    ])
    bars_1h = [_bar(monday, 8, 0, 90, 94, 88, 91)]
    result = detect_4hr_retrigger(bars_4h, bars_5m, bars_1h, monday, "MNQ")
    assert result is not None
    assert result["entry_window_open"].utcoffset() == timedelta(hours=-5)   # EST


# ── 14. missing bars and malformed inputs ──────────────────────────────────────

def test_missing_4am_bar_returns_none():
    bars_4h = [_prior_4pm(_MON)]   # no 4AM bar at all
    assert detect_4hr_retrigger(bars_4h, [], [], _TUE, "MNQ") is None


def test_no_developing_5m_bars_in_window_returns_none():
    bars_4h = [_prior_4pm(_MON), _calls_4am(_TUE)]  # 4AM present, no 5-min bars at all
    assert detect_4hr_retrigger(bars_4h, [], [], _TUE, "MNQ") is None


def test_missing_prior_4pm_bar_returns_none():
    bars_4h = [_calls_4am(_TUE)]  # no prior 4PM bar
    assert detect_4hr_retrigger(bars_4h, [], [], _TUE, "MNQ") is None


def test_missing_1h_stop_bar_returns_none():
    bars_4h, bars_5m, _ = _basic_calls_bundle()
    assert detect_4hr_retrigger(bars_4h, bars_5m, [], _TUE, "MNQ") is None


def test_missing_930_bar_returns_none():
    bars_4h, bars_5m, bars_1h = _basic_calls_bundle()
    bars_5m = [b for b in bars_5m if not (b["ts"].hour == 9 and b["ts"].minute == 30)]
    assert detect_4hr_retrigger(bars_4h, bars_5m, bars_1h, _TUE, "MNQ") is None


def test_malformed_bar_entries_are_skipped_not_crashed():
    """A non-dict entry, and a bar with a naive (non-tz-aware) timestamp, must
    both be silently excluded from matching rather than raising."""
    bars_4h, bars_5m, bars_1h = _basic_calls_bundle()
    naive_bar = {"ts": datetime(2026, 1, 6, 4, 0), "open": 1, "high": 1, "low": 1, "close": 1}
    bars_4h_bad = ["not a dict", 12345, naive_bar] + bars_4h
    result = detect_4hr_retrigger(bars_4h_bad, bars_5m, bars_1h, _TUE, "MNQ")
    assert result is not None and result["signal"] is True   # real bars still found despite the junk


def test_wrong_type_for_bars_raises_typeerror():
    with pytest.raises(TypeError):
        detect_4hr_retrigger("not-a-list", [], [], _TUE, "MNQ")


def test_missing_dict_keys_in_a_bar_do_not_crash():
    incomplete = {"ts": datetime(2026, 1, 6, 4, 0, tzinfo=ET), "open": 1}  # missing high/low/close
    bars_4h = [_prior_4pm(_MON), incomplete]
    result = detect_4hr_retrigger(bars_4h, [], [], _TUE, "MNQ")
    assert result is None   # incomplete bar treated as unusable -> 4AM bar "not found"
