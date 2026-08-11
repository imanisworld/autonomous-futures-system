from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ops import project_check_git as pcgit


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "--initial-branch=main", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "a.txt").write_text("one\n", encoding="utf-8")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-q", "-m", "init")
    return root


def test_current_branch_and_head_sha(repo: Path):
    assert pcgit.current_branch(repo) == "main"
    sha = pcgit.head_sha(repo)
    assert sha and len(sha) == 40


def test_working_tree_status_classifies_staged_dirty_untracked(repo: Path):
    (repo / "a.txt").write_text("one\ntwo\n", encoding="utf-8")  # dirty (unstaged tracked edit)
    (repo / "b.txt").write_text("new\n", encoding="utf-8")  # untracked
    (repo / "c.txt").write_text("staged\n", encoding="utf-8")
    _git(repo, "add", "c.txt")

    status = pcgit.working_tree_status(repo)
    assert status["dirty"] == ["a.txt"]
    assert status["untracked"] == ["b.txt"]
    assert status["staged"] == ["c.txt"]


def test_working_tree_status_clean_repo_is_empty(repo: Path):
    status = pcgit.working_tree_status(repo)
    assert status == {"staged": [], "dirty": [], "untracked": []}


def test_sync_state_in_sync_ahead_behind_diverged(repo: Path):
    _git(repo, "branch", "topic")
    _git(repo, "checkout", "-q", "topic")
    (repo / "a.txt").write_text("topic change\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "topic commit")

    assert pcgit.sync_state("topic", "main", repo) == "AHEAD"
    assert pcgit.sync_state("main", "topic", repo) == "BEHIND"

    _git(repo, "checkout", "-q", "main")
    (repo / "a.txt").write_text("main change\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "main commit")
    assert pcgit.sync_state("main", "topic", repo) == "DIVERGED"

    _git(repo, "branch", "same")
    assert pcgit.sync_state("main", "same", repo) == "IN_SYNC"


def test_sync_state_unknown_for_missing_ref(repo: Path):
    assert pcgit.sync_state("main", "does-not-exist", repo) == "UNKNOWN"


def test_worktree_list_reports_additional_worktree(repo: Path, tmp_path: Path):
    _git(repo, "branch", "feature")
    wt_path = tmp_path / "wt"
    _git(repo, "worktree", "add", str(wt_path), "feature")

    worktrees = pcgit.worktree_list(repo)
    paths = {Path(w["path"]).resolve() for w in worktrees}
    assert repo.resolve() in paths
    assert wt_path.resolve() in paths
    feature_wt = next(w for w in worktrees if Path(w["path"]).resolve() == wt_path.resolve())
    assert feature_wt["branch"].endswith("feature")


def test_local_only_and_gone_branches(repo: Path):
    # No remote configured in this fixture, so every branch (including main)
    # has no upstream -- local_only_branches() reports all of them.
    _git(repo, "branch", "orphan")
    local_only = pcgit.local_only_branches(repo)
    assert "orphan" in local_only
    assert "main" in local_only


def test_merged_and_unmerged_branches(repo: Path):
    _git(repo, "branch", "merged-branch")
    _git(repo, "checkout", "-q", "-b", "unmerged-branch")
    (repo / "a.txt").write_text("unmerged change\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "unmerged commit")
    _git(repo, "checkout", "-q", "main")

    assert "merged-branch" in pcgit.merged_branches("main", repo)
    assert "unmerged-branch" not in pcgit.merged_branches("main", repo)
    assert "unmerged-branch" in pcgit.unmerged_branches("main", repo)


def test_archive_tags_filters_to_prefix(repo: Path):
    _git(repo, "tag", "-a", "archive/foo-2026-01-01", "-m", "archived")
    _git(repo, "tag", "-a", "v1.0.0", "-m", "release")
    tags = pcgit.archive_tags(repo)
    assert tags == ["archive/foo-2026-01-01"]


def test_branch_unique_commits(repo: Path):
    _git(repo, "checkout", "-q", "-b", "topic2")
    (repo / "a.txt").write_text("one more\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "extra commit")
    assert pcgit.branch_unique_commits("topic2", "main", repo) == 1
    assert pcgit.branch_unique_commits("main", "main", repo) == 0


def test_stash_list_reports_entries(repo: Path):
    (repo / "a.txt").write_text("stash me\n", encoding="utf-8")
    _git(repo, "stash", "push", "-m", "wip stash")
    stashes = pcgit.stash_list(repo)
    assert len(stashes) == 1
    assert "wip stash" in stashes[0]["label"]


def test_gh_json_returns_none_when_gh_missing(monkeypatch):
    monkeypatch.setattr(pcgit.shutil, "which", lambda _name: None)
    result, err = pcgit.gh_json(["pr", "list"])
    assert result is None
    assert err is not None


def test_fetch_origin_fails_gracefully_without_remote(repo: Path):
    ok, err = pcgit.fetch_origin(repo, timeout=5.0)
    assert ok is False
    assert err
