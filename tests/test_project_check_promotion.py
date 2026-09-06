from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ops.project_check.promotion import build_promotion_report


@pytest.fixture(autouse=True)
def clean_tolerance_env(monkeypatch):
    for name in (
        "ENTRY_SLIPPAGE_TOLERANCE_TICKS",
        "ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES",
        "ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ",
        "ENTRY_FILL_MODEL",
        "MAX_CONTRACTS_HARD_CAP",
    ):
        monkeypatch.delenv(name, raising=False)


def _write_evidence(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_no_evidence_file_requires_operator_classification(tmp_path: Path) -> None:
    report = build_promotion_report(strategy="orb_breakout", repo_root=tmp_path)
    assert report["evidence_supplied"] is False
    assert report["gate_pass"] is False
    assert report["promotion_eligible"] is False
    assert report["classification"]["effective_classification"] == "REQUIRES_OPERATOR_CLASSIFICATION"


def test_missing_evidence_file_reports_load_error(tmp_path: Path) -> None:
    report = build_promotion_report(
        strategy="orb_breakout", repo_root=tmp_path, evidence_path=tmp_path / "missing.json"
    )
    assert report["ok"] is False
    assert report["gate_pass"] is False
    assert "not found" in report["evidence_load_error"]


def test_accounting_identity_holds(tmp_path: Path) -> None:
    evidence = _write_evidence(
        tmp_path,
        {
            "execution": {
                "entry_attempts": 10,
                "fills": 5,
                "cancellations": 4,
                "rejects_or_known_no_fills": 1,
                "resolved_outcomes": 4,
                "legitimately_open": 1,
            },
            "stated_classification": "WAIT",
        },
    )
    report = build_promotion_report(strategy="x", repo_root=tmp_path, evidence_path=evidence)
    acc = report["accounting_identities"]
    assert acc["identity_attempts"]["holds"] is True
    assert acc["identity_fills"]["holds"] is True
    assert acc["all_checkable_identities_hold"] is True


def test_accounting_identity_mismatch_is_a_blocker(tmp_path: Path) -> None:
    evidence = _write_evidence(
        tmp_path,
        {
            "execution": {
                "entry_attempts": 10,
                "fills": 5,
                "cancellations": 4,
                "rejects_or_known_no_fills": 1,
                "resolved_outcomes": 3,  # 3 + 1 = 4 != fills(5)
                "legitimately_open": 1,
            },
            "stated_classification": "VALIDATED",
        },
    )
    report = build_promotion_report(strategy="x", repo_root=tmp_path, evidence_path=evidence)
    assert report["accounting_identities"]["identity_fills"]["holds"] is False
    blockers = report["classification"]["blockers"]
    assert any("accounting identity" in b for b in blockers)
    assert report["classification"]["effective_classification"] == "PROMISING BUT UNPROVEN"
    assert report["classification"]["override_reason"] is not None


def test_zero_fills_blocks_validated(tmp_path: Path) -> None:
    evidence = _write_evidence(
        tmp_path,
        {
            "execution": {
                "entry_attempts": 10,
                "fills": 0,
                "cancellations": 10,
                "rejects_or_known_no_fills": 0,
                "resolved_outcomes": 0,
                "legitimately_open": 0,
            },
            "stated_classification": "VALIDATED",
        },
    )
    report = build_promotion_report(strategy="x", repo_root=tmp_path, evidence_path=evidence)
    assert any("zero executable fills" in b for b in report["classification"]["blockers"])
    assert report["classification"]["effective_classification"] != "VALIDATED"


def test_lookahead_dependency_is_a_blocker(tmp_path: Path) -> None:
    evidence = _write_evidence(
        tmp_path,
        {
            "identity_parity": {"lookahead_or_partial_bar_dependency": True},
            "stated_classification": "VALIDATED",
        },
    )
    report = build_promotion_report(strategy="x", repo_root=tmp_path, evidence_path=evidence)
    assert any("lookahead" in b for b in report["classification"]["blockers"])


def test_execution_context_pinned_and_matching_is_clean(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", "32")
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES", "16")
    monkeypatch.setenv("ENTRY_FILL_MODEL", "ioc_limit")
    evidence = _write_evidence(
        tmp_path,
        {
            "execution_context_claimed": {
                "instrument": "MNQ",
                "entry_fill_model": "ioc_limit",
                "entry_tolerance_ticks": 32,
            },
        },
    )
    report = build_promotion_report(strategy="x", repo_root=tmp_path, evidence_path=evidence)
    ctx = report["execution_context"]
    assert ctx["mismatches"] == []
    assert ctx["parity_ok"] is True


def test_execution_context_unpinned_tolerance_is_flagged_even_without_claim(tmp_path: Path) -> None:
    evidence = _write_evidence(tmp_path, {})
    report = build_promotion_report(strategy="x", repo_root=tmp_path, evidence_path=evidence)
    ctx = report["execution_context"]
    assert ctx["parity_ok"] is False
    assert any("unpinned" in m for m in ctx["mismatches"])


def test_execution_context_claimed_fill_model_mismatch_flagged(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ENTRY_FILL_MODEL", "market")
    evidence = _write_evidence(
        tmp_path, {"execution_context_claimed": {"entry_fill_model": "ioc_limit"}}
    )
    report = build_promotion_report(strategy="x", repo_root=tmp_path, evidence_path=evidence)
    assert any("entry_fill_model" in m for m in report["execution_context"]["mismatches"])


def test_invalid_stated_classification_is_a_warning(tmp_path: Path) -> None:
    evidence = _write_evidence(tmp_path, {"stated_classification": "TOTALLY_FINE"})
    report = build_promotion_report(strategy="x", repo_root=tmp_path, evidence_path=evidence)
    assert any("not one of" in w for w in report["classification"]["warnings"])


def test_gate_fails_when_blockers_exist_even_if_classification_is_promising(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", "32")
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES", "16")
    evidence = _write_evidence(
        tmp_path,
        {
            "execution": {
                "entry_attempts": 1,
                "fills": 0,
                "cancellations": 1,
                "rejects_or_known_no_fills": 0,
                "resolved_outcomes": 0,
                "legitimately_open": 0,
            },
            "stated_classification": "PROMISING BUT UNPROVEN",
        },
    )
    report = build_promotion_report(strategy="x", repo_root=tmp_path, evidence_path=evidence)
    assert report["ok"] is True
    assert report["gate_pass"] is False
    assert report["classification"]["effective_classification"] == "PROMISING BUT UNPROVEN"


def test_claimed_tolerance_requires_instrument(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", "32")
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES", "16")
    evidence = _write_evidence(
        tmp_path,
        {"execution_context_claimed": {"entry_tolerance_ticks": 32}},
    )
    report = build_promotion_report(strategy="x", repo_root=tmp_path, evidence_path=evidence)
    assert report["gate_pass"] is False
    assert any("instrument is required" in m for m in report["execution_context"]["mismatches"])


def test_tolerance_is_checked_only_against_claimed_instrument(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", "32")
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES", "16")
    evidence = _write_evidence(
        tmp_path,
        {
            "execution_context_claimed": {
                "instrument": "MES",
                "entry_tolerance_ticks": 32,
            }
        },
    )
    report = build_promotion_report(strategy="x", repo_root=tmp_path, evidence_path=evidence)
    assert report["gate_pass"] is False
    assert any("for MES" in m and "32" in m and "16" in m for m in report["execution_context"]["mismatches"])


def test_contract_qty_is_checked_against_relevant_effective_cap(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", "32")
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES", "16")
    monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", "1")
    (tmp_path / "risk_rules.yaml").write_text(
        "position_rules:\n"
        "  max_contracts_per_instrument:\n"
        "    MNQ: 6\n",
        encoding="utf-8",
    )
    evidence = _write_evidence(
        tmp_path,
        {
            "execution_context_claimed": {
                "instrument": "MNQ",
                "contract_qty": 2,
            }
        },
    )
    report = build_promotion_report(strategy="x", repo_root=tmp_path, evidence_path=evidence)
    assert report["gate_pass"] is False
    assert any("contract_qty=2" in m and "cap 1" in m for m in report["execution_context"]["mismatches"])


def test_cli_returns_nonzero_when_promotion_gate_is_blocked(tmp_path: Path) -> None:
    evidence = _write_evidence(
        tmp_path,
        {
            "execution": {
                "entry_attempts": 1,
                "fills": 0,
                "cancellations": 1,
                "rejects_or_known_no_fills": 0,
                "resolved_outcomes": 0,
                "legitimately_open": 0,
            },
            "stated_classification": "PROMISING BUT UNPROVEN",
        },
    )
    repo_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "project_check.py"),
            "promotion",
            "--strategy",
            "x",
            "--evidence-file",
            str(evidence),
            "--json",
        ],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
