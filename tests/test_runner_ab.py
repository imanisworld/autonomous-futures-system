from __future__ import annotations

from argparse import Namespace
from datetime import datetime, timezone

from scripts.runner_ab import _evaluate, _run_trade, _summary


def _ts(minutes: int) -> str:
    return datetime(2026, 1, 5, tzinfo=timezone.utc).replace(minute=minutes).isoformat()


def _trade(ts: str = "2026-01-05T00:00:00+00:00", session: str = "london") -> dict:
    return {
        "instrument": "MNQ",
        "direction": "LONG",
        "entry": 100.0,
        "stop": 90.0,
        "target": 110.0,
        "entry_ts": ts,
        "session": session,
    }


def _candles():
    return [
        (_ts(0), 100.0, 100.0, 100.0, 100.0),
        (_ts(5), 100.0, 130.0, 100.0, 125.0),
        (_ts(10), 125.0, 126.0, 124.0, 125.0),
    ]


def test_runner_ab_uses_ioc_close_and_costs_resolved_fill():
    trade = _trade()
    static = _run_trade(trade, _candles(), runner=False, activation_r=1.0, trail_r=0.5)
    runner = _run_trade(trade, _candles(), runner=True, activation_r=1.0, trail_r=0.5)

    assert static["result"] == "WIN"
    assert static["gross_pnl"] == 19.5
    assert static["net_pnl"] == 18.02
    assert runner["result"] == "WIN"
    assert runner["gross_pnl"] == 48.75
    assert runner["net_pnl"] == 47.27


def test_runner_ab_preserves_pessimistic_same_bar_resolution():
    candles = [
        (_ts(0), 100.0, 100.0, 100.0, 100.0),
        (_ts(5), 100.0, 111.0, 89.0, 100.0),
    ]
    result = _run_trade(_trade(), candles, runner=False, activation_r=1.0, trail_r=0.5)
    assert result["result"] == "LOSS"
    assert result["exit_reason"] == "STOP_HIT"


def test_runner_ab_fails_closed_on_ioc_miss():
    candles = [
        (_ts(0), 100.0, 100.0, 100.0, 110.0),
        (_ts(5), 110.0, 115.0, 109.0, 112.0),
    ]
    result = _run_trade(_trade(), candles, runner=False, activation_r=1.0, trail_r=0.5)
    assert result["status"] == "NO_FILL"
    assert result["net_pnl"] == 0.0


def test_runner_ab_reports_sessions_and_chronological_halves():
    trades = [_trade(session="london"), _trade(ts="2026-01-05T00:05:00+00:00", session="new_york")]
    args = Namespace(activation_r=1.0, trail_r=0.5, slippage_ticks=1.0, commission_round_trip=1.48, entry_fill_model="ioc_limit", ioc_tolerance={"MNQ": 32.0}, breakeven_at_1r=False, max_hold_min=480)
    report = _evaluate(trades, {"MNQ": _candles()}, args, runner=True)
    assert set(report["sessions"]) == {"london", "new_york"}
    assert report["halves"]["first"]["attempts"] == 1
    assert report["halves"]["second"]["attempts"] == 1


def test_summary_excludes_open_and_no_fill_from_expectancy():
    rows = [
        {"status": "FILLED", "result": "WIN", "net_pnl": 10.0},
        {"status": "NO_FILL", "result": "NO_FILL", "net_pnl": 0.0},
        {"status": "OPEN", "result": "OPEN", "net_pnl": 0.0},
    ]
    summary = _summary(rows)
    assert summary["attempts"] == 3
    assert summary["resolved"] == 1
    assert summary["expectancy_per_resolved"] == 10.0
