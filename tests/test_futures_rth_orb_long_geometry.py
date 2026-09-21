from __future__ import annotations

from datetime import datetime, timedelta, timezone

from alert_ranker.causal_bars import Bar
from research.futures_rth_orb_long_geometry import (
    Candidate,
    GEOMETRIES,
    TradeRow,
    build_report,
    geometry_prices,
    pass_rule,
    simulate_candidate,
)


BASE = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)


def _bar(i: int, *, o: float, h: float, l: float, c: float) -> Bar:
    return Bar(
        start=BASE + timedelta(minutes=5 * i),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=1000.0,
        vwap=c,
    )


def _candidate(instrument: str = "MES") -> Candidate:
    return Candidate(
        instrument=instrument,
        session_date="2026-09-18",
        episode_id="2026-09-18:MES:ORB_BREAKOUT_LONG:LONG:1",
        trigger_bar_start=BASE.isoformat(),
        trigger_idx=0,
        trigger_prev_close=99.9,
        trigger_close=100.5,
        trigger_low=99.5,
        orb_high=100.0,
        orb_low=96.0,
        payload_orb_high=100.0,
    )


def test_frozen_geometries_are_structural_and_target_from_decision_open():
    cand = _candidate()
    decision = 101.0

    g1_stop, g1_target = geometry_prices(cand, decision, "G1_CANONICAL_OFFSET")
    assert g1_stop == 96.0  # MES 16 ticks = 4 points
    assert round(g1_target, 4) == 112.0  # 101 + 2.2 * 5

    g2_stop, g2_target = geometry_prices(cand, decision, "G2_TRIGGER_LOW")
    assert g2_stop == 99.25
    assert g2_target == 104.5

    g3_stop, g3_target = geometry_prices(cand, decision, "G3_ORB_MIDPOINT")
    assert g3_stop == 97.75
    assert g3_target == 107.5
    assert set(GEOMETRIES) == {
        "G1_CANONICAL_OFFSET",
        "G2_TRIGGER_LOW",
        "G3_ORB_MIDPOINT",
    }


def test_legacy_payload_identity_requires_an_actual_cross():
    cand = _candidate()
    bars = [
        _bar(0, o=99.8, h=101.0, l=99.2, c=100.5),
        _bar(1, o=101.0, h=102.0, l=100.5, c=101.5),
    ]
    crossed = simulate_candidate(
        cand,
        bars,
        geometry="G2_TRIGGER_LOW",
        slippage_label="base",
        slippage_ticks=1.0,
    )
    assert crossed.legacy_boundary_same_bar is True

    # Same trigger close above the payload ORB is not a crossing if the prior
    # close was already above it.
    stale = Candidate(
        **{
            **cand.__dict__,
            "trigger_prev_close": 100.25,
        }
    )
    not_crossed = simulate_candidate(
        stale,
        bars,
        geometry="G2_TRIGGER_LOW",
        slippage_label="base",
        slippage_ticks=1.0,
    )
    assert not_crossed.legacy_boundary_same_bar is False


def test_derived_futures_prices_are_conservatively_tick_aligned():
    cand = Candidate(
        **{
            **_candidate().__dict__,
            "orb_low": 96.25,  # midpoint makes G3 structural stop half-tick
        }
    )
    stop, target = geometry_prices(cand, 101.25, "G3_ORB_MIDPOINT")
    assert stop % 0.25 == 0.0
    assert target % 0.25 == 0.0
    # Raw midpoint stop would be 97.875; conservative LONG rounding is 97.75.
    assert stop == 97.75
    raw_target = 101.25 + 2.0 * (101.25 - 97.75)
    assert target <= raw_target


def test_entry_uses_next_bar_open_not_trigger_close():
    cand = _candidate()
    bars = [
        _bar(0, o=99.8, h=101.0, l=99.2, c=100.5),
        _bar(1, o=102.0, h=103.0, l=101.0, c=102.5),
    ]
    row = simulate_candidate(
        cand,
        bars,
        geometry="G2_TRIGGER_LOW",
        slippage_label="base",
        slippage_ticks=1.0,
    )
    assert row.decision_open == 102.0
    assert row.fill_entry == 102.25
    assert row.decision_open != cand.trigger_close
    assert row.detachment_ticks == 8.0


def test_same_entry_bar_straddle_is_pessimistic_stop_first():
    cand = _candidate()
    bars = [
        _bar(0, o=99.5, h=101.0, l=99.0, c=100.5),
        # G1 decision=100 -> stop=96, target=108.8; both hit after open.
        _bar(1, o=100.0, h=110.0, l=95.0, c=101.0),
    ]
    row = simulate_candidate(
        cand,
        bars,
        geometry="G1_CANONICAL_OFFSET",
        slippage_label="base",
        slippage_ticks=1.0,
    )
    assert row.status == "RESOLVED"
    assert row.result == "LOSS"
    assert row.exit_reason == "STOP_HIT"
    assert row.bars_held == 0


