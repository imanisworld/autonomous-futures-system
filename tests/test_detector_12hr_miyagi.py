from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from research.detector_12hr_miyagi import detect_12hr_miyagi


ET = ZoneInfo("America/New_York")
EVAL_DATE = date(2026, 1, 8)  # Thursday

BAR_D_TS = datetime(2026, 1, 8, 4, tzinfo=ET)
BAR_C_TS = datetime(2026, 1, 7, 16, tzinfo=ET)
BAR_B_TS = datetime(2026, 1, 7, 4, tzinfo=ET)
BAR_A_TS = datetime(2026, 1, 6, 16, tzinfo=ET)
BAR_Z_TS = datetime(2026, 1, 6, 4, tzinfo=ET)
NINE_THIRTY = datetime(2026, 1, 8, 9, 30, tzinfo=ET)


def bar12(ts, h, l, o=None, c=None):
    return {"ts": ts, "open": o if o is not None else l, "high": h, "low": l, "close": c if c is not None else h}


def bar5(ts, o, h, l, c):
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c}


def base_12h_bars():
    """Canonical valid 1-3-1 skeleton: Z outside-parent of A, A inside Z,
    B outside A, C inside B. Values chosen so Bar C = [95, 105] (trigger 100)."""
    return [
        bar12(BAR_Z_TS, 120, 80),     # Bar Z: wide bar A sits inside
        bar12(BAR_A_TS, 110, 90),     # Bar A: inside Z
        bar12(BAR_B_TS, 130, 70),     # Bar B: outside A
        bar12(BAR_C_TS, 105, 95),     # Bar C: inside B, trigger = 100
        bar12(BAR_D_TS, 103, 97),     # Bar D: live bar, only its existence matters
    ]


def premarket_clean(eval_date=EVAL_DATE, bar_c_high=105, bar_c_low=95):
    """5-minute bars spanning [4:00, 9:30) ET that never both-breach Bar C."""
    bars = []
    ts = datetime(eval_date.year, eval_date.month, eval_date.day, 4, 0, tzinfo=ET)
    end = datetime(eval_date.year, eval_date.month, eval_date.day, 9, 30, tzinfo=ET)
    while ts < end:
        bars.append(bar5(ts, 100, bar_c_high - 1, bar_c_low + 1, 100))
        ts += timedelta(minutes=5)
    return bars


def sixty_min_bars(eval_date=EVAL_DATE, stop_high=112, stop_low=88):
    """60-minute bars through the 8-9AM ET stop-reference bar."""
    return [
        bar12(datetime(eval_date.year, eval_date.month, eval_date.day, 6, tzinfo=ET), 150, 50),
        bar12(datetime(eval_date.year, eval_date.month, eval_date.day, 7, tzinfo=ET), 140, 60),
        bar12(datetime(eval_date.year, eval_date.month, eval_date.day, 8, tzinfo=ET), stop_high, stop_low),
    ]


def short_signal_5m(eval_date=EVAL_DATE, open_price=110):
    bars = premarket_clean(eval_date)
    ts = datetime(eval_date.year, eval_date.month, eval_date.day, 9, 30, tzinfo=ET)
    bars.append(bar5(ts, open_price, open_price + 2, open_price - 2, open_price))
    return bars


def long_signal_5m(eval_date=EVAL_DATE, open_price=90):
    bars = premarket_clean(eval_date)
    ts = datetime(eval_date.year, eval_date.month, eval_date.day, 9, 30, tzinfo=ET)
    bars.append(bar5(ts, open_price, open_price + 2, open_price - 2, open_price))
    return bars


def test_valid_short_signal_bar_d_2u():
    result = detect_12hr_miyagi(
        base_12h_bars(), short_signal_5m(), sixty_min_bars(), EVAL_DATE, "MNQ"
    )
    assert result["signal"] is True
    assert result["direction"] == "SHORT"
    assert result["entry_trigger"] == 100.0
    assert result["stop_reference"] == 112  # 8AM bar high
    assert result["stop_reference_bar_ts"] == datetime(2026, 1, 8, 8, tzinfo=ET)
    assert result["target"] == 95  # T1 = Bar C low
    assert result["target_2"] == 70  # T2 = Bar B low
    assert result["bar_c_high"] == 105
    assert result["bar_c_low"] == 95
    assert result["reference_candle_high"] == 130
    assert result["reference_candle_low"] == 70
    assert result["setup_bar_ts"] == BAR_D_TS
    assert result["entry_window_open"] == NINE_THIRTY
    assert result["entry_window_close"] is None
    assert result["invalidation"] is None


