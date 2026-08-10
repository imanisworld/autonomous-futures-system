"""Tests for ops/repo_state.py -- read-only git/worktree introspection."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ops import repo_state


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("hello\n")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "init")
    return root


def test_current_branch(git_repo: Path) -> None:
    assert repo_state.current_branch(git_repo) == "main"


def test_head_sha_matches_rev_parse(git_repo: Path) -> None:
    sha = repo_state.head_sha(git_repo)
    expected = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(git_repo), capture_output=True, text=True
    ).stdout.strip()
    assert sha == expected


def test_dirty_files_splits_staged_unstaged_untracked(git_repo: Path) -> None:
    (git_repo / "untracked.txt").write_text("x\n")
    (git_repo / "README.md").write_text("changed\n")
    _git(git_repo, "add", "README.md")
    (git_repo / "README.md").write_text("changed again\n")
    files = repo_state.dirty_files(git_repo)
    assert "untracked.txt" in files["untracked"]
    assert "README.md" in files["staged"]
    assert "README.md" in files["unstaged_tracked"]


def test_dirty_files_clean_repo_is_empty(git_repo: Path) -> None:
    files = repo_state.dirty_files(git_repo)
    assert files == {"staged": [], "unstaged_tracked": [], "untracked": []}


def test_sync_relationship_in_sync_ahead_behind_diverged(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "-q", "--bare", "-b", "main")

    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test")
    (work / "a.txt").write_text("1\n")
    _git(work, "add", "a.txt")
    _git(work, "commit", "-q", "-m", "one")
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "push", "-q", "origin", "main")

    assert repo_state.sync_relationship(work, "main", "origin/main") == "IN_SYNC"

    (work / "b.txt").write_text("2\n")
    _git(work, "add", "b.txt")
    _git(work, "commit", "-q", "-m", "two")
    assert repo_state.sync_relationship(work, "main", "origin/main") == "AHEAD"

    _git(work, "push", "-q", "origin", "main")
    _git(work, "reset", "-q", "--hard", "HEAD~1")
    assert repo_state.sync_relationship(work, "main", "origin/main") == "BEHIND"

    (work / "c.txt").write_text("3\n")
    _git(work, "add", "c.txt")
    _git(work, "commit", "-q", "-m", "diverge")
    assert repo_state.sync_relationship(work, "main", "origin/main") == "DIVERGED"


def test_sync_relationship_unknown_for_missing_ref(git_repo: Path) -> None:
    assert repo_state.sync_relationship(git_repo, "main", "origin/main") == "UNKNOWN"


def test_stash_list(git_repo: Path) -> None:
    assert repo_state.stash_list(git_repo) == []
    (git_repo / "README.md").write_text("wip\n")
    _git(git_repo, "stash", "push", "-q", "-m", "wip work")
    stashes = repo_state.stash_list(git_repo)
    assert len(stashes) == 1
    assert "wip work" in stashes[0]


def test_archive_tags_filters_by_prefix(git_repo: Path) -> None:
    _git(git_repo, "tag", "archive/foo-2026-01-01")
    _git(git_repo, "tag", "not-an-archive-tag")
    tags = repo_state.archive_tags(git_repo)
    assert tags == ["archive/foo-2026-01-01"]


def test_branches_missing_archive_tag_flags_unmerged_without_tag(git_repo: Path) -> None:
    _git(git_repo, "checkout", "-q", "-b", "codex/some-feature")
    (git_repo / "feature.txt").write_text("x\n")
    _git(git_repo, "add", "feature.txt")
    _git(git_repo, "commit", "-q", "-m", "feature work")
    _git(git_repo, "checkout", "-q", "main")

    findings = repo_state.branches_missing_archive_tag(git_repo, base="main")
    by_branch = {f["branch"]: f for f in findings}
    assert "codex/some-feature" in by_branch
    assert by_branch["codex/some-feature"]["has_archive_tag"] is False

    _git(git_repo, "tag", "archive/codex-some-feature-2026-01-01", "codex/some-feature")
    findings = repo_state.branches_missing_archive_tag(git_repo, base="main")
    by_branch = {f["branch"]: f for f in findings}
    assert by_branch["codex/some-feature"]["has_archive_tag"] is True


def test_local_only_branches(git_repo: Path) -> None:
    _git(git_repo, "checkout", "-q", "-b", "local-only-branch")
    _git(git_repo, "checkout", "-q", "main")
    assert "local-only-branch" in repo_state.local_only_branches(git_repo)


def test_run_git_returns_none_on_failure(git_repo: Path) -> None:
    assert repo_state.run_git(git_repo, "not-a-real-git-command") is None


def test_list_worktrees_includes_current(git_repo: Path) -> None:
    worktrees = repo_state.list_worktrees(git_repo)
    assert len(worktrees) == 1
    assert worktrees[0]["branch"] == "main"
