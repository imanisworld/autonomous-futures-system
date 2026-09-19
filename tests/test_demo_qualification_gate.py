from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ops.project_check.demo_qualification import (
    _verify_session_day_identity,
    build_demo_qualification_report,
)


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
    replay_file = tmp_path / "bars_MNQ_2026-01-02.jsonl"
    replay_file.write_text('{"timestamp":"2026-01-02T00:00:00+00:00"}\n', encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "instrument": "MNQ",
                "timeframe_minutes": 15,
                "coverage": {"files": 1, "rows": 1},
                "gap_ledger_cme_hours": [],
                "files": {
                    replay_file.name: {
                        "sha256": _sha256(replay_file),
                        "rows": 1,
                        "first": "2026-01-02T00:00:00+00:00",
                        "last": "2026-01-02T00:00:00+00:00",
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manifest_sha = _sha256(manifest)
    journal = tmp_path / "journal_2026-01-02.jsonl"
    journal_rows = []
    for i in range(40):
        order_id = f"PAPER-{i:03d}"
        journal_rows.append(
            {
                "ts": "2026-01-02T00:00:00+00:00",
                "bar_ts": "2026-01-02T00:00:00+00:00",
                "decision": "TRADE",
                "instrument": "MNQ",
                "paper_order_id": order_id,
                "setup": {"strategy": "example"},
            }
        )
        journal_rows.append(
            {
                "ts": "2026-01-02T12:00:00+00:00",
                "type": "OUTCOME",
                "instrument": "MNQ",
                "outcome": {
                    "result": "WIN",
                    "paper_order_id": order_id,
                    "signal_timestamp": "2026-01-02T00:00:00+00:00",
                    "execution_audit": {
                        "historical_signal_bar_ts": "2026-01-02T00:00:00+00:00",
                        "historical_entry_bar_ts": "2026-01-02T00:00:00+00:00",
                        "historical_resolution_bar_ts": "2026-01-02T00:15:00+00:00",
                    },
                },
            }
        )
    journal.write_text(
        "\n".join(json.dumps(row) for row in journal_rows) + "\n",
        encoding="utf-8",
    )

    dependency_windows = tmp_path / "dependency_windows.json"
    dependency_windows.write_text(
        json.dumps(
            {
                "schema_version": "strategy_dependency_windows_v1",
                "code_sha": "abc123",
                "strategy": "example",
                "instrument": "MNQ",
                "windows": {
                    f"PAPER-{i:03d}": "2026-01-02T00:00:00+00:00"
                    for i in range(40)
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    gap_proof = tmp_path / "gap_proof.json"
    gap_proof.write_text(
        json.dumps(
            {
                "schema_version": "replay_gap_proof_v1",
                "code_sha": "abc123",
                "dataset_manifest_sha256": manifest_sha,
                "instrument": "MNQ",
                "dependency_windows_source": {
                    "path": str(dependency_windows),
                    "sha256": _sha256(dependency_windows),
                },
                "source_journals": [
                    {"path": str(journal), "sha256": _sha256(journal)}
                ],
                "resolved_outcomes": [
                    {
                        "paper_order_id": f"PAPER-{i:03d}",
                        "dependency_start_timestamp": "2026-01-02T00:00:00+00:00",
                        "signal_timestamp": "2026-01-02T00:00:00+00:00",
                        "entry_timestamp": "2026-01-02T00:00:00+00:00",
                        "exit_timestamp": "2026-01-02T00:15:00+00:00",
                    }
                    for i in range(40)
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
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
            "base_sha": "base123",
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
            "dataset_manifest_sha256": manifest_sha,
            "gap_proof_path": str(gap_proof),
            "gap_proof_sha256": _sha256(gap_proof),
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
            "required_resolved_fills_per_cell": 30,
            "minimum_resolved_fills_in_required_cells": 40,
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


def _pin_runtime_head_and_diff(monkeypatch, changed_files: str = "strategy/example.py\ntests/test_example.py\n") -> None:
    monkeypatch.setattr("ops.project_check.promotion.runtime_snapshot", _runtime_snapshot)
    monkeypatch.setattr("ops.project_check.demo_qualification.gitutil.head_sha", lambda _root: "abc123")
    monkeypatch.setattr(
        "ops.project_check.demo_qualification.gitutil.run_git",
        lambda _args, cwd: (changed_files, None),
    )
    monkeypatch.setattr(
        "ops.project_check.demo_qualification._verify_session_day_identity",
        lambda _root, instrument: {
            "ok": instrument == "MNQ",
            "instrument": instrument,
            "rows_checked": 1,
            "mismatches": [],
            "reason": None if instrument == "MNQ" else "unsupported test instrument",
        },
    )


def test_complete_strategy_only_evidence_qualifies_for_demo(tmp_path: Path, monkeypatch) -> None:
    _pin_runtime_head_and_diff(monkeypatch)
    evidence = _write_evidence(tmp_path, _complete_evidence(tmp_path))

    report = build_demo_qualification_report(
        strategy="example", repo_root=tmp_path, evidence_path=evidence
    )

    assert report["gate_pass"] is True
    assert report["demo_evidence_eligible"] is True
    assert report["long_internal_paper_phase_waived"] is True
    assert report["runtime_release_reconciliation_required"] is True
    assert report["demo_forward_validation_required"] is True
    assert report["live_trading_authorized"] is False


def test_execution_or_risk_change_cannot_skip_internal_paper(tmp_path: Path, monkeypatch) -> None:
    _pin_runtime_head_and_diff(monkeypatch)
    payload = _complete_evidence(tmp_path)
    payload["change_scope"]["execution_path_unchanged"] = False
    evidence = _write_evidence(tmp_path, payload)

    report = build_demo_qualification_report(
        strategy="example", repo_root=tmp_path, evidence_path=evidence
    )

    assert report["gate_pass"] is False
    assert any("execution_path_unchanged" in blocker for blocker in report["blockers"])


def test_git_diff_must_mechanically_be_strategy_only(tmp_path: Path, monkeypatch) -> None:
    _pin_runtime_head_and_diff(
        monkeypatch,
        changed_files="strategy/example.py\nexecution/paper_broker.py\n",
    )
    evidence = _write_evidence(tmp_path, _complete_evidence(tmp_path))

    report = build_demo_qualification_report(
        strategy="example", repo_root=tmp_path, evidence_path=evidence
    )

    assert report["gate_pass"] is False
    assert any("execution/paper_broker.py" in blocker for blocker in report["blockers"])


def test_self_baselined_or_empty_diff_cannot_qualify(tmp_path: Path, monkeypatch) -> None:
    _pin_runtime_head_and_diff(monkeypatch, changed_files="")
    payload = _complete_evidence(tmp_path)
    payload["change_scope"]["base_sha"] = "abc123"
    evidence = _write_evidence(tmp_path, payload)

    report = build_demo_qualification_report(
        strategy="example", repo_root=tmp_path, evidence_path=evidence
    )

    assert report["gate_pass"] is False
    assert any("pre-change commit" in blocker for blocker in report["blockers"])
    assert any("diff is empty" in blocker for blocker in report["blockers"])


def test_session_day_identity_must_be_proven(tmp_path: Path, monkeypatch) -> None:
    _pin_runtime_head_and_diff(monkeypatch)
    payload = _complete_evidence(tmp_path)
    payload["data_integrity"]["session_day_identity_proven"] = False
    evidence = _write_evidence(tmp_path, payload)

    report = build_demo_qualification_report(
        strategy="example", repo_root=tmp_path, evidence_path=evidence
    )

    assert report["gate_pass"] is False
    assert any("session_day_identity_proven" in blocker for blocker in report["blockers"])


def test_session_day_identity_true_claim_still_requires_mechanical_fixture_proof(
    tmp_path: Path, monkeypatch
) -> None:
    _pin_runtime_head_and_diff(monkeypatch)
    monkeypatch.setattr(
        "ops.project_check.demo_qualification._verify_session_day_identity",
        lambda _root, _instrument: {"ok": False, "reason": "fixture hash mismatch"},
    )
    evidence = _write_evidence(tmp_path, _complete_evidence(tmp_path))

    report = build_demo_qualification_report(
        strategy="example", repo_root=tmp_path, evidence_path=evidence
    )

    assert report["gate_pass"] is False
    assert any("mechanical C14 fixture proof" in blocker for blocker in report["blockers"])


def test_current_c14_fixture_package_mechanically_proves_mes_and_mnq() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    for instrument in ("MES", "MNQ"):
        proof = _verify_session_day_identity(repo_root, instrument)
        assert proof["ok"] is True, proof
        assert proof["rows_checked"] > 0
        assert proof["mismatches"] == []


def test_feed_integrity_true_claim_still_requires_replay_file_hashes(
    tmp_path: Path, monkeypatch
) -> None:
    _pin_runtime_head_and_diff(monkeypatch)
    payload = _complete_evidence(tmp_path)
    (tmp_path / "bars_MNQ_2026-01-02.jsonl").write_text(
        '{"timestamp":"tampered"}\n', encoding="utf-8"
    )
    evidence = _write_evidence(tmp_path, payload)

    report = build_demo_qualification_report(
        strategy="example", repo_root=tmp_path, evidence_path=evidence
    )

    assert report["gate_pass"] is False
    feed = report["data_integrity"]["mechanical_feed_integrity"]
    assert feed["ok"] is False
    assert feed["file_hash_mismatches"]
    assert any("frozen manifest/file proof" in blocker for blocker in report["blockers"])


def test_feed_integrity_requires_explicit_gap_ledger(tmp_path: Path, monkeypatch) -> None:
    _pin_runtime_head_and_diff(monkeypatch)
    payload = _complete_evidence(tmp_path)
    manifest = Path(payload["data_integrity"]["dataset_manifest_path"])
    body = json.loads(manifest.read_text(encoding="utf-8"))
    body.pop("gap_ledger_cme_hours")
    manifest.write_text(json.dumps(body) + "\n", encoding="utf-8")
    payload["data_integrity"]["dataset_manifest_sha256"] = _sha256(manifest)
    evidence = _write_evidence(tmp_path, payload)

    report = build_demo_qualification_report(
        strategy="example", repo_root=tmp_path, evidence_path=evidence
    )

    assert report["gate_pass"] is False
    feed = report["data_integrity"]["mechanical_feed_integrity"]
    assert feed["ok"] is False
    assert any("gap_ledger_cme_hours" in problem for problem in feed["problems"])


def test_sample_floor_cannot_be_registered_below_30_per_required_cell(tmp_path: Path, monkeypatch) -> None:
    _pin_runtime_head_and_diff(monkeypatch)
    payload = _complete_evidence(tmp_path)
    payload["validation"]["required_resolved_fills_per_cell"] = 12
    payload["validation"]["minimum_resolved_fills_in_required_cells"] = 12
    evidence = _write_evidence(tmp_path, payload)

    report = build_demo_qualification_report(
        strategy="example", repo_root=tmp_path, evidence_path=evidence
    )

    assert report["gate_pass"] is False
    assert any("required_resolved_fills_per_cell" in blocker for blocker in report["blockers"])


def test_manifest_bytes_are_verified_not_just_claimed(tmp_path: Path, monkeypatch) -> None:
    _pin_runtime_head_and_diff(monkeypatch)
    payload = _complete_evidence(tmp_path)
    payload["data_integrity"]["dataset_manifest_sha256"] = "0" * 64
    evidence = _write_evidence(tmp_path, payload)

    report = build_demo_qualification_report(
        strategy="example", repo_root=tmp_path, evidence_path=evidence
    )

    assert report["gate_pass"] is False
    assert any("dataset_manifest_sha256" in blocker for blocker in report["blockers"])


def test_gap_proof_blocks_counted_outcome_overlapping_declared_gap(
    tmp_path: Path, monkeypatch
) -> None:
    _pin_runtime_head_and_diff(monkeypatch)
    payload = _complete_evidence(tmp_path)
    manifest = Path(payload["data_integrity"]["dataset_manifest_path"])
    body = json.loads(manifest.read_text(encoding="utf-8"))
    body["gap_ledger_cme_hours"] = [
        {
            "first_missing": "2026-01-02T00:10:00+00:00",
            "last_missing": "2026-01-02T00:10:00+00:00",
            "slots": 1,
            "minutes": 15,
        }
    ]
    manifest.write_text(json.dumps(body) + "\n", encoding="utf-8")
    manifest_sha = _sha256(manifest)
    payload["data_integrity"]["dataset_manifest_sha256"] = manifest_sha

    gap_proof = Path(payload["data_integrity"]["gap_proof_path"])
    proof = json.loads(gap_proof.read_text(encoding="utf-8"))
    proof["dataset_manifest_sha256"] = manifest_sha
    gap_proof.write_text(json.dumps(proof) + "\n", encoding="utf-8")
    payload["data_integrity"]["gap_proof_sha256"] = _sha256(gap_proof)
    evidence = _write_evidence(tmp_path, payload)

    report = build_demo_qualification_report(
        strategy="example", repo_root=tmp_path, evidence_path=evidence
    )

    assert report["gate_pass"] is False
    gap = report["data_integrity"]["resolved_outcome_gap_proof"]
    assert gap["ok"] is False
    assert len(gap["contaminated_outcomes"]) == 20
    assert any("overlap declared dataset gaps" in problem for problem in gap["problems"])


def test_gap_proof_requires_full_ordered_timestamps(tmp_path: Path, monkeypatch) -> None:
    _pin_runtime_head_and_diff(monkeypatch)
    payload = _complete_evidence(tmp_path)
    gap_proof = Path(payload["data_integrity"]["gap_proof_path"])
    proof = json.loads(gap_proof.read_text(encoding="utf-8"))
    proof["resolved_outcomes"][0]["entry_timestamp"] = None
    gap_proof.write_text(json.dumps(proof) + "\n", encoding="utf-8")
    payload["data_integrity"]["gap_proof_sha256"] = _sha256(gap_proof)
    evidence = _write_evidence(tmp_path, payload)

    report = build_demo_qualification_report(
        strategy="example", repo_root=tmp_path, evidence_path=evidence
    )

    assert report["gate_pass"] is False
    gap = report["data_integrity"]["resolved_outcome_gap_proof"]
    assert gap["invalid_rows"]
    assert "four timezone-aware timestamps" in gap["invalid_rows"][0]["reason"]


def test_gap_proof_count_must_match_resolved_outcomes(tmp_path: Path, monkeypatch) -> None:
    _pin_runtime_head_and_diff(monkeypatch)
    payload = _complete_evidence(tmp_path)
    gap_proof = Path(payload["data_integrity"]["gap_proof_path"])
    proof = json.loads(gap_proof.read_text(encoding="utf-8"))
    proof["resolved_outcomes"].pop()
    gap_proof.write_text(json.dumps(proof) + "\n", encoding="utf-8")
    payload["data_integrity"]["gap_proof_sha256"] = _sha256(gap_proof)
    evidence = _write_evidence(tmp_path, payload)

    report = build_demo_qualification_report(
        strategy="example", repo_root=tmp_path, evidence_path=evidence
    )

    assert report["gate_pass"] is False
    gap = report["data_integrity"]["resolved_outcome_gap_proof"]
    assert any("execution.resolved_outcomes=40" in problem for problem in gap["problems"])


def test_gap_proof_bytes_are_hash_verified(tmp_path: Path, monkeypatch) -> None:
    _pin_runtime_head_and_diff(monkeypatch)
    payload = _complete_evidence(tmp_path)
    payload["data_integrity"]["gap_proof_sha256"] = "0" * 64
    evidence = _write_evidence(tmp_path, payload)

    report = build_demo_qualification_report(
        strategy="example", repo_root=tmp_path, evidence_path=evidence
    )

    assert report["gate_pass"] is False
    assert "gap_proof_sha256" in report["data_integrity"]["resolved_outcome_gap_proof"]["reason"]


def test_two_and_three_tick_stress_are_both_required(tmp_path: Path, monkeypatch) -> None:
    _pin_runtime_head_and_diff(monkeypatch)
    payload = _complete_evidence(tmp_path)
    payload["execution_realism"]["slippage_stress_ticks"] = [1, 2]
    evidence = _write_evidence(tmp_path, payload)

    report = build_demo_qualification_report(
        strategy="example", repo_root=tmp_path, evidence_path=evidence
    )

    assert report["gate_pass"] is False
    assert any("3-tick" in blocker for blocker in report["blockers"])
