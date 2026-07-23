from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from research.detector_322_first_live import detect_322_first_live


ET = ZoneInfo("America/New_York")
DAY = date(2026, 1, 8)


def bar(hour, o, h, l, c):
    return {
        "ts": datetime(2026, 1, 8, hour, tzinfo=ET),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 100,
    }


def short_bundle(ten=None):
    return [
        bar(7, 100, 105, 95, 100),
        bar(8, 100, 110, 90, 105),  # outside 7AM
        bar(9, 105, 115, 92, 110),  # 2U vs 8AM
        ten or bar(10, 100, 105, 90, 95),
    ]


def long_bundle(ten=None):
    return [
        bar(7, 100, 105, 95, 100),
        bar(8, 100, 110, 90, 95),   # outside 7AM
        bar(9, 95, 108, 85, 90),    # 2D vs 8AM
        ten or bar(10, 100, 110, 95, 105),
    ]


def test_short_first_live_signal():
    result = detect_322_first_live(short_bundle(), DAY, "mnq")
    assert result["signal"] is True
    assert result["direction"] == "SHORT"
    assert result["entry_trigger"] == 92
    assert result["entry_price"] == 92
    assert result["stop_reference"] == 115
    assert result["target"] == 90


def test_long_first_live_signal():
    result = detect_322_first_live(long_bundle(), DAY, "MNQ")
    assert result["signal"] is True
    assert result["direction"] == "LONG"
    assert result["entry_trigger"] == 108
    assert result["entry_price"] == 108
    assert result["stop_reference"] == 85
    assert result["target"] == 110


def test_short_gap_open_uses_10am_open_as_entry_price():
    ten = bar(10, 88, 95, 80, 90)  # opens below 9AM low=92
    result = detect_322_first_live(short_bundle(ten), DAY, "MNQ")
    assert result["gap_open"] is True
    assert result["entry_trigger"] == 92
    assert result["entry_price"] == 88
    assert result["entry_bar_ts"] == datetime(2026, 1, 8, 10, tzinfo=ET)


def test_long_gap_open_uses_10am_open_as_entry_price():
    ten = bar(10, 112, 120, 105, 115)  # opens above 9AM high=108
    result = detect_322_first_live(long_bundle(ten), DAY, "MNQ")
    assert result["gap_open"] is True
    assert result["entry_price"] == 112


def test_open_exactly_at_trigger_requires_later_intrabar_break():
    break_ten = bar(10, 92, 100, 91, 95)
    result = detect_322_first_live(short_bundle(break_ten), DAY, "MNQ")
    assert result["gap_open"] is False
    assert result["entry_price"] == 92

    no_break_ten = bar(10, 92, 100, 92, 95)
    result = detect_322_first_live(short_bundle(no_break_ten), DAY, "MNQ")
    assert result == {"signal": False, "invalidation": "NO_BREAK_BY_11AM"}


def test_no_break_returns_explicit_invalidation():
    ten = bar(10, 100, 105, 93, 100)
    assert detect_322_first_live(short_bundle(ten), DAY, "MNQ") == {
        "signal": False,
        "invalidation": "NO_BREAK_BY_11AM",
    }


def test_8am_compares_explicitly_to_7am_bar():
    bars = short_bundle()
    bars[1]["low"] = 95  # equality is not a strict outside low break
    assert detect_322_first_live(bars, DAY, "MNQ") is None


@pytest.mark.parametrize(
    "nine",
    [
        bar(9, 100, 108, 92, 100),  # inside 8AM
        bar(9, 100, 115, 85, 100),  # outside 8AM
    ],
)
def test_9am_inside_or_outside_is_rejected(nine):
    bars = short_bundle()
    bars[2] = nine
    assert detect_322_first_live(bars, DAY, "MNQ") is None


def test_stop_has_no_distance_cap():
    bars = short_bundle()
    bars[2] = bar(9, 105, 500, 92, 110)
    result = detect_322_first_live(bars, DAY, "MNQ")
    assert result["stop_reference"] == 500
    assert result["nine_am_range_points"] == 408


@pytest.mark.parametrize("instrument", ["MES", "QQQ", "IWM", ""])
def test_non_mnq_instruments_are_rejected(instrument):
    assert detect_322_first_live(short_bundle(), DAY, instrument) is None


def test_missing_10am_bar_fails_closed():
    assert detect_322_first_live(short_bundle()[:-1], DAY, "MNQ") is None


def test_output_window_and_references():
    result = detect_322_first_live(short_bundle(), DAY, "MNQ")
    assert result["setup_bar_ts"] == datetime(2026, 1, 8, 9, tzinfo=ET)
    assert result["entry_window_open"] == datetime(2026, 1, 8, 10, tzinfo=ET)
    assert result["entry_window_close"] == datetime(2026, 1, 8, 11, tzinfo=ET)
    assert result["reference_candle_high"] == 110
    assert result["reference_candle_low"] == 90


def test_non_list_input_raises():
    with pytest.raises(TypeError):
        detect_322_first_live(tuple(short_bundle()), DAY, "MNQ")