def test_valid_long_signal_bar_d_2d():
    result = detect_12hr_miyagi(
        base_12h_bars(), long_signal_5m(), sixty_min_bars(), EVAL_DATE, "MES"
    )
    assert result["signal"] is True
    assert result["direction"] == "LONG"
    assert result["entry_trigger"] == 100.0
    assert result["stop_reference"] == 88  # 8AM bar low
    assert result["target"] == 105  # T1 = Bar C high
    assert result["target_2"] == 130  # T2 = Bar B high


def test_any_of_abcd_missing_returns_none():
    for missing_ts in (BAR_D_TS, BAR_C_TS, BAR_B_TS, BAR_A_TS):
        bars = [b for b in base_12h_bars() if b["ts"] != missing_ts]
        assert (
            detect_12hr_miyagi(bars, short_signal_5m(), sixty_min_bars(), EVAL_DATE, "MNQ")
            is None
        )


def test_bar_c_not_inside_bar_b_returns_none():
    bars = base_12h_bars()
    for b in bars:
        if b["ts"] == BAR_C_TS:
            b["high"] = 999  # breaks C <= B.high
    assert (
        detect_12hr_miyagi(bars, short_signal_5m(), sixty_min_bars(), EVAL_DATE, "MNQ")
        is None
    )


def test_bar_c_equal_to_bar_b_boundary_counts_as_inside():
    """<=/>= are inclusive per spec wording."""
    bars = base_12h_bars()
    for b in bars:
        if b["ts"] == BAR_C_TS:
            b["high"] = 130  # exactly equal to Bar B high
            b["low"] = 70    # exactly equal to Bar B low -- trigger becomes 100 still
    result = detect_12hr_miyagi(bars, short_signal_5m(open_price=140), sixty_min_bars(), EVAL_DATE, "MNQ")
    assert result is not None
    assert result["signal"] is True


def test_bar_b_not_outside_bar_a_returns_none():
    bars = base_12h_bars()
    for b in bars:
        if b["ts"] == BAR_B_TS:
            b["low"] = 95  # no longer < Bar A low (90)
    assert (
        detect_12hr_miyagi(bars, short_signal_5m(), sixty_min_bars(), EVAL_DATE, "MNQ")
        is None
    )


def test_bar_a_not_inside_bar_z_returns_none():
    bars = base_12h_bars()
    for b in bars:
        if b["ts"] == BAR_A_TS:
            b["high"] = 999  # breaks A <= Z.high (120)
    assert (
        detect_12hr_miyagi(bars, short_signal_5m(), sixty_min_bars(), EVAL_DATE, "MNQ")
        is None
    )


def test_bar_z_missing_returns_none():
    bars = [b for b in base_12h_bars() if b["ts"] != BAR_Z_TS]
    assert (
        detect_12hr_miyagi(bars, short_signal_5m(), sixty_min_bars(), EVAL_DATE, "MNQ")
        is None
    )


def test_candle3_becomes_outside_bar_before_930_invalidates():
    premarket = premarket_clean()
    # Inject a single 5-minute bar whose OWN high and low both breach Bar C's
    # [95, 105] range -- the literal single-bar engulf test.
    breach_ts = datetime(2026, 1, 8, 6, 0, tzinfo=ET)
    for bar in premarket:
        if bar["ts"] == breach_ts:
            bar["high"] = 106
            bar["low"] = 94
    bars_5m = premarket + [bar5(NINE_THIRTY, 110, 112, 108, 110)]
    result = detect_12hr_miyagi(base_12h_bars(), bars_5m, sixty_min_bars(), EVAL_DATE, "MNQ")
    assert result == {"signal": False, "invalidation": "CANDLE3_BECAME_OUTSIDE_BAR"}


