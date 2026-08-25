from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ops.project_check import gitutil
from ops.project_check.session import build_precommit_report, build_session_start_report


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _git_out(root: Path, *args: str) -> str:
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


# ── Ownership/base-freshness checks folded in from ops.project_check.preflight ──
# (that module is no longer a separate top-level routine -- see its docstring
# and ops/project_check/__init__.py -- but its live origin/main and
# worktree-ownership checks are exercised here as part of session-start/precommit.)


@pytest.fixture
def repo_with_origin(tmp_path: Path) -> tuple[Path, Path]:
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
    return _git_out(publisher, "rev-parse", "HEAD")


def test_session_start_reports_current_origin_main_and_clean_ownership(
    repo_with_origin: tuple[Path, Path],
) -> None:
    origin_repo, _remote = repo_with_origin
    report = build_session_start_report(cwd=origin_repo)
    assert report["origin_main_live_verification"]["freshness"] == "CURRENT"
    assert report["origin_main_live_verification"]["head_contains_verified_main"] is True
    assert report["worktree_ownership"]["ok"] is True
    assert report["worktree_ownership"]["detached_head"] is False


def test_session_start_reports_stale_origin_main_without_blocking(
    repo_with_origin: tuple[Path, Path], tmp_path: Path
) -> None:
    origin_repo, remote = repo_with_origin
    old_local = _git_out(origin_repo, "rev-parse", "origin/main")
    new_remote = _advance_remote(tmp_path, remote)

    report = build_session_start_report(cwd=origin_repo)

    # Reported, but session-start never fails closed -- it is a report, not a gate.
    assert report["ok"] is True
    assert report["origin_main_live_verification"]["freshness"] == "STALE"
    assert report["origin_main_live_verification"]["local_sha"] == old_local
    assert report["origin_main_live_verification"]["remote_sha"] == new_remote
    # Never fetches: local origin/main is untouched.
    assert _git_out(origin_repo, "rev-parse", "origin/main") == old_local


def test_session_start_reports_unreachable_remote(
    repo_with_origin: tuple[Path, Path], tmp_path: Path
) -> None:
    origin_repo, _remote = repo_with_origin
    _git(origin_repo, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    report = build_session_start_report(cwd=origin_repo)
    assert report["origin_main_live_verification"]["freshness"] == "UNVERIFIED"


def test_session_start_reports_head_not_containing_verified_main(
    repo_with_origin: tuple[Path, Path], tmp_path: Path
) -> None:
    origin_repo, remote = repo_with_origin
    _git(origin_repo, "switch", "-q", "-c", "research/old-base")
    _advance_remote(tmp_path, remote)
    _git(origin_repo, "fetch", "-q", "origin", "main")

    report = build_session_start_report(cwd=origin_repo)

    assert report["origin_main_live_verification"]["freshness"] == "CURRENT"
    assert report["origin_main_live_verification"]["head_contains_verified_main"] is False


def test_precommit_fails_closed_on_detached_head(repo: Path) -> None:
    build_session_start_report(cwd=repo)
    _git(repo, "checkout", "-q", "--detach")
    report = build_precommit_report(cwd=repo)
    assert report["ok"] is False
    assert report["worktree_ownership"]["detached_head"] is True
    assert any("detached" in r for r in report["reasons"])


def test_precommit_fails_closed_on_duplicate_branch_ownership(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build_session_start_report(cwd=repo)
    stale = tmp_path / "stale-registration"
    head = _git_out(repo, "rev-parse", "HEAD")
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
