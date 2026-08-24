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
    remote = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))

    repo_root = tmp_path / "repo_with_origin"
    repo_root.mkdir()
    _git(repo_root, "init", "-q", "-b", "main")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Test")
    (repo_root / "tracked.txt").write_text("initial\n", encoding="utf-8")
    _git(repo_root, "add", "tracked.txt")
    _git(repo_root, "commit", "-q", "-m", "initial")
    _git(repo_root, "remote", "add", "origin", str(remote))
    _git(repo_root, "push", "-q", "-u", "origin", "main")
    return repo_root, remote


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


# -- Absorbed from the former standalone "ownership preflight" routine: --
# session-start now live-verifies origin/main and worktree/branch ownership
# as part of Routine 1 rather than as a separate fourth routine, and
# precommit fails closed on the ownership defects a commit/push could not
# safely proceed past (detached HEAD, a branch double-registered to two
# worktrees).


def test_session_start_reports_current_verified_origin_main(repo_with_origin: tuple[Path, Path]) -> None:
    repo_root, _remote = repo_with_origin
    report = build_session_start_report(cwd=repo_root)
    assert report["ok"] is True
    verified = report["repo"]["origin_main_verified"]
    assert verified["freshness"] == "CURRENT"
    assert verified["head_contains_verified_main"] is True


def test_session_start_reports_stale_origin_main_without_fetching(
    repo_with_origin: tuple[Path, Path], tmp_path: Path
) -> None:
    repo_root, remote = repo_with_origin
    old_local = _git(repo_root, "rev-parse", "origin/main")
    new_remote = _advance_remote(tmp_path, remote)

    report = build_session_start_report(cwd=repo_root)

    verified = report["repo"]["origin_main_verified"]
    assert verified["freshness"] == "STALE"
    assert verified["local_sha"] == old_local
    assert verified["remote_sha"] == new_remote
    # Purely a report -- this routine never fetches or updates the ref.
    assert _git(repo_root, "rev-parse", "origin/main") == old_local


def test_session_start_reports_unreachable_remote(
    repo_with_origin: tuple[Path, Path], tmp_path: Path
) -> None:
    repo_root, _remote = repo_with_origin
    _git(repo_root, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    report = build_session_start_report(cwd=repo_root)
    assert report["repo"]["origin_main_verified"]["freshness"] == "UNVERIFIED"


def test_session_start_reports_worktree_ownership_ok(repo: Path) -> None:
    report = build_session_start_report(cwd=repo)
    ownership = report["repo"]["worktree_ownership"]
    assert ownership["ok"] is True
    assert ownership["detached_head"] is False


def test_session_start_reports_detached_head(repo: Path) -> None:
    _git(repo, "checkout", "-q", "--detach")
    report = build_session_start_report(cwd=repo)
    ownership = report["repo"]["worktree_ownership"]
    assert ownership["detached_head"] is True
    assert ownership["ok"] is False


def test_precommit_fails_closed_on_detached_head(repo: Path) -> None:
    build_session_start_report(cwd=repo)
    _git(repo, "checkout", "-q", "--detach")
    report = build_precommit_report(cwd=repo)
    assert report["ok"] is False
    assert any("detached" in r for r in report["reasons"])


def test_precommit_fails_closed_on_duplicate_worktree_registration(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build_session_start_report(cwd=repo)
    stale = tmp_path / "stale-registration"
    head = _git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(
        gitutil,
        "worktrees",
        lambda _root: [
            gitutil.Worktree(str(repo), head, "main", False, False, False),
            gitutil.Worktree(str(stale), head, "main", False, False, False),
        ],
    )
    report = build_precommit_report(cwd=repo)
    assert report["ok"] is False
    assert any("multiple worktrees" in r for r in report["reasons"])