def test_single_sided_premarket_breach_does_not_invalidate():
    """High-only or low-only breach (two different bars) must NOT invalidate --
    only one bar's own high AND low breaching both sides counts."""
    premarket = premarket_clean()
    ts1 = datetime(2026, 1, 8, 6, 0, tzinfo=ET)
    ts2 = datetime(2026, 1, 8, 6, 5, tzinfo=ET)
    for bar in premarket:
        if bar["ts"] == ts1:
            bar["high"] = 106  # breaches high only
        if bar["ts"] == ts2:
            bar["low"] = 94  # breaches low only, different bar
    bars_5m = premarket + [bar5(NINE_THIRTY, 110, 112, 108, 110)]
    result = detect_12hr_miyagi(base_12h_bars(), bars_5m, sixty_min_bars(), EVAL_DATE, "MNQ")
    assert result["signal"] is True
    assert result["invalidation"] is None


def test_missing_930_bar_returns_none():
    bars_5m = premarket_clean()  # no 9:30 bar appended
    assert (
        detect_12hr_miyagi(base_12h_bars(), bars_5m, sixty_min_bars(), EVAL_DATE, "MNQ")
        is None
    )


@pytest.mark.parametrize("open_price", [105, 95])
def test_price_exactly_at_bar_c_boundary_is_ambiguous_returns_none(open_price):
    bars_5m = premarket_clean() + [bar5(NINE_THIRTY, open_price, open_price + 1, open_price - 1, open_price)]
    assert (
        detect_12hr_miyagi(base_12h_bars(), bars_5m, sixty_min_bars(), EVAL_DATE, "MNQ")
        is None
    )


def test_price_between_bar_c_high_and_low_returns_none():
    bars_5m = premarket_clean() + [bar5(NINE_THIRTY, 100, 101, 99, 100)]
    assert (
        detect_12hr_miyagi(base_12h_bars(), bars_5m, sixty_min_bars(), EVAL_DATE, "MNQ")
        is None
    )


def test_missing_60m_bars_before_930_returns_none():
    assert (
        detect_12hr_miyagi(base_12h_bars(), short_signal_5m(), [], EVAL_DATE, "MNQ")
        is None
    )


def test_stop_reference_uses_last_completed_60m_bar_before_930_not_earlier_ones():
    bars_60m = sixty_min_bars(stop_high=112, stop_low=88)
    # Confirm changing an EARLIER 60m bar (7AM) does not affect the stop.
    for b in bars_60m:
        if b["ts"].hour == 7:
            b["high"] = 999
            b["low"] = -999
    result = detect_12hr_miyagi(base_12h_bars(), short_signal_5m(), bars_60m, EVAL_DATE, "MNQ")
    assert result["stop_reference"] == 112
    assert result["stop_reference_bar_ts"].hour == 8


def test_60m_bar_at_or_after_930_is_excluded_from_stop_reference():
    bars_60m = sixty_min_bars() + [
        bar12(NINE_THIRTY, 999, -999),  # must never be selected as stop ref
    ]
    result = detect_12hr_miyagi(base_12h_bars(), short_signal_5m(), bars_60m, EVAL_DATE, "MNQ")
    assert result["stop_reference"] == 112
    assert result["stop_reference_bar_ts"].hour == 8


@pytest.mark.parametrize("arg_index", [0, 1, 2])
def test_non_list_inputs_raise_type_error(arg_index):
    args = [base_12h_bars(), short_signal_5m(), sixty_min_bars()]
    args[arg_index] = tuple(args[arg_index])
    with pytest.raises(TypeError):
        detect_12hr_miyagi(args[0], args[1], args[2], EVAL_DATE, "MNQ")


def test_malformed_bar_entries_are_ignored_not_crashed_on():
    bars_12h = base_12h_bars() + [{"not": "a real bar"}]
    result = detect_12hr_miyagi(bars_12h, short_signal_5m(), sixty_min_bars(), EVAL_DATE, "MNQ")
    assert result["signal"] is True
