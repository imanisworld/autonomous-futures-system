from __future__ import annotations

import subprocess
from pathlib import Path

from ops.session_check import precommit_report, session_start_report


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_remote_pair(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    subprocess.check_call(["git", "init", "--bare", "-b", "main", str(remote)])
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-b", "main"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "test@example.com"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "Test User"], cwd=repo)
    (repo / "a.txt").write_text("one\n")
    subprocess.check_call(["git", "add", "a.txt"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "init"], cwd=repo)
    subprocess.check_call(["git", "remote", "add", "origin", str(remote)], cwd=repo)
    subprocess.check_call(["git", "push", "-u", "origin", "main"], cwd=repo)
    return repo, remote


def test_session_start_report_reports_repo_state_and_persists_snapshot(tmp_path):
    repo, _remote = _init_remote_pair(tmp_path)
    report = session_start_report(repo, log_dir=repo / "logs")

    assert report["mode"] == "session-start"
    assert report["repo"]["current_branch"] == "main"
    assert report["repo"]["local_main_relationship"] == "IN_SYNC"
    state_path = Path(report["state_persisted_to"])
    assert state_path.exists()
    assert state_path.name == ".ops_session_state.json"


def test_precommit_fails_closed_when_no_session_state_exists(tmp_path):
    repo, _remote = _init_remote_pair(tmp_path)
    report = precommit_report(repo, state_path=tmp_path / "does-not-exist.json")
    assert report["fail_closed"] is True
    assert report["status"] == "BLOCK"
    assert any("not found" in reason for reason in report["reasons"])


def test_precommit_passes_after_session_start_with_no_drift(tmp_path):
    repo, _remote = _init_remote_pair(tmp_path)
    session_start_report(repo, log_dir=repo / "logs")
    report = precommit_report(repo)
    assert report["fail_closed"] is False
    assert report["status"] == "OK"
    assert report["reasons"] == []


def test_precommit_fails_closed_when_branch_changes_after_session_start(tmp_path):
    repo, _remote = _init_remote_pair(tmp_path)
    session_start_report(repo, log_dir=repo / "logs")
    subprocess.check_call(["git", "checkout", "-b", "feature/other"], cwd=repo)

    report = precommit_report(repo)
    assert report["fail_closed"] is True
    assert any("branch differs" in reason for reason in report["reasons"])


def test_precommit_never_mutates_repo_state(tmp_path):
    repo, _remote = _init_remote_pair(tmp_path)
    session_start_report(repo, log_dir=repo / "logs")
    before_head = _git(repo, "rev-parse", "HEAD")
    # The persisted state file lives under logs/, which is untracked here,
    # so `git status --porcelain` may legitimately show it as untracked both
    # before and after -- what must never change is tracked/staged content.
    before_status_tracked = _git(repo, "status", "--porcelain", "--", ":!logs")

    precommit_report(repo)

    after_head = _git(repo, "rev-parse", "HEAD")
    after_status_tracked = _git(repo, "status", "--porcelain", "--", ":!logs")
    assert before_head == after_head
    assert before_status_tracked == after_status_tracked
