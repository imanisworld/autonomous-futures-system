from __future__ import annotations

from pathlib import Path

import pytest

from scripts.counterfactual_stats_report import build_report, validate_rows


def _row(**overrides):
    row = {
        "cohort": "A",
        "sample_half": "H1",
        "sequence": 0,
        "ts": "2026-09-01T10:00:00+00:00",
        "filled": True,
        "pnl_dollars": 1.0,
    }
    row.update(overrides)
    return row


def test_aggregates_with_producer_defined_halves_and_order():
    # Deliberately shuffled input. Producer sequence defines A P&L order as
    # -100, +200, -50, so gross max drawdown is 100.
    rows = [
        _row(sequence=1, ts="2026-09-01T11:00:00+00:00", pnl_dollars=200.0, mae_r=0.5, mfe_r=2.0),
        _row(
            sequence=2,
            ts="2026-09-01T12:00:00+00:00",
            sample_half="H2",
            pnl_dollars=-50.0,
            mae_r=0.25,
            mfe_r=1.0,
        ),
        _row(sequence=0, ts="2026-09-01T10:00:00+00:00", pnl_dollars=-100.0, mae_r=1.0, mfe_r=0.5),
        _row(
            sequence=3,
            ts="2026-09-01T12:15:00+00:00",
            sample_half="H2",
            filled=False,
            pnl_dollars=None,
        ),
        _row(
            cohort="B",
            sequence=0,
            ts="2026-09-01T13:00:00+00:00",
            sample_half="H1",
            pnl_dollars=-10.0,
        ),
    ]
    report = build_report(rows, hypothetical_costs=(0.0, 2.0, 4.0))
    assert report["authority"] == "aggregation_only"
    assert report["research_logic_replayed"] is False
    assert report["sample_split_source"] == "input.sample_half"
    assert report["performance_order_source"] == "input.sequence"
    assert report["timestamp_role"] == "provenance_only"
    assert report["performance_basis"] == "gross_before_hypothetical_costs"
    assert report["commission_configured"] is False

    a = report["cohorts"]["A"]
    assert a["candidates"] == 4
    assert a["fills"] == 3
    assert a["no_fills"] == 1
    assert a["fill_rate_percent"] == 75.0
    assert a["win_rate_percent"] == pytest.approx(33.33)
    assert a["gross_pnl_dollars"] == 50.0
    assert a["profit_factor"] == pytest.approx(1.333333)
    assert a["sample_halves_present"] == ["H1", "H2"]
    assert a["half_coverage_complete"] is True
    assert a["h1_candidates"] == 2
    assert a["h2_candidates"] == 2
    assert a["h1_fills"] == 2
    assert a["h2_fills"] == 1
    assert a["h1_pnl_dollars"] == 100.0
    assert a["h2_pnl_dollars"] == -50.0
    assert a["worst_trade_dollars"] == -100.0
    assert a["gross_max_drawdown_dollars"] == 100.0
    assert a["mean_mae_r"] == pytest.approx(0.5833)
    assert a["mean_mfe_r"] == pytest.approx(1.1667)
    assert a["break_even_round_trip_cost_dollars"] == pytest.approx(16.6667)
    assert a["hypothetical_cost_sensitivity"] == {"0": 50.0, "2": 44.0, "4": 38.0}

    b = report["cohorts"]["B"]
    assert b["gross_pnl_dollars"] == -10.0
    assert b["break_even_round_trip_cost_dollars"] == -10.0
    assert b["sample_halves_present"] == ["H1"]
    assert b["half_coverage_complete"] is False


def test_validation_fails_closed_on_missing_or_ambiguous_provenance():
    with pytest.raises(ValueError, match="cohort is required"):
        validate_rows([_row(cohort="")])
    with pytest.raises(ValueError, match="sample_half must be H1 or H2"):
        validate_rows([_row(sample_half="")])
    with pytest.raises(ValueError, match="sequence must be an integer"):
        validate_rows([_row(sequence=None)])
    with pytest.raises(ValueError, match="sequence cannot be negative"):
        validate_rows([_row(sequence=-1)])
    with pytest.raises(ValueError, match="ts is required"):
        validate_rows([_row(ts="")])
    with pytest.raises(ValueError, match="ts must include a UTC offset"):
        validate_rows([_row(ts="2026-09-01T10:00:00")])
    with pytest.raises(ValueError, match="filled must be boolean"):
        validate_rows([_row(filled=1)])
    with pytest.raises(ValueError, match="filled row requires pnl_dollars"):
        validate_rows([_row(pnl_dollars=None)])
    with pytest.raises(ValueError, match="no-fill row must not carry pnl_dollars"):
        validate_rows([_row(filled=False, pnl_dollars=5.0)])
    with pytest.raises(ValueError, match="pnl_dollars must be finite"):
        validate_rows([_row(pnl_dollars=float("nan"))])
    with pytest.raises(ValueError, match="mae_r cannot be negative"):
        validate_rows([_row(mae_r=-0.1)])
    with pytest.raises(ValueError, match="duplicate sequence 0 in cohort A"):
        validate_rows([_row(), _row(ts="2026-09-01T11:00:00+00:00")])


def test_costs_and_empty_input_fail_closed():
    with pytest.raises(ValueError, match="contains no rows"):
        build_report([])
    with pytest.raises(ValueError, match="cannot be negative"):
        build_report([_row()], hypothetical_costs=(-1.0,))
    with pytest.raises(ValueError, match="must be finite"):
        build_report([_row()], hypothetical_costs=(float("inf"),))


def test_all_no_fill_cohort_is_reported_without_fabricating_performance():
    report = build_report(
        [
            _row(filled=False, pnl_dollars=None),
            _row(
                sequence=1,
                ts="2026-09-01T11:00:00+00:00",
                sample_half="H2",
                filled=False,
                pnl_dollars=None,
            ),
        ]
    )
    a = report["cohorts"]["A"]
    assert a["fills"] == 0
    assert a["no_fills"] == 2
    assert a["gross_pnl_dollars"] == 0.0
    assert a["profit_factor"] is None
    assert a["win_rate_percent"] is None
    assert a["worst_trade_dollars"] is None
    assert a["break_even_round_trip_cost_dollars"] is None
    assert a["h1_fills"] == 0
    assert a["h2_fills"] == 0
    assert a["half_coverage_complete"] is True


def test_counterfactual_reporter_has_no_runtime_trade_imports():
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "counterfactual_stats_report.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "from strategy",
        "import strategy",
        "from risk",
        "import risk",
        "from execution",
        "import execution",
        "from webhook",
        "import webhook",
        "PaperBroker",
        "RiskEngine",
        "DecisionEngine",
    ):
        assert forbidden not in source
