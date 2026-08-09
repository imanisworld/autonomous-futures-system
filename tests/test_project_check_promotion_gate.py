from __future__ import annotations

import json
from pathlib import Path

from ops.promotion_gate import evaluate_promotion_evidence, run


def _base_evidence(**overrides):
    evidence = {
        "strategy": "orb_breakout_inverse",
        "identity_parity": {
            "raw_candidate_count": 40,
            "candidate_identity_parity": True,
            "direction_parity": True,
            "entry_stop_target_parity": True,
            "timeframe_parity": True,
            "causal_data_availability": True,
            "lookahead_or_partial_bar_dependency": "none",
        },
        "gate_attrition": {"market_condition": 40, "trend_strength": 22, "confluence": 10},
        "execution": {
            "candidates_reaching_risk_engine": 10,
            "candidates_approved": 8,
            "entry_attempts": 8,
            "fills": 5,
            "cancellations": 2,
            "rejects_or_known_no_fills": 1,
            "resolved_outcomes": 4,
            "legitimately_open_positions": 1,
        },
        "performance": {
            "net_pnl": 1200.0,
            "profit_factor": 1.8,
            "expectancy": 60.0,
            "win_rate": 0.55,
        },
        "execution_context": {
            "actual_entry_model": "ioc_limit",
            "actual_effective_tolerance": "12 ticks",
            "actual_commission_slippage_assumptions": "2 tick adverse, $4.20 rt commission",
            "contract_quantity": 1,
            "account_risk_caps": "20% drawdown breaker",
        },
        "classification": {
            "research_result": "positive",
            "runtime_parity": "confirmed via ReplayEngine/DecisionEngine/RiskEngine/PaperBroker",
            "paper_forward_evidence": "collecting",
            "final": "PROMISING BUT UNPROVEN",
        },
        "attestation": {
            "rescue_or_tuning_variant_in_same_pass": False,
            "risk_controls_exempted_to_reproduce_research": False,
            "zero_executable_fills": False,
        },
    }
    evidence.update(overrides)
    return evidence


def test_clean_evidence_passes_and_reports_declared_classification():
    report = evaluate_promotion_evidence(_base_evidence())
    assert report["gate_verdict"] == "EVIDENCE_CONSISTENT (PROMISING BUT UNPROVEN)"
    assert report["blockers"] == []
    assert report["missing_fields"] == []
    assert report["accounting"]["ok"] is True


def test_accounting_identity_violation_is_blocked():
    evidence = _base_evidence()
    evidence["execution"]["fills"] = 999  # breaks both identities
    report = evaluate_promotion_evidence(evidence)
    assert report["gate_verdict"] == "BLOCKED"
    codes = {b["code"] for b in report["blockers"]}
    assert "accounting_identity_violation" in codes


def test_missing_sections_are_reported_not_fabricated():
    evidence = _base_evidence()
    del evidence["performance"]
    report = evaluate_promotion_evidence(evidence)
    assert report["gate_verdict"] == "INCOMPLETE"
    assert any(f.startswith("performance.") for f in report["missing_fields"])


def test_rescue_variant_attestation_blocks():
    evidence = _base_evidence()
    evidence["attestation"]["rescue_or_tuning_variant_in_same_pass"] = True
    report = evaluate_promotion_evidence(evidence)
    assert report["gate_verdict"] == "BLOCKED"
    codes = {b["code"] for b in report["blockers"]}
    assert "rescue_variant_in_same_pass" in codes


def test_risk_exemption_attestation_blocks():
    evidence = _base_evidence()
    evidence["attestation"]["risk_controls_exempted_to_reproduce_research"] = True
    report = evaluate_promotion_evidence(evidence)
    assert report["gate_verdict"] == "BLOCKED"
    codes = {b["code"] for b in report["blockers"]}
    assert "risk_controls_exempted" in codes


def test_zero_fills_cannot_be_classified_validated():
    evidence = _base_evidence()
    evidence["execution"].update({
        "entry_attempts": 3,
        "fills": 0,
        "cancellations": 2,
        "rejects_or_known_no_fills": 1,
        "resolved_outcomes": 0,
        "legitimately_open_positions": 0,
    })
    evidence["attestation"]["zero_executable_fills"] = True
    evidence["classification"]["final"] = "VALIDATED"
    report = evaluate_promotion_evidence(evidence)
    assert report["gate_verdict"] == "BLOCKED"
    codes = {b["code"] for b in report["blockers"]}
    assert "validated_with_zero_fills" in codes


def test_invalid_classification_value_is_blocked():
    evidence = _base_evidence()
    evidence["classification"]["final"] = "PRETTY GOOD I GUESS"
    report = evaluate_promotion_evidence(evidence)
    codes = {b["code"] for b in report["blockers"]}
    assert "invalid_classification_value" in codes


def test_strategy_name_mismatch_is_blocked():
    report = evaluate_promotion_evidence(_base_evidence(), strategy="some_other_strategy")
    codes = {b["code"] for b in report["blockers"]}
    assert "strategy_name_mismatch" in codes


def test_run_reads_evidence_file_and_never_writes(tmp_path):
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(_base_evidence()), encoding="utf-8")
    before = evidence_path.read_text(encoding="utf-8")

    report = run(evidence_path, strategy="orb_breakout_inverse")

    assert report["gate_verdict"] == "EVIDENCE_CONSISTENT (PROMISING BUT UNPROVEN)"
    assert evidence_path.read_text(encoding="utf-8") == before


def test_run_missing_file_is_blocked_not_raised(tmp_path):
    report = run(tmp_path / "does_not_exist.json", strategy="x")
    assert report["gate_verdict"] == "BLOCKED"
    assert report["blockers"][0]["code"] == "evidence_file_not_found"
