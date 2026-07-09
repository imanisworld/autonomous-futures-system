from __future__ import annotations

import json

from scripts.shadow_gate_choke_sweep_622d import ShadowRow
from scripts.shadow_strategy_deep_dive_622d import (
    MIN_CELL_N,
    _breakdown,
    _co_occurring_real_strategy,
    _summarize,
    classify,
    filter_target_rows,
    max_drawdown,
    outlier_share,
    walk_forward_half,
)


def _row(**kw):
    base = dict(
        instrument="MES",
        day="2026-06-01",
        bar_ts="2026-06-01T14:00:00+00:00",
        gate="WEAK_BAR_CLOSE",
        session="ny_am",
        market_condition="TRENDING",
        shadow_strategy="impulse_first_pullback_observed",
        direction="LONG",
        result="WIN",
        entry_filled=True,
        pnl_ticks=40.0,
    )
    base.update(kw)
    return ShadowRow(**base)


# ─── filter_target_rows ──────────────────────────────────────────────────────

def test_filter_target_rows_keeps_only_targets():
    rows = [
        _row(shadow_strategy="impulse_first_pullback_observed"),
        _row(shadow_strategy="strat_22_reversal_observed"),
        _row(shadow_strategy="ema_pullback_trend"),
    ]
    out = filter_target_rows(rows)
    assert set(out.keys()) == {"impulse_first_pullback_observed", "strat_22_reversal_observed"}
    assert len(out["impulse_first_pullback_observed"]) == 1
    assert len(out["strat_22_reversal_observed"]) == 1


# ─── walk_forward_half ───────────────────────────────────────────────────────

