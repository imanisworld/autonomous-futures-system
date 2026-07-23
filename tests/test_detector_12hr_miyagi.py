"""Focused tests for the pure 12HR Miyagi detector."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from research.detector_12hr_miyagi import detect_12hr_miyagi


ET = ZoneInfo("America/New_York")
EVAL = date(2026, 1, 8)


def bar(day, hour, minute, o, h, l, c):
    return {
        "ts": datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 100,
    }


def bundle(open_930=112, preopen=None):
    d1 = date(2026, 1, 6)
    d2 = date(2026, 1, 7)
    bars_12h = [
        bar(d1, 4, 0, 100, 120, 80, 100),   # Z
        bar(d1, 16, 0, 100, 115, 85, 105),  # A: inside Z
        bar(d2, 4, 0, 105, 125, 75, 100),   # B: outside A
        bar(d2, 16, 0, 100, 110, 90, 100),  # C: inside B
        bar(EVAL, 4, 0, 100, 130, 70, 100), # D: values deliberately ignored
    ]
    bars_5m = preopen or [
        bar(EVAL, 4, 0, 100, 105, 95, 102),
        bar(EVAL, 9, 25, 111, 114, 108, 112),
    ]
    bars_5m = bars_5m + [bar(EVAL, 9, 30, open_930, open_930 + 2, open_930 - 2, open_930)]
    bars_60m = [
        bar(EVAL, 8, 0, 100, 108, 92, 104),
        bar(EVAL, 9, 0, 104, 120, 101, 115),  # still forming at 09:30; must be ignored
    ]
    return bars_12h, bars_5m, bars_60m


def detect(open_930=112, preopen=None):
    return detect_12hr_miyagi(*bundle(open_930, preopen), EVAL, "mnq")


def test_short_signal_uses_930_open_above_candle3_high():
    result = detect(112)
    assert result["signal"] is True
    assert result["direction"] == "SHORT"
    assert result["entry_trigger"] == 100
    assert result["target"] == 90
    assert result["target_2"] == 75


def test_long_signal_uses_930_open_below_candle3_low():
    result = detect(88)
    assert result["signal"] is True
    assert result["direction"] == "LONG"
    assert result["target"] == 110
    assert result["target_2"] == 125


@pytest.mark.parametrize("price", [90, 100, 110])
def test_930_open_equal_to_or_inside_candle3_range_is_no_setup(price):
    assert detect(price) is None


def test_intrabar_break_before_930_does_not_define_direction():
    preopen = [
        bar(EVAL, 4, 0, 100, 115, 95, 105),  # briefly above C high
        bar(EVAL, 9, 25, 105, 108, 98, 100),
    ]
    assert detect(100, preopen) is None


def test_cumulative_outside_range_across_separate_bars_invalidates():
    preopen = [
        bar(EVAL, 4, 0, 100, 112, 95, 105),  # breaks C high only
        bar(EVAL, 9, 25, 95, 105, 88, 92),   # later breaks C low only
    ]
    result = detect(112, preopen)
    assert result == {
        "signal": False,
        "invalidation": "CANDLE3_BECAME_OUTSIDE_BAR",
    }


def test_single_bar_outside_invalidates():
    preopen = [bar(EVAL, 8, 0, 100, 112, 88, 100)]
    result = detect(112, preopen)
    assert result["signal"] is False
    assert result["invalidation"] == "CANDLE3_BECAME_OUTSIDE_BAR"


def test_stop_uses_last_completed_60m_bar_not_forming_9am_bar():
    short = detect(112)
    assert short["stop_reference"] == 108
    assert short["stop_reference_bar_ts"] == datetime(2026, 1, 8, 8, 0, tzinfo=ET)
    long = detect(88)
    assert long["stop_reference"] == 92


def test_output_structural_fields_and_window():
    result = detect(112)
    assert result["setup_bar_ts"] == datetime(2026, 1, 8, 4, 0, tzinfo=ET)
    assert result["entry_window_open"] == datetime(2026, 1, 8, 9, 30, tzinfo=ET)
    assert result["entry_window_close"] is None
    assert result["reference_candle_high"] == 125
    assert result["reference_candle_low"] == 75
    assert result["bar_c_high"] == 110
    assert result["bar_c_low"] == 90
    assert result["instrument"] == "MNQ"


def test_missing_pattern_bar_fails_closed():
    bars_12h, bars_5m, bars_60m = bundle()
    assert detect_12hr_miyagi(bars_12h[:-1], bars_5m, bars_60m, EVAL, "MNQ") is None


def test_non_131_sequence_is_rejected():
    bars_12h, bars_5m, bars_60m = bundle()
    bars_12h[2]["high"] = 114  # B no longer outside A
    assert detect_12hr_miyagi(bars_12h, bars_5m, bars_60m, EVAL, "MNQ") is None


def test_missing_930_bar_fails_closed():
    bars_12h, bars_5m, bars_60m = bundle()
    bars_5m = [value for value in bars_5m if value["ts"].time().isoformat() != "09:30:00"]
    assert detect_12hr_miyagi(bars_12h, bars_5m, bars_60m, EVAL, "MNQ") is None


def test_missing_completed_hourly_bar_fails_closed():
    bars_12h, bars_5m, _ = bundle()
    forming = [bar(EVAL, 9, 0, 100, 120, 90, 110)]
    assert detect_12hr_miyagi(bars_12h, bars_5m, forming, EVAL, "MNQ") is None


def test_malformed_and_naive_bars_are_ignored():
    bars_12h, bars_5m, bars_60m = bundle()
    bars_5m.insert(0, {"ts": datetime(2026, 1, 8, 8), "open": 1, "high": 2, "low": 0, "close": 1})
    bars_5m.insert(0, {"bad": "row"})
    assert detect_12hr_miyagi(bars_12h, bars_5m, bars_60m, EVAL, "MNQ")["signal"] is True


def test_dst_keeps_et_wall_clock_boundaries():
    spring = date(2026, 3, 12)
    assert datetime(spring.year, spring.month, spring.day, 9, 30, tzinfo=ET).utcoffset().total_seconds() == -14400


@pytest.mark.parametrize("index", [0, 1, 2])
def test_non_list_inputs_raise(index):
    args = list(bundle())
    args[index] = tuple(args[index])
    with pytest.raises(TypeError):
        detect_12hr_miyagi(*args, EVAL, "MNQ")
