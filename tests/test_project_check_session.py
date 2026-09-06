from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ops.project_check.session import build_precommit_report, build_session_start_report


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "a.txt").write_text("one\n")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-q", "-m", "initial")
    return root


def test_session_start_reports_ok_and_writes_state(repo: Path) -> None:
    report = build_session_start_report(cwd=repo)
    assert report["ok"] is True
    assert report["repo"]["current_branch"] == "main"
    assert report["branch_changed_during_check"] is False
    state_file = repo / ".git" / "afs-project-check" / "session_state.json"
    assert state_file.exists()


def test_session_start_outside_git_repo(tmp_path: Path) -> None:
    outside = tmp_path / "not_a_repo"
    outside.mkdir()
    report = build_session_start_report(cwd=outside)
    assert report["ok"] is False
    assert "not inside a git repository" in report["error"]


def test_precommit_fails_closed_without_session_start(repo: Path) -> None:
    report = build_precommit_report(cwd=repo)
    assert report["ok"] is False
    assert report["verdict"] == "FAIL_CLOSED"
    assert any("session-start state cannot be verified" in r for r in report["reasons"])


def test_precommit_ok_immediately_after_session_start(repo: Path) -> None:
    build_session_start_report(cwd=repo)
    report = build_precommit_report(cwd=repo)
    assert report["ok"] is True
    assert report["verdict"] == "OK"
    assert report["reasons"] == []


def test_precommit_fails_closed_on_branch_change(repo: Path) -> None:
    build_session_start_report(cwd=repo)
    _git(repo, "checkout", "-q", "-b", "feature/other")
    report = build_precommit_report(cwd=repo)
    assert report["ok"] is False
    assert any("branch differs from session-start branch unexpectedly" in r for r in report["reasons"])


def test_precommit_fails_closed_on_head_moving_on_same_branch(repo: Path) -> None:
    build_session_start_report(cwd=repo)
    (repo / "a.txt").write_text("two\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "external commit")
    report = build_precommit_report(cwd=repo)
    assert report["ok"] is False
    assert any("branch moved unexpectedly" in r for r in report["reasons"])


def test_precommit_never_mutates_repo(repo: Path) -> None:
    build_session_start_report(cwd=repo)
    (repo / "a.txt").write_text("dirty\n")
    (repo / "new.txt").write_text("new\n")
    before_status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    build_precommit_report(cwd=repo)
    after_status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert before_status == after_status


def test_precommit_reports_changed_staged_untracked_lists(repo: Path) -> None:
    build_session_start_report(cwd=repo)
    (repo / "a.txt").write_text("dirty\n")
    (repo / "new.txt").write_text("new\n")
    _git(repo, "add", "new.txt")
    report = build_precommit_report(cwd=repo)
    assert "a.txt" in report["repo"]["changed_files"]
    assert "new.txt" in report["repo"]["staged_files"]


def test_linked_worktree_session_and_checkpoint_are_isolated(repo: Path, tmp_path: Path) -> None:
    from ops.project_check import gitutil
    from ops.project_check.daily import _repo_hygiene
    from ops.project_check.trade_chain import load_checkpoint, save_checkpoint

    linked = tmp_path / "linked"
    _git(repo, "worktree", "add", "-b", "other", str(linked))
    (repo / "a.txt").write_text("original dirty worktree\n")
    (repo / "untracked.txt").write_text("preserve this\n")
    report = build_session_start_report(cwd=linked)
    by_path = {w["path"]: w for w in report["repo"]["all_worktrees"]}
    assert by_path[str(repo)]["dirty_status"]["dirty_tracked"] == ["a.txt"]
    assert by_path[str(repo)]["dirty_status"]["untracked"] == ["untracked.txt"]
    assert by_path[str(linked)]["dirty_status"]["dirty"] is False
    assert "outside" in report["repo"]["stash_preservation_note"]
    assert not (repo / ".git/afs-project-check/session_state.json").exists()
    assert (gitutil.git_dir(linked) / "afs-project-check/session_state.json").exists()
    assert build_precommit_report(cwd=linked)["ok"] is True
    daily_trees = {w["path"]: w for w in _repo_hygiene(linked)["worktrees"]}
    assert daily_trees[str(repo)]["dirty_status"] == by_path[str(repo)]["dirty_status"]
    save_checkpoint(repo, "2026-01-01T00:00:00Z")
    save_checkpoint(linked, "2026-02-01T00:00:00Z")
    assert load_checkpoint(repo) == "2026-01-01T00:00:00Z"
    assert load_checkpoint(linked) == "2026-02-01T00:00:00Z"
    assert (repo / "a.txt").read_text() == "original dirty worktree\n"
