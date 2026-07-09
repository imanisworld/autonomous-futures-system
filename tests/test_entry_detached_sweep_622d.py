from __future__ import annotations

import json

from replay.candle_loader import ReplayCandle

from scripts.entry_detached_sweep_622d import (
    Case,
    _classify,
    _load_cases,
    _simulate,
    _summarize,
    _walk_forward_half,
)


def _candle(ts, o, h, l, c):
    return ReplayCandle(
        timestamp=ts,
        instrument="MES",
        session="ny_am",
        open=o,
        high=h,
        low=l,
        close=c,
        volume=1000,
        vwap=c,
        orb_high=h,
        orb_low=l,
        orb_status=None,
        market_condition="TRENDING",
        trend_direction="UP",
        trend_strength="STRONG",
        previous_day_high=h,
        previous_day_low=l,
        previous_day_close=c,
    )


def _case(**kw):
    base = dict(
        day="2026-06-01",
        instrument="MES",
        bar_ts="2026-06-01T14:00:00+00:00",
        strategy="orb_breakout",
        direction="LONG",
        entry=100.0,
        stop=95.0,
        target=115.0,
    )
    base.update(kw)
    return Case(**base)


# ─── Dedup guard ────────────────────────────────────────────────────────────

def test_dedup_key_identical_for_same_case():
    a = _case()
    b = _case()
    assert a.dedup_key() == b.dedup_key()


def test_dedup_key_differs_on_entry():
    a = _case(entry=100.0)
    b = _case(entry=101.0)
    assert a.dedup_key() != b.dedup_key()


# ─── Fill-model simulation ──────────────────────────────────────────────────

def test_market_model_always_fills_and_resolves():
    case = _case(entry=100.0, stop=95.0, target=115.0)
    candles = [
        _candle(case.bar_ts, 99, 101, 98, 100),
        _candle("2026-06-01T14:15:00+00:00", 100, 116, 99, 115),
    ]
    result = _simulate(case, candles, "market")
    assert result["status"] == "FILLED"
    assert result["result"] == "WIN"


def test_ioc_limit_no_fill_when_market_beyond_cap():
    # LONG, entry=100, MES tolerance 16 ticks (4.0 pts) -> cap 104.0.
    # decision-bar close 110 is well beyond the cap -> unmarketable.
    case = _case(entry=100.0, stop=95.0, target=115.0)
    candles = [
        _candle(case.bar_ts, 108, 111, 107, 110),
        _candle("2026-06-01T14:15:00+00:00", 110, 116, 109, 115),
    ]
    result = _simulate(case, candles, "ioc_limit")
    assert result["status"] == "NO_FILL"
    assert result["result"] == "CANCELLED"


def test_ioc_limit_fills_when_market_within_cap():
    # decision-bar close 101 is within the 104.0 cap -> marketable, fills.
    case = _case(entry=100.0, stop=95.0, target=115.0)
    candles = [
        _candle(case.bar_ts, 99, 102, 98, 101),
        _candle("2026-06-01T14:15:00+00:00", 101, 116, 100, 115),
    ]
    result = _simulate(case, candles, "ioc_limit")
    assert result["status"] == "FILLED"
    assert result["result"] == "WIN"


def test_stop_market_no_next_bar_cancels():
    case = _case()
    candles = [_candle(case.bar_ts, 99, 101, 98, 100)]  # no next bar
    result = _simulate(case, candles, "stop_market")
    assert result["status"] == "NO_FILL"


def test_no_data_when_bar_ts_not_in_candles():
    case = _case(bar_ts="2099-01-01T00:00:00+00:00")
    candles = [_candle("2026-06-01T14:00:00+00:00", 99, 101, 98, 100)]
    result = _simulate(case, candles, "market")
    assert result["status"] == "NO_DATA"


# ─── Walk-forward half split ────────────────────────────────────────────────

def test_walk_forward_half_splits_at_midpoint():
    cases = [
        _case(day="2026-06-01"),
        _case(day="2026-06-02"),
        _case(day="2026-06-03"),
        _case(day="2026-06-04"),
    ]
    halves = _walk_forward_half(cases)
    assert halves["2026-06-01"] == "H1"
    assert halves["2026-06-02"] == "H1"
    assert halves["2026-06-03"] == "H2"
    assert halves["2026-06-04"] == "H2"


def test_walk_forward_half_empty_input():
    assert _walk_forward_half([]) == {}


# ─── Summary aggregation ────────────────────────────────────────────────────

