from __future__ import annotations

import json

from scripts.shadow_gate_choke_sweep_622d import (
    MIN_CELL_N,
    ShadowRow,
    _breakdown,
    _classify_cell,
    _summarize,
    collect_shadow_rows,
)


def _row(**kw):
    base = dict(
        instrument="MES",
        day="2026-06-01",
        bar_ts="2026-06-01T14:00:00+00:00",
        gate="REGIME_NOT_FULL",
        session="ny_am",
        market_condition="TRENDING",
        shadow_strategy="strat_22_reversal_observed",
        direction="LONG",
        result="WIN",
        entry_filled=True,
        pnl_ticks=40.0,
    )
    base.update(kw)
    return ShadowRow(**base)


# ─── pnl_dollars conversion ──────────────────────────────────────────────────

def test_pnl_dollars_uses_tick_value_for_instrument():
    row = _row(instrument="MES", pnl_ticks=40.0)
    assert row.pnl_dollars() == 40.0 * 1.25


def test_pnl_dollars_none_when_ticks_none():
    row = _row(pnl_ticks=None)
    assert row.pnl_dollars() is None


# ─── Cell classification ────────────────────────────────────────────────────

def test_classify_cell_insufficient_data_below_min_n():
    rows = [_row(result="WIN", pnl_ticks=10.0) for _ in range(MIN_CELL_N - 1)]
    assert _classify_cell(rows) == "INSUFFICIENT_DATA"


def test_classify_cell_valid_shadow_candidate_when_net_positive_and_good_win_rate():
    rows = [_row(result="WIN", pnl_ticks=40.0) for _ in range(10)] + [_row(result="LOSS", pnl_ticks=-20.0) for _ in range(8)]
    assert len(rows) >= MIN_CELL_N
    assert _classify_cell(rows) == "VALID_SHADOW_CANDIDATE"


def test_classify_cell_bad_counterfactual_when_net_negative_and_low_win_rate():
    rows = [_row(result="LOSS", pnl_ticks=-40.0) for _ in range(12)] + [_row(result="WIN", pnl_ticks=10.0) for _ in range(5)]
    assert len(rows) >= MIN_CELL_N
    assert _classify_cell(rows) == "BAD_COUNTERFACTUAL"


def test_classify_cell_mixed_when_neither_condition_met():
    # net positive but win rate too low to call VALID; not negative enough for BAD either
    rows = [_row(result="WIN", pnl_ticks=100.0) for _ in range(6)] + [_row(result="LOSS", pnl_ticks=-10.0) for _ in range(14)]
    assert len(rows) >= MIN_CELL_N
    assert _classify_cell(rows) == "MIXED"


# ─── Summary aggregation ─────────────────────────────────────────────────────

def test_summarize_aggregation_math():
    rows = [_row(result="WIN", pnl_ticks=40.0), _row(result="LOSS", pnl_ticks=-20.0)]
    s = _summarize(rows)
    assert s["n"] == 2
    assert s["wins"] == 1
    assert s["losses"] == 1
    assert s["win_rate"] == 0.5
    assert s["net_dollars"] == round(40.0 * 1.25 + (-20.0) * 1.25, 2)
    assert s["expectancy_dollars"] == round((40.0 * 1.25 + (-20.0) * 1.25) / 2, 2)


def test_summarize_empty_rows():
    s = _summarize([])
    assert s["n"] == 0
    assert s["net_dollars"] == 0.0
    assert s["expectancy_dollars"] is None
    assert s["classification"] == "INSUFFICIENT_DATA"


# ─── Breakdown grouping ──────────────────────────────────────────────────────

def test_breakdown_groups_by_key_fn():
    rows = [_row(gate="REGIME_NOT_FULL"), _row(gate="WEAK_BAR_CLOSE")]
    b = _breakdown(rows, lambda r: r.gate)
    assert set(b.keys()) == {"REGIME_NOT_FULL", "WEAK_BAR_CLOSE"}
    assert b["REGIME_NOT_FULL"]["n"] == 1


# ─── collect_shadow_rows: dominant-gate ranking on a small fixture ──────────

def _write_journal_and_candles(tmp_path, monkeypatch, instrument, day, no_trade_rows, candle_bars):
    import scripts.missed_move_gate_sweep_622d as move_mod

    journal_dir = tmp_path / "journals" / instrument
    journal_dir.mkdir(parents=True, exist_ok=True)
    (journal_dir / f"journal_{day}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in no_trade_rows) + "\n"
    )

    candle_dir = tmp_path / "candles" / instrument
    candle_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for ts, o, h, l, c in candle_bars:
        lines.append(json.dumps({
            "timestamp": ts, "instrument": instrument, "session": "ny_am",
            "open": o, "high": h, "low": l, "close": c, "volume": 1000, "vwap": c,
            "orb_high": h, "orb_low": l, "orb_status": None,
            "market_condition": "TRENDING", "trend_direction": "UP", "trend_strength": "STRONG",
            "previous_day_high": h, "previous_day_low": l, "previous_day_close": c,
        }))
    (candle_dir / f"{instrument}_{day}.jsonl").write_text("\n".join(lines) + "\n")

    monkeypatch.setattr(move_mod, "JOURNAL_ROOT", tmp_path / "journals")
    monkeypatch.setattr(move_mod, "CANDLE_ROOT", tmp_path / "candles")


def test_collect_shadow_rows_excludes_unfilled_and_unresolved(tmp_path, monkeypatch):
    import scripts.shadow_gate_choke_sweep_622d as sweep_mod

    day = "2026-06-01"
    bars = [
        (f"2026-06-01T{h:02d}:00:00+00:00", 100 + h, 105 + h, 98 + h, 102 + h)
        for h in range(4)
    ]
    bars[1] = ("2026-06-01T01:00:00+00:00", 102, 140, 60, 100)  # large-range block
    rows = [
        {
            "decision": "NO_TRADE",
            "bar_ts": "2026-06-01T00:00:00+00:00",
            "failed_gates": ["REGIME_NOT_FULL"],
            "session": "ny_am",
            "market_condition": "TRENDING",
            "shadow_candidates": [
                {"strategy": "strat_a", "direction": "LONG", "outcome": {"result": "WIN", "entry_filled": True, "pnl_ticks": 40.0}},
                {"strategy": "strat_a", "direction": "LONG", "outcome": {"result": "OPEN", "entry_filled": True, "pnl_ticks": None}},
                {"strategy": "strat_a", "direction": "LONG", "outcome": {"result": "NO_FILL", "entry_filled": False, "pnl_ticks": None}},
            ],
        },
    ]
    _write_journal_and_candles(tmp_path, monkeypatch, "MES", day, rows, bars)
    monkeypatch.setattr(sweep_mod, "_instruments", lambda: ["MES"])

    resolved_rows, excluded, total = sweep_mod.collect_shadow_rows()
    assert len(resolved_rows) == 1
    assert resolved_rows[0].result == "WIN"
    assert excluded == 2  # OPEN + NO_FILL
    assert total == 1
