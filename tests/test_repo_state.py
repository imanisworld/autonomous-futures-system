"""tests/test_repo_state.py

Proves ops.repo_state's read-only git/worktree/branch inspection against a
real (throwaway) git repository built in tmp_path -- never against this
actual repo's live state, so it stays deterministic regardless of what
branch/worktree this test suite happens to run under.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ops import repo_state


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-q", "-b", "main"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "test@example.com"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "Test User"], cwd=repo)
    (repo / "a.txt").write_text("1\n")
    subprocess.check_call(["git", "add", "a.txt"], cwd=repo)
    subprocess.check_call(["git", "commit", "-q", "-m", "init"], cwd=repo)
    return repo


def _clone_as_origin(repo: Path, tmp_path: Path) -> Path:
    bare = tmp_path / "origin.git"
    subprocess.check_call(["git", "init", "-q", "--bare", "-b", "main", str(bare)])
    subprocess.check_call(["git", "remote", "add", "origin", str(bare)], cwd=repo)
    subprocess.check_call(["git", "push", "-q", "-u", "origin", "main"], cwd=repo)
    return bare


def test_read_only_never_mutates_repo(tmp_path):
    repo = _init_repo(tmp_path)
    before = _git(repo, "rev-parse", "HEAD")
    repo_state.build_report(cwd=repo, include_prs=False)
    after = _git(repo, "rev-parse", "HEAD")
    assert before == after
    # Confirm nothing was staged/modified by the read either.
    assert _git(repo, "status", "--porcelain") == ""


def test_in_sync_when_local_matches_origin(tmp_path):
    repo = _init_repo(tmp_path)
    _clone_as_origin(repo, tmp_path)
    assert repo_state.main_sync_state(cwd=repo) == "IN_SYNC"


def test_ahead_when_local_has_unpushed_commit(tmp_path):
    repo = _init_repo(tmp_path)
    _clone_as_origin(repo, tmp_path)
    (repo / "b.txt").write_text("2\n")
    subprocess.check_call(["git", "add", "b.txt"], cwd=repo)
    subprocess.check_call(["git", "commit", "-q", "-m", "second"], cwd=repo)
    assert repo_state.main_sync_state(cwd=repo) == "AHEAD"


def test_behind_when_origin_has_a_commit_local_lacks(tmp_path):
    repo = _init_repo(tmp_path)
    bare = _clone_as_origin(repo, tmp_path)
    other = tmp_path / "other"
    subprocess.check_call(["git", "clone", "-q", "--branch", "main", str(bare), str(other)])
    subprocess.check_call(["git", "config", "user.email", "test@example.com"], cwd=other)
    subprocess.check_call(["git", "config", "user.name", "Test User"], cwd=other)
    (other / "c.txt").write_text("3\n")
    subprocess.check_call(["git", "add", "c.txt"], cwd=other)
    subprocess.check_call(["git", "commit", "-q", "-m", "third"], cwd=other)
    subprocess.check_call(["git", "push", "-q"], cwd=other)
    subprocess.check_call(["git", "fetch", "-q", "origin"], cwd=repo)
    assert repo_state.main_sync_state(cwd=repo) == "BEHIND"


def test_dirty_staged_and_untracked_files_are_distinguished(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "a.txt").write_text("changed\n")
    (repo / "staged.txt").write_text("staged\n")
    subprocess.check_call(["git", "add", "staged.txt"], cwd=repo)
    (repo / "untracked.txt").write_text("new\n")

    assert repo_state.dirty_tracked_files(cwd=repo) == ["a.txt"]
    assert repo_state.staged_files(cwd=repo) == ["staged.txt"]
    assert repo_state.untracked_files(cwd=repo) == ["untracked.txt"]


def test_local_only_branch_has_no_upstream(tmp_path):
    repo = _init_repo(tmp_path)
    _clone_as_origin(repo, tmp_path)
    subprocess.check_call(["git", "checkout", "-q", "-b", "feature/local-only"], cwd=repo)
    branches = repo_state.local_branches(cwd=repo)
    by_name = {b.name: b for b in branches}
    assert by_name["feature/local-only"].upstream is None
    assert "feature/local-only" in repo_state.local_only_branches(cwd=repo)
    assert by_name["main"].upstream == "origin/main"


def test_worktree_branch_names_with_slashes_are_not_truncated(tmp_path):
    repo = _init_repo(tmp_path)
    subprocess.check_call(["git", "branch", "claude/some-feature"], cwd=repo)
    linked = tmp_path / "linked-worktree"
    subprocess.check_call(
        ["git", "worktree", "add", "-q", str(linked), "claude/some-feature"], cwd=repo
    )
    worktrees = repo_state.list_worktrees(cwd=repo)
    branches = {wt.branch for wt in worktrees}
    assert "claude/some-feature" in branches
    assert "some-feature" not in branches


def test_archive_tags_lists_only_archive_prefixed_tags(tmp_path):
    repo = _init_repo(tmp_path)
    subprocess.check_call(["git", "tag", "archive/old-branch"], cwd=repo)
    subprocess.check_call(["git", "tag", "v1.0.0"], cwd=repo)
    tags = repo_state.archive_tags(cwd=repo)
    assert tags == ["archive/old-branch"]


def test_stash_list_reports_entries_without_dropping_them(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "a.txt").write_text("stash me\n")
    subprocess.check_call(["git", "stash", "push", "-q", "-m", "wip"], cwd=repo)
    stashes = repo_state.stash_list(cwd=repo)
    assert len(stashes) == 1
    # Confirm the read didn't drop or pop the stash.
    assert len(repo_state.stash_list(cwd=repo)) == 1


def test_gh_unavailable_degrades_to_none_not_a_raise(monkeypatch, tmp_path):
    repo = _init_repo(tmp_path)
    monkeypatch.setattr(repo_state, "gh_available", lambda: False)
    assert repo_state.open_prs(cwd=repo) is None
    report = repo_state.build_report(cwd=repo, include_prs=True)
    assert report.open_prs is None
    assert report.as_dict()["open_prs_status"].startswith("UNKNOWN")
