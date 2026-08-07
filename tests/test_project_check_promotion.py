from __future__ import annotations

import json
from pathlib import Path

from ops import project_check_promotion as pcp
from ops import proof_30_mnq as p30


def _journal(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "journal_2026-08-01.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return tmp_path


def test_gate_attrition_counts_rejections_and_winners():
    candidates = [
        {"strategy": "orb_breakout", "selected": True, "attempted": True, "direction": "LONG", "_instrument": "MNQ"},
        {"strategy": "orb_breakout", "selected": False, "attempted": False, "direction": "LONG", "failed_gates": ["MIN_RR"], "_instrument": "MNQ"},
        {"strategy": "orb_breakout", "selected": False, "attempted": False, "direction": "SHORT", "reject_code": "MAX_CONTRACTS_HARD_CAP", "_instrument": "MNQ"},
    ]
    result = pcp.gate_attrition(candidates)
    assert result["raw_candidate_count"] == 3
    assert result["selected_as_winner_count"] == 1
    assert result["attempted_count"] == 1
    assert result["reject_reason_counts"] == {"MIN_RR": 1, "MAX_CONTRACTS_HARD_CAP": 1, "none_recorded": 1}
    assert result["direction_counts"] == {"LONG": 2, "SHORT": 1}


def test_gate_attrition_none_recorded_when_no_reject_data():
    candidates = [{"strategy": "x", "selected": True, "attempted": True, "direction": "LONG", "_instrument": "MNQ"}]
    result = pcp.gate_attrition(candidates)
    assert result["reject_reason_counts"] == {"none_recorded": 1}


def test_performance_stats_no_trades():
    stats = pcp.performance_stats([])
    assert stats["available"] is False
    assert stats["n"] == 0


def test_performance_stats_win_loss_mix():
    summaries = [
        {"category": "filled_win_loss", "pnl_dollars": 40.0},
        {"category": "filled_win_loss", "pnl_dollars": -20.0},
        {"category": "cancelled_nofill", "pnl_dollars": 0.0},  # excluded — not filled
    ]
    stats = pcp.performance_stats(summaries)
    assert stats["available"] is True
    assert stats["n"] == 2
    assert stats["net_pnl"] == 20.0
    assert stats["win_rate"] == 0.5
    assert stats["profit_factor"] == 2.0
    assert stats["expectancy"] == 10.0


def test_performance_stats_all_wins_reports_undefined_profit_factor():
    stats = pcp.performance_stats([{"category": "filled_win_loss", "pnl_dollars": 10.0}])
    assert stats["profit_factor"] == "undefined_no_losses"


def test_accounting_identity_pass():
    gate = {"raw_candidate_count": 1}
    funnel = {"resolved_trades_total": 2, "fills": 1, "cancellations_no_fill": 1}
    result = pcp.accounting_identity(gate, funnel, order_attempts=3, strategy="orb_breakout")
    assert result["identity_check"] == "PASS"
    assert result["legitimately_open"] == 1


def test_accounting_identity_mismatch_when_resolved_exceeds_attempts():
    gate = {"raw_candidate_count": 1}
    funnel = {"resolved_trades_total": 5, "fills": 5, "cancellations_no_fill": 0}
    result = pcp.accounting_identity(gate, funnel, order_attempts=2, strategy="orb_breakout")
    assert result["identity_check"] == "MISMATCH"


def test_accounting_identity_not_evaluated_for_causal_strategies():
    gate = {"raw_candidate_count": 1}
    funnel = {"resolved_trades_total": 0, "fills": 0, "cancellations_no_fill": 0}
    result = pcp.accounting_identity(gate, funnel, order_attempts=0, strategy="strat_212")
    assert result["identity_check"] == "NOT_EVALUATED"


def test_advisory_classification_zero_fills_is_broken_when_candidates_exist():
    gate = {"raw_candidate_count": 5}
    funnel = {"fills": 0, "resolved_trades_total": 0}
    result = pcp.advisory_classification(gate, funnel, {"available": False}, {"identity_check": "PASS"})
    assert result["advisory_classification"] == "BROKEN"


def test_advisory_classification_zero_fills_zero_candidates_is_wait():
    gate = {"raw_candidate_count": 0}
    funnel = {"fills": 0, "resolved_trades_total": 0}
    result = pcp.advisory_classification(gate, funnel, {"available": False}, {"identity_check": "PASS"})
    assert result["advisory_classification"] == "WAIT"


def test_advisory_classification_mismatch_is_unsafe():
    gate = {"raw_candidate_count": 5}
    funnel = {"fills": 3, "resolved_trades_total": 3}
    result = pcp.advisory_classification(gate, funnel, {"available": False}, {"identity_check": "MISMATCH"})
    assert result["advisory_classification"] == "UNSAFE"


def test_advisory_classification_thin_sample_is_promising_unproven():
    gate = {"raw_candidate_count": 5}
    funnel = {"fills": 3, "resolved_trades_total": 3}
    perf = {"available": True, "net_pnl": 10.0, "profit_factor": 2.0}
    result = pcp.advisory_classification(gate, funnel, perf, {"identity_check": "PASS"})
    assert result["advisory_classification"] == "PROMISING BUT UNPROVEN"


def test_advisory_classification_adequate_sample_negative_pnl_is_broken():
    gate = {"raw_candidate_count": 40}
    funnel = {"fills": 30, "resolved_trades_total": 30}
    perf = {"available": True, "net_pnl": -10.0, "profit_factor": 0.8}
    result = pcp.advisory_classification(gate, funnel, perf, {"identity_check": "PASS"})
    assert result["advisory_classification"] == "BROKEN"


def test_advisory_classification_adequate_sample_low_pf_is_overfit():
    gate = {"raw_candidate_count": 40}
    funnel = {"fills": 30, "resolved_trades_total": 30}
    perf = {"available": True, "net_pnl": 5.0, "profit_factor": 1.05}
    result = pcp.advisory_classification(gate, funnel, perf, {"identity_check": "PASS"})
    assert result["advisory_classification"] == "OVERFIT"


def test_advisory_classification_adequate_sample_positive_is_validated():
    gate = {"raw_candidate_count": 40}
    funnel = {"fills": 30, "resolved_trades_total": 30}
    perf = {"available": True, "net_pnl": 500.0, "profit_factor": 2.0}
    result = pcp.advisory_classification(gate, funnel, perf, {"identity_check": "PASS"})
    assert result["advisory_classification"] == "VALIDATED"


def test_build_promotion_report_end_to_end(tmp_path):
    rows = [
        {
            "ts": "2026-08-01T14:30:00Z",
            "type": "DECISION",
            "decision": "TRADE",
            "instrument": "MNQ",
            "setup": {"strategy": "orb_breakout", "direction": "LONG", "entry": 100, "stop": 95, "target": 110},
            "risk_check": {"result": "APPROVED"},
            "paper_order_id": "ord-1",
            "candidate_audit": [{"strategy": "orb_breakout", "direction": "LONG", "selected": True, "attempted": True}],
        },
        {"ts": "2026-08-01T14:35:00Z", "type": "OUTCOME", "instrument": "MNQ", "outcome": {"result": "WIN", "exit_reason": "target_hit", "pnl_dollars": 40.0}},
    ]
    journal_dir = _journal(tmp_path, rows)
    report = pcp.build_promotion_report(journal_dir=journal_dir, strategy="orb_breakout", instrument="MNQ")
    assert report["strategy"] == "orb_breakout"
    assert report["paper_forward_evidence"]["execution_funnel"]["fills"] == 1
    assert report["paper_forward_evidence"]["accounting_identity"]["identity_check"] == "PASS"
    assert report["research_result"]["status"] == "NOT_EVALUATED"
    assert report["classification"]["requires_human_judgment"] is True
