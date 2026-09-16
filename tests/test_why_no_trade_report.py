from __future__ import annotations

from pathlib import Path

from scripts.why_no_trade_report import build_report, decision_bar


def _row(**overrides):
    row = {
        "ts": "2026-09-16T09:00:00+00:00",
        "instrument": "MNQ",
        "session": "london",
        "timeframe_minutes": 15,
        "decision": "NO_TRADE",
        "reason": "Market condition RANGE_BOUND is not TRENDING.",
        "market_condition": "RANGE_BOUND",
        "regime": None,
        "failed_gates": ["MARKET_CONDITION_NOT_TRENDING"],
        "candidate_audit": [],
        "setup": None,
        "context": {
            "instrument": "MNQ",
            "market_condition": "RANGE_BOUND",
            "structural_market_condition": "STRUCTURAL_TREND_UP",
            "structural_direction": "UP",
            "structural_mismatch": True,
            "structural_gate_authoritative": False,
        },
    }
    row.update(overrides)
    return row


def test_per_bar_chain_preserves_authoritative_and_observation_fields():
    bar = decision_bar(_row())
    assert bar["market_condition"] == "RANGE_BOUND"
    assert bar["structural_market_condition"] == "STRUCTURAL_TREND_UP"
    assert bar["structural_mismatch"] is True
    assert bar["structural_gate_authoritative"] is False
    assert bar["failed_gates"] == ["MARKET_CONDITION_NOT_TRENDING"]
    assert bar["primary_rejection"] == "MARKET_CONDITION_NOT_TRENDING"
    assert bar["candidate_count"] == 0
    assert bar["selected_setup"] is None


def test_report_surfaces_signal_starvation_without_promoting_structure():
    second = _row(
        ts="2026-09-16T12:00:00+00:00",
        market_condition="TRENDING",
        regime="FULL_LONG",
        failed_gates=[],
        reason="No qualifying setup found.",
        context={
            "instrument": "MNQ",
            "market_condition": "TRENDING",
            "structural_market_condition": "STRUCTURAL_TREND_UP",
            "structural_direction": "UP",
            "structural_mismatch": False,
            "structural_gate_authoritative": False,
        },
        candidate_audit=[
            {
                "strategy": "strat_22_reversal",
                "direction": "LONG",
                "selected": False,
                "attempted": False,
                "reject_code": "NOT_EXECUTABLE",
            }
        ],
    )
    outcome = {"type": "OUTCOME", "instrument": "MNQ", "outcome": {"result": "WIN"}}
    report = build_report([_row(), second, outcome])
    assert report["authority"] == "journal_read_only"
    assert report["structural_gate_authoritative"] is False
    assert report["decision_bars"] == 2
    summary = report["summary"]
    assert summary["bars_with_candidates"] == 1
    assert summary["bars_without_candidates"] == 1
    assert summary["structural_trend_bars"] == 2
    assert summary["structural_trend_pine_nontrending"] == 1
    assert summary["structural_mismatch_true"] == 1
    assert summary["failed_gates"] == {"MARKET_CONDITION_NOT_TRENDING": 1}
    assert summary["candidate_strategies"] == {"strat_22_reversal": 1}


def test_risk_rejection_becomes_primary_rejection_when_no_decision_gate():
    row = _row(
        failed_gates=[],
        risk_check={"result": "REJECTED", "failed_rule": "max_daily_loss", "reason": "cap"},
    )
    assert decision_bar(row)["primary_rejection"] == "max_daily_loss"


def test_report_script_is_reporting_only():
    source = (Path(__file__).resolve().parents[1] / "scripts" / "why_no_trade_report.py").read_text(encoding="utf-8")
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
