"""Daily gate must fail closed when proof-critical runtime state is unverifiable.

Reconciliation on 2026-09-17 found ops/project_check/daily.py blocked only on
live_box_drift.status == "error" while execution/live_preflight.py refuses to
arm on the same guard's ok is not True. The box was in exactly that gap:
status "warn" from active unpinned proof-critical overrides and missing
identity pins, preflight failed, daily gate PASSED. These tests pin the repair.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import ops.project_check.runtime as runtime_mod
import scripts.project_check as cli
from ops.project_check.daily import _overall_blockers, build_daily_report


def _blockers(drift: dict) -> list[dict]:
    return _overall_blockers(
        hygiene={"dirty_tracked_files": [], "staged_files": []},
        runtime={"live_box_drift": drift, "risk_rules_load_error": None},
        strategy_drift={"checked": True, "drift_findings": []},
        trade_chain={"status": "PASS"},
    )


# The exact shape live_box_drift_report returned on the box at reconciliation:
# no mismatch, no dirty tree, but proof-critical state unverifiable.
BOX_WARN = {
    "ok": False,
    "status": "warn",
    "summary": (
        "Live box guard cannot fully verify drift; missing expected pin(s): branch, "
        "commit, risk_rules_sha256; active unpinned runtime override(s): SCHEDULE_MODE, EXIT_MODE."
    ),
    "missing_pins": ["branch", "commit", "risk_rules_sha256"],
    "unpinned_runtime_overrides": ["SCHEDULE_MODE", "EXIT_MODE"],
    "mismatches": [],
    "security_runtime": {"status": "ok"},
}


def test_unpinned_proof_critical_override_blocks_daily_gate() -> None:
    drift = dict(BOX_WARN, missing_pins=[])
    blockers = _blockers(drift)
    codes = [b["code"] for b in blockers]
    assert codes == ["RUNTIME_DRIFT_UNVERIFIED"]
    assert "SCHEDULE_MODE" in blockers[0]["detail"]
    assert "EXIT_MODE" in blockers[0]["detail"]


def test_missing_identity_pins_block_daily_gate() -> None:
    drift = dict(BOX_WARN, unpinned_runtime_overrides=[])
    blockers = _blockers(drift)
    assert [b["code"] for b in blockers] == ["RUNTIME_DRIFT_UNVERIFIED"]
    assert "risk_rules_sha256" in blockers[0]["detail"]


def test_drift_error_still_fails_closed_and_is_not_double_reported() -> None:
    drift = dict(
        BOX_WARN,
        status="error",
        summary="Live box drift guard failed: mismatch: commit.",
        mismatches=["commit"],
    )
    codes = [b["code"] for b in _blockers(drift)]
    assert codes == ["RUNTIME_DRIFT_ERROR"]


def test_security_rotation_warning_alone_stays_informational() -> None:
    # Primary webhook secret configured, no distinct rotation alias staged:
    # a verified hardening posture, not unverifiable proof-critical state.
    drift = {
        "ok": False,
        "status": "warn",
        "summary": "Live box guard cannot fully verify drift; webhook secret rotation is not ready.",
        "missing_pins": [],
        "unpinned_runtime_overrides": [],
        "mismatches": [],
        "security_runtime": {"status": "warn"},
    }
    assert _blockers(drift) == []


def test_clean_guard_passes() -> None:
    drift = {
        "ok": True,
        "status": "ok",
        "summary": "Live box guard verified branch main.",
        "missing_pins": [],
        "unpinned_runtime_overrides": [],
        "mismatches": [],
        "security_runtime": {"status": "ok"},
    }
    assert _blockers(drift) == []


def _init_repo(root: Path) -> None:
    import subprocess

    root.mkdir()
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.email", "test@example.com"),
        ("config", "user.name", "Test"),
    ):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    (root / "a.txt").write_text("one\n")
    subprocess.run(["git", "add", "a.txt"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True, capture_output=True)
    (root / "logs").mkdir()


def test_daily_report_and_cli_fail_closed_on_box_warn_condition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.setattr(runtime_mod, "live_box_drift_report", lambda **_kw: dict(BOX_WARN))

    report = build_daily_report(repo_root=repo, journal_dir="logs", use_checkpoint=False, advance_checkpoint=False)
    assert report["trade_chain"]["status"] == "PASS"
    assert report["ok"] is False
    assert report["overall_status"] == "FAIL"
    # The bare tmp repo also lacks risk_rules.yaml / the inventory, which are
    # separate (pre-existing) blockers; the new one must be among them.
    codes = [b["code"] for b in report["overall_blockers"]]
    assert "RUNTIME_DRIFT_UNVERIFIED" in codes
    detail = next(b["detail"] for b in report["overall_blockers"] if b["code"] == "RUNTIME_DRIFT_UNVERIFIED")
    assert "SCHEDULE_MODE" in detail and "risk_rules_sha256" in detail

    monkeypatch.setattr(cli, "ROOT", repo)
    rc = cli.main(["daily", "--journal-dir", "logs", "--no-checkpoint"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "overall: FAIL" in out
    assert "BLOCKER RUNTIME_DRIFT_UNVERIFIED" in out
    assert "SCHEDULE_MODE" in out
