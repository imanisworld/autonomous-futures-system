"""Tests for ops.repo_state — read-only git/worktree/branch/stash introspection."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ops import repo_state as rs


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(root), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    return result.stdout.strip()


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")


def _commit(root: Path, filename: str, content: str, message: str) -> str:
    (root / filename).write_text(content, encoding="utf-8")
    _git(root, "add", filename)
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    _init_repo(root)
    _commit(root, "README.md", "hello\n", "initial commit")
    return root


def test_find_repo_root(repo: Path) -> None:
    assert rs.find_repo_root(repo) == repo.resolve()
    nested = repo / "sub"
    nested.mkdir()
    assert rs.find_repo_root(nested) == repo.resolve()


def test_current_branch_and_head_sha(repo: Path) -> None:
    assert rs.current_branch(repo) == "main"
    head = _git(repo, "rev-parse", "HEAD")
    assert rs.head_sha(repo) == head


def test_dirty_status_reports_staged_unstaged_untracked(repo: Path) -> None:
    clean = rs.dirty_status(repo)
    assert clean == {"ok": True, "staged": [], "unstaged": [], "untracked": [], "raw": []}

    (repo / "untracked.txt").write_text("x", encoding="utf-8")
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    (repo / "staged_then_edited.txt").write_text("a", encoding="utf-8")
    _git(repo, "add", "staged_then_edited.txt")
    (repo / "staged_then_edited.txt").write_text("b", encoding="utf-8")

    status = rs.dirty_status(repo)
    assert "untracked.txt" in status["untracked"]
    assert "README.md" in status["staged"]
    assert "staged_then_edited.txt" in status["staged"]
    assert "staged_then_edited.txt" in status["unstaged"]


def test_worktrees_lists_main_worktree(repo: Path) -> None:
    entries = rs.worktrees(repo)
    assert len(entries) == 1
    assert Path(entries[0]["path"]).resolve() == repo.resolve()
    assert entries[0]["branch"] == "main"
    assert entries[0]["dirty"] is False


def test_worktrees_detects_second_worktree_and_dirty_flag(repo: Path, tmp_path: Path) -> None:
    other = tmp_path / "other-worktree"
    _git(repo, "worktree", "add", "-q", "-b", "feature", str(other))
    (other / "scratch.txt").write_text("x", encoding="utf-8")

    entries = rs.worktrees(repo)
    assert len(entries) == 2
    by_branch = {e["branch"]: e for e in entries}
    assert "feature" in by_branch
    assert by_branch["feature"]["dirty"] is True
    assert "scratch.txt" in by_branch["feature"]["dirty_files"]


def test_stashes_list(repo: Path) -> None:
    assert rs.stashes(repo) == []
    (repo / "README.md").write_text("stash me\n", encoding="utf-8")
    _git(repo, "stash", "push", "-u", "-m", "wip work")
    stashes = rs.stashes(repo)
    assert len(stashes) == 1
    assert "wip work" in stashes[0]["subject"]


def test_main_sync_state_in_sync_ahead_diverged(repo: Path, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "-u", "origin", "main")

    sync = rs.main_sync_state(repo, local_ref="main", remote_ref="origin/main")
    assert sync["state"] == "IN_SYNC"

    _commit(repo, "ahead.txt", "ahead\n", "ahead commit")
    sync = rs.main_sync_state(repo, local_ref="main", remote_ref="origin/main")
    assert sync["state"] == "AHEAD"
    assert sync["ahead"] == 1
    assert sync["behind"] == 0

    # Simulate someone else having pushed a different commit to origin/main
    # off the same base commit, built in a throwaway clone so this repo's own
    # working tree/index stay untouched, then point the remote-tracking ref
    # at it directly (no network needed for this synthetic scenario).
    other_clone = tmp_path / "other-clone"
    subprocess.run(["git", "clone", "-q", str(remote), str(other_clone)], check=True)
    _git(other_clone, "config", "user.email", "test@example.com")
    _git(other_clone, "config", "user.name", "Test")
    _commit(other_clone, "diverged.txt", "diverged\n", "diverged commit")
    _git(other_clone, "push", "-q", "origin", "HEAD:refs/heads/tmp-diverged")
    # Bring the new object into `repo`'s object database via a real fetch,
    # then point the remote-tracking ref at it directly (no merge/checkout).
    _git(repo, "fetch", "-q", "origin", "tmp-diverged")
    diverged_sha = _git(repo, "rev-parse", "FETCH_HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/main", diverged_sha)

    sync = rs.main_sync_state(repo, local_ref="main", remote_ref="origin/main")
    assert sync["state"] == "DIVERGED"
    assert sync["ahead"] == 1
    assert sync["behind"] == 1


def test_main_sync_state_unknown_when_ref_missing(repo: Path) -> None:
    sync = rs.main_sync_state(repo, local_ref="main", remote_ref="origin/main")
    assert sync["state"] == rs.UNKNOWN
    assert sync["remote_sha"] is None


def test_local_branches_reports_gone_and_merged(repo: Path) -> None:
    _git(repo, "branch", "merged-branch")
    _git(repo, "checkout", "-q", "-b", "unmerged-branch")
    _commit(repo, "unique.txt", "unique\n", "unique commit")
    _git(repo, "checkout", "-q", "main")

    branches = {b["name"]: b for b in rs.local_branches(repo)}
    assert branches["merged-branch"]["merged_into_main"] is True
    assert branches["unmerged-branch"]["merged_into_main"] is False


def test_archive_tags_and_unmerged_branch_evidence(repo: Path) -> None:
    _git(repo, "checkout", "-q", "-b", "research/unique-work")
    sha = _commit(repo, "research.txt", "research\n", "research commit")
    _git(repo, "checkout", "-q", "main")

    # Not preserved yet.
    report = rs.unmerged_branch_evidence(repo, include_remote=False)
    entry = next(b for b in report["branches"] if b["name"] == "research/unique-work")
    assert entry["has_unique_evidence"] is True
    assert entry["archive_tag_preserved"] is False
    assert entry["blocker"] is True
    assert "research/unique-work" in report["blockers"]

    # Now preserve it with an archive tag pointing at the exact tip.
    _git(repo, "tag", "archive/claude-research-unique-work-2026-01-01", sha)
    report = rs.unmerged_branch_evidence(repo, include_remote=False)
    entry = next(b for b in report["branches"] if b["name"] == "research/unique-work")
    assert entry["archive_tag_preserved"] is True
    assert entry["blocker"] is False
    assert report["blockers"] == []


def test_repo_slug_parses_ssh_and_https(repo: Path) -> None:
    _git(repo, "remote", "add", "origin", "git@github.com:example-org/example-repo.git")
    assert rs.repo_slug(repo) == "example-org/example-repo"
    _git(repo, "remote", "set-url", "origin", "https://github.com/example-org/example-repo.git")
    assert rs.repo_slug(repo) == "example-org/example-repo"


def test_fetch_remote_never_raises_without_a_remote(repo: Path) -> None:
    assert rs.fetch_remote(repo) is False