def test_summarize_aggregation_math():
    rows = [
        {"market": {"status": "FILLED", "result": "WIN", "pnl": 100.0}},
        {"market": {"status": "FILLED", "result": "LOSS", "pnl": -50.0}},
        {"market": {"status": "NO_FILL", "result": "CANCELLED", "pnl": 0.0}},
    ]
    s = _summarize(rows, "market")
    assert s["cases"] == 3
    assert s["filled"] == 2
    assert s["fill_rate"] == round(2 / 3, 4)
    assert s["no_fill"] == 1
    assert s["wins"] == 1
    assert s["losses"] == 1
    assert s["net_pnl"] == 50.0
    assert s["expectancy"] == 25.0  # mean of (100, -50)


def test_summarize_empty_resolved_gives_none_expectancy():
    rows = [{"market": {"status": "NO_FILL", "result": "CANCELLED", "pnl": 0.0}}]
    s = _summarize(rows, "market")
    assert s["expectancy"] is None


# ─── Classification ─────────────────────────────────────────────────────────

def test_classify_insufficient_data_below_min_cell_n():
    ioc = {"cases": 5, "fill_rate": 0.5, "expectancy": 10.0}
    stop_mkt = {"cases": 5, "fill_rate": 0.6, "expectancy": 10.0}
    assert _classify(ioc, stop_mkt) == "INSUFFICIENT_DATA"


def test_classify_underfilling_not_entry_driven_when_fill_rate_barely_moves():
    ioc = {"cases": 50, "fill_rate": 0.40, "expectancy": 5.0}
    stop_mkt = {"cases": 50, "fill_rate": 0.45, "expectancy": 5.0}
    assert _classify(ioc, stop_mkt) == "UNDERFILLING_NOT_ENTRY_DRIVEN"


def test_classify_underfilling_entry_model_when_looser_recovers_fills_without_hurting_expectancy():
    ioc = {"cases": 50, "fill_rate": 0.30, "expectancy": 5.0}
    stop_mkt = {"cases": 50, "fill_rate": 0.80, "expectancy": 8.0}
    assert _classify(ioc, stop_mkt) == "UNDERFILLING_ENTRY_MODEL"


def test_classify_passivity_protective_when_looser_fills_more_but_expectancy_drops():
    ioc = {"cases": 50, "fill_rate": 0.30, "expectancy": 5.0}
    stop_mkt = {"cases": 50, "fill_rate": 0.80, "expectancy": -2.0}
    assert _classify(ioc, stop_mkt) == "PASSIVITY_PROTECTIVE"


def test_classify_bad_strategy_when_both_models_negative():
    ioc = {"cases": 50, "fill_rate": 0.30, "expectancy": -5.0}
    stop_mkt = {"cases": 50, "fill_rate": 0.80, "expectancy": -3.0}
    assert _classify(ioc, stop_mkt) == "BAD_STRATEGY"


# ─── Case loading (integration-lite, tiny fixture journal) ─────────────────

def test_load_cases_dedupes_and_filters(tmp_path, monkeypatch):
    import scripts.entry_detached_sweep_622d as mod

    journal_root = tmp_path / "journals"
    (journal_root / "MES").mkdir(parents=True)
    (journal_root / "MNQ").mkdir(parents=True)
    row_ok = {
        "decision": "NO_TRADE",
        "failed_gates": ["ENTRY_DETACHED_FROM_PRICE"],
        "instrument": "MES",
        "bar_ts": "2026-06-01T14:00:00+00:00",
        "setup": {"strategy": "orb_breakout", "direction": "LONG", "entry": 100.0, "stop": 95.0, "target": 115.0},
    }
    row_not_detached = {
        "decision": "NO_TRADE",
        "failed_gates": ["SIGNAL_BAR_VOLUME_TOO_LOW"],
        "instrument": "MES",
        "bar_ts": "2026-06-01T14:15:00+00:00",
        "setup": {"strategy": "orb_breakout", "direction": "LONG", "entry": 101.0, "stop": 96.0, "target": 116.0},
    }
    row_missing_setup = {
        "decision": "NO_TRADE",
        "failed_gates": ["ENTRY_DETACHED_FROM_PRICE"],
        "instrument": "MES",
        "bar_ts": "2026-06-01T14:30:00+00:00",
        "setup": {"strategy": "orb_breakout", "direction": "LONG", "entry": None, "stop": 96.0, "target": 116.0},
    }
    lines = "\n".join(json.dumps(r) for r in [row_ok, row_ok, row_not_detached, row_missing_setup])
    (journal_root / "MES" / "journal_2026-06-01.jsonl").write_text(lines + "\n")
    (journal_root / "MNQ" / "journal_2026-06-01.jsonl").write_text("")

    monkeypatch.setattr(mod, "JOURNAL_ROOT", journal_root)
    cases = mod._load_cases()
    assert len(cases) == 1  # row_ok deduped from 2 -> 1; not_detached and missing_setup excluded
    assert cases[0].strategy == "orb_breakout"
