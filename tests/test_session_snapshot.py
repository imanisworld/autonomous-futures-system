from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ops import session_snapshot
from ops.live_box_guard import PROOF_CRITICAL_RUNTIME_OVERRIDES


@pytest.fixture(autouse=True)
def _isolate_runtime_env(monkeypatch):
    for name in PROOF_CRITICAL_RUNTIME_OVERRIDES:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"EXPECTED_PROOF_{name}", raising=False)
    monkeypatch.delenv("ENABLE_MANUAL_EXECUTION_CONTROLS", raising=False)
    monkeypatch.setenv("WEBHOOK_SECRET", "test-primary")
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", "test-rotation")


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-q", "-b", "main"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "test@example.com"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "Test User"], cwd=repo)
    (repo / "risk_rules.yaml").write_text("trading_mode:\n  live_trading_enabled: false\n", encoding="utf-8")
    (repo / "logs").mkdir()
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "."], cwd=repo)
    subprocess.check_call(["git", "commit", "-q", "-m", "initial"], cwd=repo)
    return repo


def test_session_start_writes_state_and_precommit_reads_it_ok(tmp_path):
    repo = _init_repo(tmp_path)
    start_report = session_snapshot.build_session_start_report(repo, log_dir=repo / "logs")
    assert start_report["session_state_recorded"] is True
    assert start_report["repo"]["branch"] == "main"

    precommit_report = session_snapshot.build_precommit_report(repo, log_dir=repo / "logs")
    assert precommit_report["ok"] is True
    assert precommit_report["fail_reasons"] == []


def test_precommit_fails_closed_without_session_start(tmp_path):
    repo = _init_repo(tmp_path)
    report = session_snapshot.build_precommit_report(repo, log_dir=repo / "logs")
    assert report["ok"] is False
    assert report["fail_closed"] is True
    assert any("session-start state cannot be verified" in reason for reason in report["fail_reasons"])


def test_precommit_fails_closed_on_branch_change(tmp_path):
    repo = _init_repo(tmp_path)
    session_snapshot.build_session_start_report(repo, log_dir=repo / "logs")

    subprocess.check_call(["git", "checkout", "-q", "-b", "other-branch"], cwd=repo)
    report = session_snapshot.build_precommit_report(repo, log_dir=repo / "logs")
    assert report["ok"] is False
    assert any("branch differs from session-start" in reason for reason in report["fail_reasons"])


def test_precommit_flags_moved_head_without_failing_by_itself(tmp_path):
    repo = _init_repo(tmp_path)
    session_snapshot.build_session_start_report(repo, log_dir=repo / "logs")

    (repo / "extra.txt").write_text("x\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "extra.txt"], cwd=repo)
    subprocess.check_call(["git", "commit", "-q", "-m", "a legit commit"], cwd=repo)

    report = session_snapshot.build_precommit_report(repo, log_dir=repo / "logs")
    # HEAD-moved is surfaced but does not by itself fail closed (branch/worktree unchanged).
    assert any("HEAD moved since session-start" in reason for reason in report["fail_reasons"])
    assert report["ok"] is True


def test_precommit_never_mutates_repo_state(tmp_path):
    repo = _init_repo(tmp_path)
    session_snapshot.build_session_start_report(repo, log_dir=repo / "logs")
    before_status = _git(repo, "status", "--porcelain")
    before_head = _git(repo, "rev-parse", "HEAD")

    session_snapshot.build_precommit_report(repo, log_dir=repo / "logs")

    after_status = _git(repo, "status", "--porcelain")
    after_head = _git(repo, "rev-parse", "HEAD")
    assert before_status == after_status
    assert before_head == after_head


def test_session_state_path_is_worktree_private_not_tracked(tmp_path):
    repo = _init_repo(tmp_path)
    session_snapshot.build_session_start_report(repo, log_dir=repo / "logs")
    state_path = session_snapshot.session_state_path(repo)
    assert state_path is not None
    assert state_path.exists()
    # Never shows up as an untracked/dirty file since it lives inside .git.
    status = _git(repo, "status", "--porcelain")
    assert "afs_session_state" not in status
