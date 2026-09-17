from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ops.project_check.demo_qualification import build_demo_qualification_report


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _runtime_snapshot(**_kwargs) -> dict:
    return {
        "entry_fill_model": "ioc_limit",
        "entry_tolerance_ticks": {
            "MNQ": {
                "effective_replay_paper": 32,
                "effective_live_broker": 32,
                "diverges": False,
            }
        },
        "quantity_caps": {
            "max_contracts_per_instrument_config": {"MNQ": 1},
            "hard_cap_env": 1,
        },
    }


def _complete_evidence(tmp_path: Path) -> dict:
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"dataset":"frozen"}\n', encoding="utf-8")
    risk_rules = tmp_path / "risk_rules.yaml"
    risk_rules.write_text("version: test\n", encoding="utf-8")
    return {
        "strategy": "example",
        "identity_parity": {
            "raw_candidate_count": 60,
            "candidate_identity_parity": True,
            "direction_parity": True,
            "entry_stop_target_parity": True,
            "timeframe_parity": True,
            "causal_data_availability": True,
            "lookahead_or_partial_bar_dependency": False,
        },
        "execution": {
            "candidates_reaching_risk_engine": 45,
            "approved": 40,
            "entry_attempts": 40,
            "fills": 40,
            "cancellations": 0,
            "rejects_or_known_no_fills": 0,
            "resolved_outcomes": 40,
            "legitimately_open": 0,
        },
        "execution_context_claimed": {
            "instrument": "MNQ",
            "entry_fill_model": "ioc_limit",
            "entry_tolerance_ticks": 32,
            "contract_qty": 1,
            "commission_slippage_assumptions": "$1.48 round turn, 1 adverse tick baseline",
        },
        "stated_classification": "PROMISING BUT UNPROVEN",
        "change_scope": {
            "strategy_or_parameter_only": True,
            "risk_policy_unchanged": True,
            "execution_path_unchanged": True,
            "broker_routing_unchanged": True,
            "session_contract_semantics_unchanged": True,
        },
        "canonical_replay": {
            "real_replay_engine": True,
            "real_decision_engine": True,
            "real_risk_engine": True,
            "real_paper_broker": True,
            "replay_live_logic_confirmed": True,
            "same_strategy_formula_confirmed": True,
        },
        "data_integrity": {
            "dataset_frozen": True,
            "dataset_manifest_path": str(manifest),
            "dataset_manifest_sha256": _sha256(manifest),
            "contract_roll_identity_proven": True,
            "session_day_identity_proven": True,
            "feed_integrity_proven": True,
        },
        "execution_realism": {
            "ioc_no_fill_modeled": True,
            "pessimistic_same_bar": True,
            "gap_through_modeled": True,
            "slippage_included": True,
            "commission_included": True,
            "baseline_adverse_slippage_ticks": 1,
            "commission_round_turn_dollars": 1.48,
            "slippage_stress_ticks": [1, 2, 3],
            "slippage_stress_pass": True,
        },
        "validation": {
            "untouched_validation_window": True,
            "multiple_months_covered": True,
            "walk_forward_pass": True,
            "sample_requirement_pre_registered": True,
            "required_resolved_fills": 30,
            "resolved_fills": 40,
            "drawdown_within_pre_registered_limit": True,
            "concentration_check_pass": True,
            "session_filters_respected": True,
            "direction_coverage_requirement_met": True,
        },
        "golden_parity": {
            "fixture_set_frozen": True,
            "runtime_vs_replay_candidate_parity": True,
            "runtime_vs_replay_risk_parity": True,
            "runtime_vs_replay_order_intent_parity": True,
            "no_fill_case_covered": True,
            "same_bar_ambiguity_case_covered": True,
            "gap_case_covered": True,
            "session_boundary_case_covered": True,
            "roll_boundary_case_covered": True,
        },
        "replay_provenance": {
            "code_sha": "abc123",
            "risk_rules_sha256": _sha256(risk_rules),
        },
    }