def test_research_sim_hard_disables_webull_mirror(monkeypatch):
    monkeypatch.setenv("WEBULL_FUTURES_MIRROR_ENABLED", "true")
    cand = _candidate()
    bars = [
        _bar(0, o=99.5, h=101.0, l=99.0, c=100.5),
        _bar(1, o=100.0, h=101.0, l=99.0, c=100.0),
    ]
    simulate_candidate(
        cand,
        bars,
        geometry="G2_TRIGGER_LOW",
        slippage_label="base",
        slippage_ticks=1.0,
    )
    import os

    assert os.environ["WEBULL_FUTURES_MIRROR_ENABLED"] == "false"


def test_report_keeps_raw_and_risk_feasible_views_separate():
    def row(*, episode: str, over_cap: bool, pnl: float) -> TradeRow:
        return TradeRow(
            instrument="MNQ",
            session_date="2026-01-15",
            half="H1",
            episode_id=episode,
            geometry="G1_CANONICAL_OFFSET",
            slippage_label="base",
            slippage_ticks=1.0,
            trigger_bar_start=BASE.isoformat(),
            trigger_close=100.5,
            decision_open=101.0,
            fill_entry=101.25,
            orb_high=100.0,
            orb_low=96.0,
            stop=90.0 if over_cap else 98.0,
            target=108.0,
            stop_ticks=200.0 if over_cap else 12.0,
            over_stop_cap=over_cap,
            detachment_ticks=4.0,
            status="RESOLVED",
            result="WIN" if pnl > 0 else "LOSS",
            exit_reason="TARGET_HIT" if pnl > 0 else "STOP_HIT",
            exit_price=102.0,
            gross_pnl=pnl + 1.24,
            net_pnl=pnl,
            bars_held=1,
            payload_orb_high=100.0,
            payload_orb_equal=True,
            legacy_boundary_same_bar=True,
        )

    rows = [
        row(episode="keep", over_cap=False, pnl=10.0),
        row(episode="drop-secondary-only", over_cap=True, pnl=-50.0),
    ]
    meta = {
        "MNQ": {"candidates": 2, "skipped": {}},
        "MES": {"candidates": 2, "skipped": {}},
    }
    # Duplicate the same synthetic rows into both instruments only to exercise
    # report structure; the primary/raw gate still reads raw cells.
    report = build_report({"MNQ": rows, "MES": rows}, meta)
    raw = report["instruments"]["MNQ"]["cells"]["G1_CANONICAL_OFFSET:base"]
    feasible = report["instruments"]["MNQ"]["risk_feasible_cells"]["G1_CANONICAL_OFFSET:base"]
    assert raw["resolved"] == 2
    assert raw["net"] == -40.0
    assert feasible["resolved"] == 1
    assert feasible["net"] == 10.0


def _cell(*, h1_n=120, h2_n=120, h1_net=100.0, h2_net=100.0, h1_pf=1.2, h2_pf=1.2, month=0.3):
    return {
        "halves": {
            "H1": {"n": h1_n, "net": h1_net, "pf": h1_pf},
            "H2": {"n": h2_n, "net": h2_net, "pf": h2_pf},
        },
        "top_positive_month_share": month,
    }


def test_preregistered_gate_requires_cross_instrument_stress_and_halves():
    geometry = "G1_CANONICAL_OFFSET"
    report = {
        "instruments": {
            "MNQ": {
                "cells": {
                    f"{geometry}:base": _cell(),
                    f"{geometry}:stress": _cell(h1_pf=1.0, h2_pf=1.0),
                }
            },
            "MES": {
                "cells": {
                    f"{geometry}:base": _cell(),
                    f"{geometry}:stress": _cell(h1_pf=1.0, h2_pf=1.0),
                }
            },
        }
    }
    good = pass_rule(report, geometry)
    assert good["numeric_pass"] is True
    assert good["passes"] is False
    assert good["classification"] == "WAIT"
    assert good["manual_gate_remaining"] == "identity_and_causality_audit"

    report["instruments"]["MES"]["cells"][f"{geometry}:stress"]["halves"]["H2"]["net"] = -1.0
    result = pass_rule(report, geometry)
    assert result["numeric_pass"] is False
    assert result["passes"] is False
    assert "MES:H2:stress_net_not_positive" in result["reasons"]