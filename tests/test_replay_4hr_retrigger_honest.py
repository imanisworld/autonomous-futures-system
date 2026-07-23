from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from research.replay_4hr_retrigger_honest import replay_one, summarize


ET = ZoneInfo("America/New_York")


def dt(hour, minute=0):
    return datetime(2026, 1, 6, hour, minute, tzinfo=ET)


def bar(ts, o, h, l, c):
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c}


def signal(direction="LONG"):
    return {
        "direction": direction,
        "entry_trigger": 100.0,
        "target": 110.0 if direction == "LONG" else 90.0,
        "entry_window_open": dt(9, 30).isoformat(),
        "entry_window_close": dt(11).isoformat(),
    }


def hours():
    return [
        bar(dt(8), 100, 105, 95, 101),
        bar(dt(9), 101, 106, 96, 102),
    ]


def test_ioc_uses_crossing_bar_close_and_skips_decision_bar_for_exits():
    bars = [
        # Contains trigger, stop and target, but it is the completed decision
        # bar and cannot resolve a position filled at its close.
        bar(dt(9, 30), 99, 111, 94, 101),
        bar(dt(9, 35), 101, 110, 100, 109),
    ]
    out = replay_one(
        eval_date=date(2026, 1, 6),
        signal=signal(),
        bars_5m=bars,
        bars_1h=hours(),
        slippage_ticks=2,
    )
    assert out["filled"]
    assert out["entry_fill"] == 101.5
    assert out["stop"] == 95
    assert out["exit_reason"] == "TARGET"
    assert out["exit_fill"] == 109.5


def test_ioc_cancels_when_decision_close_exceeds_32_tick_cap():
    out = replay_one(
        eval_date=date(2026, 1, 6),
        signal=signal(),
        bars_5m=[bar(dt(9, 30), 99, 112, 99, 108.25)],
        bars_1h=hours(),
        slippage_ticks=2,
    )
    assert not out["filled"]
    assert out["status"] == "IOC_CANCELLED"


def test_stop_is_recalculated_from_hour_completed_at_crossing_time():
    bars = [
        bar(dt(10, 5), 99, 101, 98, 100.5),
        bar(dt(10, 10), 100, 101, 95.5, 96),
    ]
    out = replay_one(
        eval_date=date(2026, 1, 6),
        signal=signal(),
        bars_5m=bars,
        bars_1h=hours(),
        slippage_ticks=2,
    )
    assert out["stop_bar_ts"] == dt(9).isoformat()
    assert out["stop"] == 96
    assert out["exit_reason"] == "STOP"
    assert out["exit_fill"] == 95.5


def test_955_crossing_does_not_look_ahead_to_9am_hour():
    bars = [
        bar(dt(9, 55), 99, 101, 98, 100.5),
        bar(dt(10), 100, 110, 94, 102),
    ]
    out = replay_one(
        eval_date=date(2026, 1, 6),
        signal=signal(),
        bars_5m=bars,
        bars_1h=hours(),
        slippage_ticks=1,
    )
    assert out["stop_bar_ts"] == dt(8).isoformat()
    assert out["stop"] == 95


def test_subsequent_both_hit_bar_is_stop_first():
    bars = [
        bar(dt(9, 30), 99, 101, 99, 100),
        bar(dt(9, 35), 100, 111, 94, 100),
    ]
    out = replay_one(
        eval_date=date(2026, 1, 6),
        signal=signal(),
        bars_5m=bars,
        bars_1h=hours(),
        slippage_ticks=2,
    )
    assert out["exit_reason"] == "STOP"


def test_short_slippage_is_adverse_on_both_legs():
    short_hours = [
        bar(dt(8), 100, 105, 95, 101),
        bar(dt(9), 101, 106, 96, 102),
    ]
    bars = [
        bar(dt(9, 30), 101, 102, 99, 99),
        bar(dt(9, 35), 99, 100, 89, 91),
    ]
    out = replay_one(
        eval_date=date(2026, 1, 6),
        signal=signal("SHORT"),
        bars_5m=bars,
        bars_1h=short_hours,
        slippage_ticks=2,
    )
    assert out["entry_fill"] == 98.5
    assert out["exit_reason"] == "TARGET"
    assert out["exit_fill"] == 90.5


def test_summary_counts_signals_and_fills_separately():
    rows = [
        {"date": "2026-01-01", "filled": False, "status": "NO_TRIGGER"},
        {
            "date": "2026-01-02",
            "filled": True,
            "status": "FILLED",
            "exit_reason": "TARGET",
            "gross_pnl": 11,
            "total_costs": 1,
            "net_pnl": 10,
        },
        {
            "date": "2026-01-03",
            "filled": True,
            "status": "FILLED",
            "exit_reason": "STOP",
            "gross_pnl": -4,
            "total_costs": 1,
            "net_pnl": -5,
        },
    ]
    result = summarize(rows)
    assert result["n"] == 3
    assert result["fills"] == 2
    assert result["fill_rate"] == pytest.approx(2 / 3)
    assert result["expectancy_per_signal"] == pytest.approx(5 / 3, abs=1e-4)
    assert result["profit_factor"] == 2
