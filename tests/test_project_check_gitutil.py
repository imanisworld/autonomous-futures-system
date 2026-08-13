from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ops.project_check import gitutil


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


def test_run_git_rejects_unlisted_subcommand(repo: Path) -> None:
    with pytest.raises(ValueError):
        gitutil.run_git(["commit", "-m", "nope"], cwd=repo)


def test_run_git_rejects_mutating_subcommands_even_if_spelled_out(repo: Path) -> None:
    for banned in ("push", "pull", "reset", "rebase", "checkout", "merge", "cherry-pick", "clean"):
        with pytest.raises(ValueError):
            gitutil.run_git([banned], cwd=repo)


def test_repo_root_and_branch(repo: Path) -> None:
    assert gitutil.repo_root(repo) == repo.resolve()
    assert gitutil.current_branch(repo) == "main"
    assert gitutil.head_sha(repo) is not None


def test_status_porcelain_reports_staged_dirty_untracked(repo: Path) -> None:
    (repo / "a.txt").write_text("changed\n")
    (repo / "untracked.txt").write_text("new\n")
    status = gitutil.status_porcelain(repo)
    assert "a.txt" in status["dirty_tracked"]
    assert "untracked.txt" in status["untracked"]
    assert status["staged"] == []

    _git(repo, "add", "untracked.txt")
    status2 = gitutil.status_porcelain(repo)
    assert "untracked.txt" in status2["staged"]


def test_main_sync_state_unknown_without_remote(repo: Path) -> None:
    state = gitutil.main_sync_state(repo)
    assert state["state"] == "UNKNOWN"
    assert state["local_main_branch"] == "main"
    assert state["remote_ref"] is None


def test_main_sync_state_in_sync_ahead_behind(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "-q", "--bare", "-b", "main")

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(remote), str(clone)], check=True, capture_output=True)
    _git(clone, "config", "user.email", "test@example.com")
    _git(clone, "config", "user.name", "Test")
    (clone / "f.txt").write_text("1\n")
    _git(clone, "add", "f.txt")
    _git(clone, "commit", "-q", "-m", "c1")
    _git(clone, "push", "-q", "-u", "origin", "main")

    state = gitutil.main_sync_state(clone)
    assert state["state"] == "IN_SYNC"

    (clone / "f.txt").write_text("2\n")
    _git(clone, "add", "f.txt")
    _git(clone, "commit", "-q", "-m", "c2 local only")
    state_ahead = gitutil.main_sync_state(clone)
    assert state_ahead["state"] == "AHEAD"
    assert state_ahead["ahead"] == 1
    assert state_ahead["behind"] == 0


def test_worktrees_lists_current_worktree(repo: Path) -> None:
    wts = gitutil.worktrees(repo)
    assert len(wts) == 1
    assert Path(wts[0].path).resolve() == repo.resolve()
    assert wts[0].branch == "main"


def test_stash_list_empty_then_populated(repo: Path) -> None:
    assert gitutil.stash_list(repo) == []
    (repo / "a.txt").write_text("dirty\n")
    _git(repo, "stash", "push", "-m", "wip")
    stashes = gitutil.stash_list(repo)
    assert len(stashes) == 1
    assert "wip" in stashes[0]["message"]


def test_archive_tags_only_matches_archive_prefix(repo: Path) -> None:
    _git(repo, "tag", "-a", "archive/foo-2026-01-01", "-m", "archived")
    _git(repo, "tag", "-a", "v1.0.0", "-m", "release")
    tags = gitutil.archive_tags(repo)
    names = [t["tag"] for t in tags]
    assert "archive/foo-2026-01-01" in names
    assert "v1.0.0" not in names


def test_local_branches_reports_local_only(repo: Path) -> None:
    _git(repo, "branch", "feature/x")
    branches = gitutil.local_branches(repo)
    by_name = {b["branch"]: b for b in branches}
    assert by_name["feature/x"]["local_only"] is True
    assert by_name["main"]["local_only"] is True


def test_gh_available_reflects_path(monkeypatch) -> None:
    monkeypatch.setattr(gitutil.shutil, "which", lambda name: None)
    assert gitutil.gh_available() is False


def test_open_prs_unavailable_without_gh(monkeypatch, repo: Path) -> None:
    monkeypatch.setattr(gitutil.shutil, "which", lambda name: None)
    result = gitutil.open_prs(repo)
    assert result["available"] is False
    assert result["prs"] == []