def _write_evidence(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _pin_runtime_and_head(monkeypatch) -> None:
    monkeypatch.setattr("ops.project_check.promotion.runtime_snapshot", _runtime_snapshot)
    monkeypatch.setattr("ops.project_check.demo_qualification.gitutil.head_sha", lambda _root: "abc123")


def test_complete_strategy_only_evidence_qualifies_for_demo(tmp_path: Path, monkeypatch) -> None:
    _pin_runtime_and_head(monkeypatch)
    evidence = _write_evidence(tmp_path, _complete_evidence(tmp_path))

    report = build_demo_qualification_report(
        strategy="example", repo_root=tmp_path, evidence_path=evidence
    )

    assert report["gate_pass"] is True
    assert report["demo_evidence_eligible"] is True
    assert report["internal_paper_forward_waived_by_gate"] is True
    assert report["runtime_release_reconciliation_required"] is True
    assert report["live_trading_authorized"] is False


def test_execution_or_risk_change_cannot_skip_internal_paper(tmp_path: Path, monkeypatch) -> None:
    _pin_runtime_and_head(monkeypatch)
    payload = _complete_evidence(tmp_path)
    payload["change_scope"]["execution_path_unchanged"] = False
    evidence = _write_evidence(tmp_path, payload)

    report = build_demo_qualification_report(
        strategy="example", repo_root=tmp_path, evidence_path=evidence
    )

    assert report["gate_pass"] is False
    assert any("execution_path_unchanged" in blocker for blocker in report["blockers"])


def test_session_day_identity_must_be_proven(tmp_path: Path, monkeypatch) -> None:
    _pin_runtime_and_head(monkeypatch)
    payload = _complete_evidence(tmp_path)
    payload["data_integrity"]["session_day_identity_proven"] = False
    evidence = _write_evidence(tmp_path, payload)

    report = build_demo_qualification_report(
        strategy="example", repo_root=tmp_path, evidence_path=evidence
    )

    assert report["gate_pass"] is False
    assert any("session_day_identity_proven" in blocker for blocker in report["blockers"])


def test_sample_floor_cannot_be_registered_below_30(tmp_path: Path, monkeypatch) -> None:
    _pin_runtime_and_head(monkeypatch)
    payload = _complete_evidence(tmp_path)
    payload["validation"]["required_resolved_fills"] = 12
    payload["validation"]["resolved_fills"] = 12
    evidence = _write_evidence(tmp_path, payload)

    report = build_demo_qualification_report(
        strategy="example", repo_root=tmp_path, evidence_path=evidence
    )

    assert report["gate_pass"] is False
    assert any("required_resolved_fills" in blocker for blocker in report["blockers"])


def test_manifest_bytes_are_verified_not_just_claimed(tmp_path: Path, monkeypatch) -> None:
    _pin_runtime_and_head(monkeypatch)
    payload = _complete_evidence(tmp_path)
    payload["data_integrity"]["dataset_manifest_sha256"] = "0" * 64
    evidence = _write_evidence(tmp_path, payload)

    report = build_demo_qualification_report(
        strategy="example", repo_root=tmp_path, evidence_path=evidence
    )

    assert report["gate_pass"] is False
    assert any("dataset_manifest_sha256" in blocker for blocker in report["blockers"])


def test_two_and_three_tick_stress_are_both_required(tmp_path: Path, monkeypatch) -> None:
    _pin_runtime_and_head(monkeypatch)
    payload = _complete_evidence(tmp_path)
    payload["execution_realism"]["slippage_stress_ticks"] = [1, 2]
    evidence = _write_evidence(tmp_path, payload)

    report = build_demo_qualification_report(
        strategy="example", repo_root=tmp_path, evidence_path=evidence
    )

    assert report["gate_pass"] is False
    assert any("3-tick" in blocker for blocker in report["blockers"])