def test_walk_forward_half_splits_at_midpoint():
    rows = [_row(day=d) for d in ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04"]]
    halves = walk_forward_half(rows)
    assert halves["2026-06-01"] == "H1"
    assert halves["2026-06-02"] == "H1"
    assert halves["2026-06-03"] == "H2"
    assert halves["2026-06-04"] == "H2"


def test_walk_forward_half_empty():
    assert walk_forward_half([]) == {}


# ─── outlier_share ───────────────────────────────────────────────────────────

def test_outlier_share_dominant_trade():
    rows = [_row(pnl_ticks=1000.0)] + [_row(pnl_ticks=1.0) for _ in range(10)]
    share = outlier_share(rows)
    # top-3 = the big trade (1000*1.25) + two 1.25s; net = 1000*1.25 + 10*1.25
    top3 = 1000 * 1.25 + 1.25 + 1.25
    net = 1000 * 1.25 + 10 * 1.25
    assert share == round(top3 / net, 4)


def test_outlier_share_none_when_net_zero():
    rows = [_row(pnl_ticks=100.0), _row(pnl_ticks=-100.0)]
    assert outlier_share(rows) is None


def test_outlier_share_none_when_no_dollar_values():
    rows = [_row(pnl_ticks=None)]
    assert outlier_share(rows) is None


# ─── max_drawdown ─────────────────────────────────────────────────────────────

def test_max_drawdown_known_sequence():
    # cum: +100, +150 (peak 150), -50 -> 100 (dd=50), +80 -> 180 (new peak)
    rows = [
        _row(instrument="MNQ", day="2026-06-01", bar_ts="t1", pnl_ticks=100 / 0.5),  # +100
        _row(instrument="MNQ", day="2026-06-01", bar_ts="t2", pnl_ticks=50 / 0.5),   # +50 (cum 150)
        _row(instrument="MNQ", day="2026-06-02", bar_ts="t3", pnl_ticks=-150 / 0.5),  # -150 (cum 0, dd from 150->0 = 150)
        _row(instrument="MNQ", day="2026-06-02", bar_ts="t4", pnl_ticks=180 / 0.5),  # +180 (cum 180, new peak)
    ]
    assert max_drawdown(rows) == 150.0


def test_max_drawdown_no_rows():
    assert max_drawdown([]) == 0.0


def test_max_drawdown_monotonic_increase_has_zero_drawdown():
    rows = [_row(bar_ts=f"t{i}", pnl_ticks=10.0) for i in range(5)]
    assert max_drawdown(rows) == 0.0


# ─── _summarize ───────────────────────────────────────────────────────────────

def test_summarize_basic():
    rows = [_row(result="WIN", pnl_ticks=40.0), _row(result="LOSS", pnl_ticks=-20.0)]
    s = _summarize(rows)
    assert s["n"] == 2
    assert s["wins"] == 1
    assert s["losses"] == 1
    assert s["win_rate"] == 0.5
    assert s["net_dollars"] == round(40.0 * 1.25 - 20.0 * 1.25, 2)


def test_summarize_empty():
    s = _summarize([])
    assert s["n"] == 0
    assert s["win_rate"] is None
    assert s["net_dollars"] == 0.0
    assert s["outlier_share"] is None
    assert s["max_drawdown"] == 0.0


# ─── classify ────────────────────────────────────────────────────────────────

def _cell(n, net):
    return {"n": n, "net_dollars": net}


def test_classify_reject_when_net_non_positive():
    combined = {"net_dollars": -10.0, "outlier_share": 0.1, "n": 50}
    assert classify(combined, {}, {}) == "REJECT"


def test_classify_validated_when_consistent_and_not_outlier_dependent():
    combined = {"net_dollars": 500.0, "outlier_share": 0.2, "n": 50}
    by_instrument = {"MES": _cell(20, 100.0), "MNQ": _cell(30, 400.0)}
    by_half = {"H1": _cell(25, 200.0), "H2": _cell(25, 300.0)}
    assert classify(combined, by_instrument, by_half) == "VALIDATED_SHADOW_CANDIDATE"


def test_classify_promising_but_unproven_when_one_half_negative():
    combined = {"net_dollars": 500.0, "outlier_share": 0.2, "n": 50}
    by_instrument = {"MES": _cell(20, 100.0), "MNQ": _cell(30, 400.0)}
    by_half = {"H1": _cell(25, -50.0), "H2": _cell(25, 550.0)}
    assert classify(combined, by_instrument, by_half) == "PROMISING_BUT_UNPROVEN"


def test_classify_promising_but_unproven_when_outlier_dependent():
    combined = {"net_dollars": 500.0, "outlier_share": 0.9, "n": 50}
    by_instrument = {"MES": _cell(20, 100.0), "MNQ": _cell(30, 400.0)}
    by_half = {"H1": _cell(25, 200.0), "H2": _cell(25, 300.0)}
    assert classify(combined, by_instrument, by_half) == "PROMISING_BUT_UNPROVEN"


def test_classify_watch_when_combined_zero_and_no_consistency_cells():
    combined = {"net_dollars": 0.0, "outlier_share": None, "n": 5}
    assert classify(combined, {}, {}) in ("WATCH", "REJECT")


def test_classify_promising_but_unproven_when_one_cell_is_outlier_dependent_even_if_combined_is_not():
    # Combined looks fine (20% outlier share), but the H1 half is a near-zero
    # result almost entirely propped up by a couple of trades (90% share) —
    # must not be labeled VALIDATED despite every cell being net-positive.
    combined = {"net_dollars": 500.0, "outlier_share": 0.2, "n": 50}
    by_instrument = {"MES": {**_cell(20, 100.0), "outlier_share": 0.1}, "MNQ": {**_cell(30, 400.0), "outlier_share": 0.15}}
    by_half = {"H1": {**_cell(25, 5.0), "outlier_share": 0.9}, "H2": {**_cell(25, 495.0), "outlier_share": 0.25}}
    assert classify(combined, by_instrument, by_half) == "PROMISING_BUT_UNPROVEN"


# ─── _co_occurring_real_strategy ─────────────────────────────────────────────

def test_co_occurring_real_strategy_reads_setup_strategy(tmp_path, monkeypatch):
    import scripts.missed_move_gate_sweep_622d as move_mod

    journal_dir = tmp_path / "MES"
    journal_dir.mkdir(parents=True)
    (journal_dir / "journal_2026-06-01.jsonl").write_text(
        json.dumps({
            "decision": "NO_TRADE",
            "bar_ts": "2026-06-01T14:00:00+00:00",
            "setup": {"strategy": "pdh_reclaim"},
        }) + "\n"
    )
    monkeypatch.setattr(move_mod, "JOURNAL_ROOT", tmp_path)
    row = _row(instrument="MES", day="2026-06-01", bar_ts="2026-06-01T14:00:00+00:00")
    assert _co_occurring_real_strategy(row) == "pdh_reclaim"


def test_co_occurring_real_strategy_none_when_no_matching_row(tmp_path, monkeypatch):
    import scripts.missed_move_gate_sweep_622d as move_mod

    journal_dir = tmp_path / "MES"
    journal_dir.mkdir(parents=True)
    (journal_dir / "journal_2026-06-01.jsonl").write_text("")
    monkeypatch.setattr(move_mod, "JOURNAL_ROOT", tmp_path)
    row = _row(instrument="MES", day="2026-06-01", bar_ts="2026-06-01T14:00:00+00:00")
    assert _co_occurring_real_strategy(row) is None


# ─── _breakdown ───────────────────────────────────────────────────────────────

def test_breakdown_groups_correctly():
    rows = [_row(instrument="MES"), _row(instrument="MNQ")]
    b = _breakdown(rows, lambda r: r.instrument)
    assert set(b.keys()) == {"MES", "MNQ"}
    assert b["MES"]["n"] == 1
