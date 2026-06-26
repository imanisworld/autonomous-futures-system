from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from ops.live_box_guard import live_box_drift_report


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-b", "main"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "test@example.com"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "Test User"], cwd=repo)
    (repo / "risk_rules.yaml").write_text("trading_mode:\n  live_trading_enabled: false\n", encoding="utf-8")
    (repo / "logs").mkdir()
    subprocess.check_call(["git", "add", "risk_rules.yaml"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "init"], cwd=repo)
    return repo


def test_live_box_drift_guard_verifies_pinned_repo(monkeypatch, tmp_path):
    repo = _init_repo(tmp_path)
    commit = _git(repo, "rev-parse", "HEAD")
    digest = hashlib.sha256((repo / "risk_rules.yaml").read_bytes()).hexdigest()
    log_dir = repo / "logs"

    monkeypatch.setenv("EXPECTED_LIVE_REPO_ROOT", str(repo))
    monkeypatch.setenv("EXPECTED_LIVE_BRANCH", "main")
    monkeypatch.setenv("EXPECTED_LIVE_COMMIT", commit)
    monkeypatch.setenv("EXPECTED_RISK_RULES_SHA256", digest)
    monkeypatch.setenv("EXPECTED_RUNTIME_JOURNAL_DIR", str(log_dir))
    monkeypatch.setenv("EXPECTED_RUNTIME_EVIDENCE_SOURCE", "active_box_journal_and_status")
    monkeypatch.setenv("RUNTIME_EVIDENCE_SOURCE", "active_box_journal_and_status")

    report = live_box_drift_report(repo_root=repo, log_dir=log_dir)

    assert report["ok"] is True
    assert report["status"] == "ok"
    assert report["mismatches"] == []
    assert report["missing_pins"] == []


def test_live_box_drift_guard_reports_mismatched_commit_and_config(monkeypatch, tmp_path):
    repo = _init_repo(tmp_path)

    monkeypatch.setenv("EXPECTED_LIVE_REPO_ROOT", str(repo))
    monkeypatch.setenv("EXPECTED_LIVE_BRANCH", "main")
    monkeypatch.setenv("EXPECTED_LIVE_COMMIT", "0" * 40)
    monkeypatch.setenv("EXPECTED_RISK_RULES_SHA256", "1" * 64)
    monkeypatch.setenv("EXPECTED_RUNTIME_JOURNAL_DIR", str(repo / "logs"))

    report = live_box_drift_report(repo_root=repo, log_dir=repo / "logs")

    assert report["ok"] is False
    assert report["status"] == "error"
    assert {"commit", "risk_rules_sha256"}.issubset(set(report["mismatches"]))


def test_live_box_drift_guard_warns_when_pins_are_absent(monkeypatch, tmp_path):
    repo = _init_repo(tmp_path)
    for name in (
        "EXPECTED_LIVE_REPO_ROOT",
        "EXPECTED_LIVE_BRANCH",
        "EXPECTED_LIVE_COMMIT",
        "EXPECTED_RISK_RULES_SHA256",
        "EXPECTED_RUNTIME_JOURNAL_DIR",
    ):
        monkeypatch.delenv(name, raising=False)

    report = live_box_drift_report(repo_root=repo, log_dir=repo / "logs")

    assert report["ok"] is False
    assert report["status"] == "warn"
    assert {"branch", "commit", "risk_rules_sha256"}.issubset(set(report["missing_pins"]))
