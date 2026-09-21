from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from alert_ranker.causal_bars import Bar
from research.futures_intraday_momentum import (
    COMMISSION_RT,
    ENTRY_BAR_INDEX,
    EXIT_BAR_INDEX,
    SIGNAL_BAR_INDEX,
    build_trade,
    build_report,
)


BASE = datetime(2026, 9, 18, 13, 30, tzinfo=timezone.utc)


def _bars(signal_close: float, entry_open: float, exit_close: float):
    out = []
    for i in range(78):
        px = 100.0
        if i == SIGNAL_BAR_INDEX:
            px = signal_close
        o = entry_open if i == ENTRY_BAR_INDEX else px
        c = exit_close if i == EXIT_BAR_INDEX else px
        out.append(Bar(start=BASE + timedelta(minutes=5 * i), open=o, high=max(o, c), low=min(o, c), close=c, volume=1000.0, vwap=c))
    return out


def test_signal_uses_completed_1525_bar_and_entry_uses_next_bar_open():
    bars = _bars(101.0, 102.0, 103.0)
    row = build_trade("MES", date(2026, 9, 18), 100.0, bars, slippage_label="base", slippage_ticks=1.0)
    assert row is not None
    assert row.direction == "LONG"
    assert row.rod_return == 0.01
    assert row.decision_open == 102.0
    assert row.fill_entry == 102.25


def test_short_costs_are_adverse_on_both_entry_and_exit():
    bars = _bars(99.0, 98.0, 97.0)
    row = build_trade("MES", date(2026, 9, 18), 100.0, bars, slippage_label="base", slippage_ticks=1.0)
    assert row is not None
    assert row.direction == "SHORT"
    assert row.fill_entry == 97.75
    assert row.fill_exit == 97.25
    assert row.net_pnl == (0.5 * 5.0) - COMMISSION_RT


def test_zero_signal_produces_no_trade():
    bars = _bars(100.0, 100.0, 100.0)
    assert build_trade("MES", date(2026, 9, 18), 100.0, bars, slippage_label="base", slippage_ticks=1.0) is None


def test_report_never_promotes_to_paper_without_stop():
    rows = []
    for instrument in ("MNQ", "MES"):
        for idx in range(240):
            bars = _bars(101.0, 100.0, 101.0)
            day = date(2025, 1, 2) if idx < 120 else date(2026, 1, 2)
            for label, ticks in (("base", 1.0), ("stress", 2.0)):
                row = build_trade(instrument, day, 100.0, bars, slippage_label=label, slippage_ticks=ticks)
                assert row is not None
                rows.append(row)
    by = {i: [r for r in rows if r.instrument == i] for i in ("MNQ", "MES")}
    report = build_report(by, {"MNQ": {}, "MES": {}})
    assert report["gate"]["passes_for_paper"] is False
    assert report["gate"]["classification"] in {"WAIT", "PROMISING BUT UNPROVEN"}
