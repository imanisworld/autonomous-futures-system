from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from research.replay_12hr_miyagi import (
    _ioc_fill,
    replay_signal,
    run_replay,
)


ET = ZoneInfo("America/New_York")


def ts(hour, minute=0):
    return datetime(2025, 1, 2, hour, minute, tzinfo=ET)


def bar(stamp, o, h, l, c, volume=1):
    return {
        "ts": stamp,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": volume,
    }


def signal(direction="LONG"):
    return {
        "direction": direction,
        "entry_trigger": 100.0,
        "target": 110.0 if direction == "LONG" else 90.0,
        "entry_window_open": ts(9, 30),
    }


def test_ioc_matches_mnq_32_tick_cap_and_adverse_slippage():
    assert _ioc_fill(
        direction="LONG", trigger=100, market=107, slippage_ticks=2
    ) == (107, 107.5)
    assert _ioc_fill(
        direction="LONG", trigger=100, market=108, slippage_ticks=2
    ) == (108, 108)
    assert _ioc_fill(
        direction="LONG", trigger=100, market=108.25, slippage_ticks=2
    ) is None
    assert _ioc_fill(
        direction="SHORT", trigger=100, market=93, slippage_ticks=2
    ) == (93, 92.5)
    assert _ioc_fill(
        direction="SHORT", trigger=100, market=91.75, slippage_ticks=2
    ) is None


def test_first_touch_attempt_can_ioc_cancel_and_never_retries():
    bars = [
        bar(ts(9, 30), 90, 101, 89, 109, 1),
        bar(ts(9, 35), 100, 101, 99, 100, 1),
    ]
    row = replay_signal(
        date(2025, 1, 2), signal(), bars, [], slippage_ticks=2
    )
    assert row["touch_bar_ts"] == ts(9, 30).isoformat()
    assert row["outcome"] == "IOC_CANCELLED"
    assert not row["filled"]


def test_stop_is_recalculated_at_touch_bar_close_and_fixed():
    bars_5m = [
        bar(ts(10, 5), 95, 101, 94, 100, 1),
        bar(ts(10, 10), 100, 106, 96, 104, 1),
        bar(ts(10, 15), 104, 111, 103, 110, 1),
    ]
    bars_60m = [
        bar(ts(8), 90, 99, 80, 95, 1),
        bar(ts(9), 95, 102, 90, 100, 1),
        bar(ts(10), 100, 105, 96, 104, 1),
    ]
    row = replay_signal(
        date(2025, 1, 2), signal(), bars_5m, bars_60m, slippage_ticks=2
    )
    assert row["arrival_ts"] == ts(10, 10).isoformat()
    assert row["stop_bar_ts"] == ts(9).isoformat()
    assert row["stop"] == 90
    assert row["outcome"] == "TARGET"
    assert row["entry_fill"] == 100.5
    assert row["exit_fill"] == 109.5
    assert row["total_costs"] == pytest.approx(3.24)


def test_stop_wins_same_bar_target_ambiguity():
    bars_5m = [
        bar(ts(10, 5), 95, 101, 94, 100, 1),
        bar(ts(10, 10), 100, 111, 89, 100, 1),
    ]
    bars_60m = [bar(ts(9), 95, 102, 90, 100, 1)]
    row = replay_signal(
        date(2025, 1, 2), signal(), bars_5m, bars_60m, slippage_ticks=2
    )
    assert row["outcome"] == "STOP"
    assert row["raw_exit"] == 90


def test_actual_ioc_fill_with_wrong_side_hourly_stop_fails_closed():
    bars_5m = [bar(ts(10, 5), 95, 101, 94, 100)]
    # LONG stop at 101 is above the actual 100.50 slipped fill.
    bars_60m = [bar(ts(9), 95, 105, 101, 103)]
    row = replay_signal(
        date(2025, 1, 2), signal(), bars_5m, bars_60m, slippage_ticks=2
    )
    assert row["outcome"] == "INVALID_NON_PROTECTIVE_STOP"
    assert not row["filled"]
    assert row["entry_fill"] == 100.5
    assert row["stop"] == 101


def test_walk_forward_uses_setup_date_range_and_reports_splits():
    signals = {}
    for offset in (0, 2, 8, 10):
        day = date(2025, 1, 2) + timedelta(days=offset)
        open_ts = datetime.combine(day, ts(9, 30).timetz())
        signals[day.isoformat()] = {
            "direction": "LONG",
            "entry_trigger": 100,
            "target": 110,
            "entry_window_open": open_ts.isoformat(),
        }
    report = run_replay(signals, [], [])
    primary = report["sensitivity"]["2"]
    assert primary["midpoint_date"] == "2025-01-07"
    assert primary["halves"]["H1"]["n"] == 2
    assert primary["halves"]["H2"]["n"] == 2
    assert primary["overall"]["n"] == 4
