from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ops.project_check import gitutil
from ops.project_check.session import build_precommit_report, build_session_start_report


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
    return result.stdout.strip()


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


@pytest.fixture
def repo_with_origin(tmp_path: Path) -> tuple[Path, Path]:
    """A repo with a real `origin` remote, for the live origin/main checks
    formerly covered by the standalone ownership-preflight routine (folded
    into session-start/precommit -- see session.py's module docstring)."""
    remote = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))

    origin_repo = tmp_path / "repo"
    origin_repo.mkdir()
    _git(origin_repo, "init", "-q", "-b", "main")
    _git(origin_repo, "config", "user.email", "test@example.com")
    _git(origin_repo, "config", "user.name", "Test")
    (origin_repo / "tracked.txt").write_text("initial\n", encoding="utf-8")
    _git(origin_repo, "add", "tracked.txt")
    _git(origin_repo, "commit", "-q", "-m", "initial")
    _git(origin_repo, "remote", "add", "origin", str(remote))
    _git(origin_repo, "push", "-q", "-u", "origin", "main")
    return origin_repo, remote


def _advance_remote(tmp_path: Path, remote: Path) -> str:
    publisher = tmp_path / "publisher"
    _git(tmp_path, "clone", "-q", str(remote), str(publisher))
    _git(publisher, "config", "user.email", "publisher@example.com")
    _git(publisher, "config", "user.name", "Publisher")
    (publisher / "remote.txt").write_text("new remote work\n", encoding="utf-8")
    _git(publisher, "add", "remote.txt")
    _git(publisher, "commit", "-q", "-m", "advance main")
    _git(publisher, "push", "-q", "origin", "main")
    return _git(publisher, "rev-parse", "HEAD")


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


# ── Folded-in from the former standalone ownership-preflight routine ────────
# (live origin/main freshness + worktree ownership -- see session.py's module
# docstring for why these now live in session-start/precommit instead of a
# fourth routine).


def test_session_start_reports_live_origin_main_current(repo_with_origin: tuple[Path, Path]) -> None:
    repo, _remote = repo_with_origin
    report = build_session_start_report(cwd=repo)
    lom = report["repo"]["live_origin_main_verification"]
    assert lom["freshness"] == "CURRENT"
    assert lom["head_contains_verified_main"] is True


def test_session_start_flags_stale_local_origin_main_without_fetching(
    repo_with_origin: tuple[Path, Path], tmp_path: Path
) -> None:
    repo, remote = repo_with_origin
    old_local = _git(repo, "rev-parse", "origin/main")
    new_remote = _advance_remote(tmp_path, remote)

    report = build_session_start_report(cwd=repo)

    lom = report["repo"]["live_origin_main_verification"]
    assert lom["freshness"] == "STALE"
    assert lom["local_sha"] == old_local
    assert lom["remote_sha"] == new_remote
    # Read-only: the local origin/main ref must not have been updated by this check.
    assert _git(repo, "rev-parse", "origin/main") == old_local
    # session-start never fails closed -- it's a report, not a gate.
    assert report["ok"] is True


def test_precommit_fails_closed_on_detached_head(repo: Path) -> None:
    build_session_start_report(cwd=repo)
    _git(repo, "checkout", "-q", "--detach")
    report = build_precommit_report(cwd=repo)
    assert report["ok"] is False
    assert any("detached HEAD" in r for r in report["reasons"])


def test_precommit_fails_closed_on_duplicate_branch_worktree_ownership(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build_session_start_report(cwd=repo)
    stale = tmp_path / "stale-registration"
    monkeypatch.setattr(
        gitutil,
        "worktrees",
        lambda _root: [
            gitutil.Worktree(str(repo), _git(repo, "rev-parse", "HEAD"), "main", False, False, False),
            gitutil.Worktree(str(stale), _git(repo, "rev-parse", "HEAD"), "main", False, False, False),
        ],
    )
    report = build_precommit_report(cwd=repo)
    assert report["ok"] is False
    assert any("registered to multiple worktrees" in r for r in report["reasons"])
