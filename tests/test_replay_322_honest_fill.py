from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from research.replay_322_honest_fill import (
    ReplayInputError,
    recover_entry,
    replay_signal,
    run_replay,
)


ET = ZoneInfo("America/New_York")


def bar(hour, minute, *, o, h, low, c, day=date(2025, 1, 2)):
    return {
        "ts": datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET),
        "open": o,
        "high": h,
        "low": low,
        "close": c,
    }


def signal(direction="LONG", *, gap_open=False):
    return {
        "date": date(2025, 1, 2),
        "direction": direction,
        "entry_trigger": 100.0,
        "entry_price": 100.0,
        "stop": 90.0 if direction == "LONG" else 110.0,
        "target": 110.0 if direction == "LONG" else 90.0,
        "gap_open": gap_open,
    }


def test_recovers_first_strict_crossing_and_uses_its_close():
    bars = [
        bar(10, 0, o=99, h=100, low=98, c=99),
        bar(10, 5, o=99, h=100.25, low=98, c=101),
        bar(10, 10, o=101, h=105, low=100, c=104),
    ]
    entry = recover_entry(signal(), bars)
    assert entry["bar"]["ts"].minute == 5
    assert entry["market_proxy"] == 101
    assert entry["gap_open"] is False


def test_gap_open_fills_at_open_plus_adverse_slippage():
    s = signal(gap_open=True)
    bars = [
        bar(10, 0, o=101, h=105, low=99, c=104),
        bar(10, 5, o=104, h=111, low=103, c=110),
    ]
    row = replay_signal(s, bars, slippage_ticks=2)
    assert row["base_entry_price"] == 101
    assert row["fill_entry_price"] == 101.5
    assert row["exit_reason"] == "TARGET"


@pytest.mark.parametrize(
    ("direction", "close"),
    [("LONG", 108.25), ("SHORT", 91.75)],
)
def test_ioc_cancels_when_crossing_bar_close_is_beyond_32_tick_cap(
    direction, close
):
    s = signal(direction)
    crossing = (
        bar(10, 0, o=99, h=109, low=98, c=close)
        if direction == "LONG"
        else bar(10, 0, o=101, h=102, low=91, c=close)
    )
    row = replay_signal(s, [crossing, bar(10, 5, o=100, h=111, low=89, c=100)])
    assert row["filled"] is False
    assert row["exit_reason"] == "ENTRY_NOT_FILLED"
    assert row["net_pnl"] == 0


def test_non_gap_entry_bar_extremes_are_not_used_after_close_fill():
    s = signal()
    bars = [
        # Crosses and touches target before the IOC arrives at this bar's close.
        bar(10, 0, o=99, h=111, low=98, c=101),
        bar(10, 5, o=101, h=102, low=89, c=90),
    ]
    row = replay_signal(s, bars)
    assert row["exit_reason"] == "STOP"


def test_target_already_through_at_ioc_arrival_exits_at_market_not_stale_target():
    s = {**signal("SHORT"), "target": 99.75}
    bars = [
        bar(10, 0, o=101, h=102, low=99, c=99.5),
        bar(10, 5, o=99.5, h=100, low=98, c=99),
    ]
    row = replay_signal(s, bars, slippage_ticks=2)
    assert row["exit_reason"] == "TARGET_AT_ENTRY"
    assert row["base_entry_price"] == 99.5
    assert row["base_exit_price"] == 99.5
    assert row["gross_pnl"] == 0
    assert row["net_pnl"] == pytest.approx(-3.24)


def test_post_fill_wrong_side_fixed_stop_fails_closed():
    s = {**signal(), "stop": 101.0}
    bars = [
        bar(10, 0, o=99, h=100.25, low=98, c=100),
        bar(10, 5, o=100, h=111, low=99, c=110),
    ]
    row = replay_signal(s, bars, slippage_ticks=2)
    assert row["filled"] is False
    assert row["ioc_parent_filled"] is True
    assert row["exit_reason"] == "POST_FILL_INVALID_STOP"


def test_same_post_entry_bar_stop_and_target_is_stop_first():
    s = signal()
    bars = [
        bar(10, 0, o=99, h=101, low=98, c=100),
        bar(10, 5, o=100, h=111, low=89, c=100),
    ]
    row = replay_signal(s, bars)
    assert row["exit_reason"] == "STOP"


def test_slippage_applies_on_entry_and_exit_and_commission_is_deducted():
    s = signal()
    bars = [
        bar(10, 0, o=99, h=101, low=98, c=100),
        bar(10, 5, o=100, h=111, low=99, c=110),
    ]
    row = replay_signal(s, bars, slippage_ticks=2)
    assert row["gross_pnl"] == 20
    assert row["slippage_cost"] == 2
    assert row["commission"] == 1.24
    assert row["net_pnl"] == pytest.approx(16.76)


def test_eod_marks_unresolved_trade():
    s = signal()
    bars = [
        bar(10, 0, o=99, h=101, low=98, c=100),
        bar(15, 55, o=104, h=105, low=103, c=104),
    ]
    row = replay_signal(s, bars, slippage_ticks=1)
    assert row["exit_reason"] == "EOD"
    assert row["base_exit_price"] == 104


def test_missing_cross_in_reconciled_signal_fails_closed():
    with pytest.raises(ReplayInputError, match="no five-minute trigger crossing"):
        replay_signal(signal(), [bar(10, 0, o=99, h=100, low=98, c=99)])


def test_calendar_midpoint_halves_and_direction_splits_are_reported():
    d1 = date(2025, 1, 2)
    d2 = date(2025, 1, 8)
    signals = [
        signal(),
        {**signal("SHORT"), "date": d2},
    ]
    bars = [
        bar(10, 0, o=99, h=101, low=98, c=100, day=d1),
        bar(10, 5, o=100, h=111, low=99, c=110, day=d1),
        bar(10, 0, o=101, h=102, low=99, c=100, day=d2),
        bar(10, 5, o=100, h=101, low=89, c=90, day=d2),
    ]
    report = run_replay(
        signals,
        bars,
        study_start=date(2025, 1, 1),
        study_end=date(2025, 1, 9),
    )
    assert report["halves"]["H1"]["n"] == 1
    assert report["halves"]["H2"]["n"] == 1
    assert report["directions"]["LONG"]["n"] == 1
    assert report["directions"]["SHORT"]["n"] == 1
