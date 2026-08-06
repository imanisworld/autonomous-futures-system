from __future__ import annotations

import subprocess
from pathlib import Path

from ops.git_state import (
    ahead_behind,
    archive_tags,
    branch_summary,
    git_state_report,
    list_worktrees,
    parse_status,
    stash_list,
    sync_status,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-b", "main"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "test@example.com"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "Test User"], cwd=repo)
    (repo / "a.txt").write_text("one\n")
    subprocess.check_call(["git", "add", "a.txt"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "init"], cwd=repo)
    return repo


def _init_remote_pair(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    subprocess.check_call(["git", "init", "--bare", "-b", "main", str(remote)])
    repo = _init_repo(tmp_path)
    subprocess.check_call(["git", "remote", "add", "origin", str(remote)], cwd=repo)
    subprocess.check_call(["git", "push", "-u", "origin", "main"], cwd=repo)
    return repo, remote


def test_sync_status_maps_ahead_behind_pairs():
    assert sync_status((0, 0)) == "IN_SYNC"
    assert sync_status((2, 0)) == "AHEAD"
    assert sync_status((0, 3)) == "BEHIND"
    assert sync_status((1, 1)) == "DIVERGED"
    assert sync_status(None) == "UNKNOWN"


def test_parse_status_splits_staged_unstaged_untracked():
    lines = ["M  staged.txt", " M unstaged.txt", "?? new.txt", "AM both.txt"]
    parsed = parse_status(lines)
    assert parsed["staged"] == ["staged.txt", "both.txt"]
    assert parsed["unstaged"] == ["unstaged.txt", "both.txt"]
    assert parsed["untracked"] == ["new.txt"]


def test_git_state_report_reports_in_sync_with_origin(tmp_path):
    repo, _remote = _init_remote_pair(tmp_path)
    report = git_state_report(repo, base_branch="main")
    assert report["current_branch"] == "main"
    assert report["local_main_relationship"] == "IN_SYNC"
    assert report["ahead"] == 0 and report["behind"] == 0
    assert report["dirty_tracked_files"] == []
    assert report["staged_files"] == []
    assert report["untracked_files"] == []
    assert report["current_worktree"] is not None
    assert report["current_worktree"]["path"] == str(repo)


def test_git_state_report_detects_ahead_and_dirty_and_untracked(tmp_path):
    repo, _remote = _init_remote_pair(tmp_path)
    (repo / "b.txt").write_text("two\n")
    subprocess.check_call(["git", "add", "b.txt"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "second"], cwd=repo)
    (repo / "a.txt").write_text("one changed\n")
    (repo / "c.txt").write_text("new\n")

    report = git_state_report(repo, base_branch="main")
    assert report["local_main_relationship"] == "AHEAD"
    assert report["ahead"] == 1 and report["behind"] == 0
    assert "a.txt" in report["dirty_tracked_files"]
    assert "c.txt" in report["untracked_files"]


def test_ahead_behind_unavailable_without_remote(tmp_path):
    repo = _init_repo(tmp_path)
    assert ahead_behind(repo, "HEAD", "origin/main") is None


def test_branch_summary_flags_local_only_and_deleted_remote_tracking(tmp_path):
    repo, remote = _init_remote_pair(tmp_path)
    subprocess.check_call(["git", "checkout", "-b", "feature/local-only"], cwd=repo)
    subprocess.check_call(["git", "checkout", "-b", "feature/gone"], cwd=repo)
    subprocess.check_call(["git", "push", "-u", "origin", "feature/gone"], cwd=repo)
    subprocess.check_call(["git", "push", "origin", "--delete", "feature/gone"], cwd=repo)
    subprocess.check_call(["git", "fetch", "--prune"], cwd=repo)
    subprocess.check_call(["git", "checkout", "main"], cwd=repo)

    rows = {row["branch"]: row for row in branch_summary(repo)}
    assert rows["feature/local-only"]["local_only"] is True
    assert rows["feature/local-only"]["tracking_deleted_remote"] is False
    assert rows["feature/gone"]["tracking_deleted_remote"] is True
    assert rows["main"]["local_only"] is False


def test_list_worktrees_includes_added_worktree(tmp_path):
    repo, _remote = _init_remote_pair(tmp_path)
    subprocess.check_call(["git", "branch", "wt-branch"], cwd=repo)
    wt_path = tmp_path / "wt"
    subprocess.check_call(["git", "worktree", "add", str(wt_path), "wt-branch"], cwd=repo)

    worktrees = list_worktrees(repo)
    paths = {str(Path(wt["path"]).resolve()) for wt in worktrees}
    assert str(repo.resolve()) in paths
    assert str(wt_path.resolve()) in paths


def test_stash_list_and_archive_tags(tmp_path):
    repo, _remote = _init_remote_pair(tmp_path)
    (repo / "a.txt").write_text("stashed change\n")
    subprocess.check_call(["git", "stash", "push", "-m", "wip"], cwd=repo)
    subprocess.check_call(["git", "tag", "-a", "archive/old-branch-2026-01-01", "-m", "archive"], cwd=repo)

    assert len(stash_list(repo)) == 1
    assert archive_tags(repo) == ["archive/old-branch-2026-01-01"]


def test_git_state_report_never_mutates_repo(tmp_path):
    repo, _remote = _init_remote_pair(tmp_path)
    before = _git(repo, "rev-parse", "HEAD")
    git_state_report(repo, base_branch="main")
    after = _git(repo, "rev-parse", "HEAD")
    assert before == after
    assert _git(repo, "status", "--porcelain") == ""
