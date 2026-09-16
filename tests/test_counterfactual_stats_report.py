from __future__ import annotations

from pathlib import Path

import pytest

from scripts.counterfactual_stats_report import build_report, validate_rows


def test_aggregates_fill_realism_statistics_without_replaying_research_logic():
    rows = [
        {"cohort": "A", "filled": True, "pnl_dollars": 100.0, "mae_r": 0.5, "mfe_r": 2.0},
        {"cohort": "A", "filled": True, "pnl_dollars": -50.0, "mae_r": 1.0, "mfe_r": 1.0},
        {"cohort": "A", "filled": False, "pnl_dollars": None},
        {"cohort": "B", "filled": True, "pnl_dollars": -10.0},
    ]
    report = build_report(rows, hypothetical_costs=(0.0, 2.0, 4.0))
    assert report["authority"] == "aggregation_only"
    assert report["research_logic_replayed"] is False
    assert report["commission_configured"] is False

    a = report["cohorts"]["A"]
    assert a["candidates"] == 3
    assert a["fills"] == 2
    assert a["no_fills"] == 1
    assert a["fill_rate_percent"] == pytest.approx(66.67)
    assert a["win_rate_percent"] == 50.0
    assert a["gross_pnl_dollars"] == 50.0
    assert a["profit_factor"] == 2.0
    assert a["h1_pnl_dollars"] == 100.0
    assert a["h2_pnl_dollars"] == -50.0
    assert a["worst_trade_dollars"] == -50.0
    assert a["max_drawdown_dollars"] == 50.0
    assert a["mean_mae_r"] == 0.75
    assert a["mean_mfe_r"] == 1.5
    assert a["break_even_round_trip_cost_dollars"] == 25.0
    assert a["hypothetical_cost_sensitivity"] == {"0": 50.0, "2": 46.0, "4": 42.0}

    b = report["cohorts"]["B"]
    assert b["gross_pnl_dollars"] == -10.0
    assert b["break_even_round_trip_cost_dollars"] == -10.0


def test_validation_fails_closed_on_missing_or_inconsistent_fill_fields():
    with pytest.raises(ValueError, match="cohort is required"):
        validate_rows([{"filled": True, "pnl_dollars": 1.0}])
    with pytest.raises(ValueError, match="filled must be boolean"):
        validate_rows([{"cohort": "A", "filled": 1, "pnl_dollars": 1.0}])
    with pytest.raises(ValueError, match="filled row requires pnl_dollars"):
        validate_rows([{"cohort": "A", "filled": True, "pnl_dollars": None}])
    with pytest.raises(ValueError, match="no-fill row must not carry pnl_dollars"):
        validate_rows([{"cohort": "A", "filled": False, "pnl_dollars": 5.0}])


def test_all_no_fill_cohort_is_reported_without_fabricating_performance():
    report = build_report(
        [
            {"cohort": "C", "filled": False, "pnl_dollars": None},
            {"cohort": "C", "filled": False, "pnl_dollars": None},
        ]
    )
    c = report["cohorts"]["C"]
    assert c["fills"] == 0
    assert c["no_fills"] == 2
    assert c["gross_pnl_dollars"] == 0.0
    assert c["profit_factor"] is None
    assert c["win_rate_percent"] is None
    assert c["worst_trade_dollars"] is None
    assert c["break_even_round_trip_cost_dollars"] is None


def test_counterfactual_reporter_has_no_runtime_trade_imports():
    source = (Path(__file__).resolve().parents[1] / "scripts" / "counterfactual_stats_report.py").read_text(encoding="utf-8")
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
