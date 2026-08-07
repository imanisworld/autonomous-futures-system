from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ops import project_check_git as pcg


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-b", "main"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "test@example.com"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "Test User"], cwd=repo)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "README.md"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "init"], cwd=repo)
    return repo


def test_current_branch_and_head_sha(tmp_path):
    repo = _init_repo(tmp_path)
    assert pcg.current_branch(repo) == "main"
    assert pcg.head_sha(repo) == _git(repo, "rev-parse", "HEAD")


def test_current_branch_detached_head_is_none(tmp_path):
    repo = _init_repo(tmp_path)
    sha = _git(repo, "rev-parse", "HEAD")
    subprocess.check_call(["git", "checkout", sha], cwd=repo, stderr=subprocess.DEVNULL)
    assert pcg.current_branch(repo) is None


def test_porcelain_status_splits_staged_dirty_untracked(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("changed\n", encoding="utf-8")  # dirty tracked
    (repo / "new.txt").write_text("new\n", encoding="utf-8")  # untracked
    (repo / "staged.txt").write_text("staged\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "staged.txt"], cwd=repo)

    status = pcg.porcelain_status(repo)
    assert status["dirty"] == ["README.md"]
    assert status["staged"] == ["staged.txt"]
    assert status["untracked"] == ["new.txt"]
    assert status["available"] is True


def test_worktrees_lists_current_worktree(tmp_path):
    repo = _init_repo(tmp_path)
    entries = pcg.worktrees(repo)
    assert len(entries) == 1
    assert entries[0]["branch"] == "main"
    assert Path(entries[0]["path"]).resolve() == repo.resolve()


def test_local_branches_flags_local_only(tmp_path):
    repo = _init_repo(tmp_path)
    subprocess.check_call(["git", "branch", "feature/local-only"], cwd=repo)
    branches = {b["branch"]: b for b in pcg.local_branches(repo)}
    assert branches["feature/local-only"]["local_only"] is True
    assert branches["feature/local-only"]["tracking_gone"] is False


def test_archive_tags_and_has_archive_tag_for_branch(tmp_path):
    repo = _init_repo(tmp_path)
    subprocess.check_call(["git", "tag", "archive/some-branch-2026-01-01"], cwd=repo)
    subprocess.check_call(["git", "tag", "not-an-archive-tag"], cwd=repo)
    tags = pcg.archive_tags(repo)
    assert tags == ["archive/some-branch-2026-01-01"]
    assert pcg.has_archive_tag_for_branch(tags, "some-branch") is True
    assert pcg.has_archive_tag_for_branch(tags, "other-branch") is False


def test_sync_status_in_sync_ahead_behind_diverged(tmp_path):
    repo = _init_repo(tmp_path)
    subprocess.check_call(["git", "branch", "origin-stand-in"], cwd=repo)
    assert pcg.sync_status(repo, "origin-stand-in", "HEAD") == "IN_SYNC"

    (repo / "f2.txt").write_text("x\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "f2.txt"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "second"], cwd=repo)
    assert pcg.sync_status(repo, "origin-stand-in", "HEAD") == "AHEAD"
    assert pcg.sync_status(repo, "HEAD", "origin-stand-in") == "BEHIND"

    subprocess.check_call(["git", "checkout", "origin-stand-in"], cwd=repo)
    (repo / "f3.txt").write_text("y\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "f3.txt"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "diverge"], cwd=repo)
    subprocess.check_call(["git", "checkout", "main"], cwd=repo)
    assert pcg.sync_status(repo, "origin-stand-in", "HEAD") == "DIVERGED"


def test_sync_status_unknown_ref():
    assert pcg.sync_status(Path("."), None) == "UNKNOWN"


def test_branch_unique_commits(tmp_path):
    repo = _init_repo(tmp_path)
    subprocess.check_call(["git", "checkout", "-b", "feature"], cwd=repo)
    (repo / "feat.txt").write_text("x\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "feat.txt"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "feature work"], cwd=repo)
    subprocess.check_call(["git", "checkout", "main"], cwd=repo)

    assert pcg.branch_unique_commits(repo, "feature", base="main") == 1
    assert pcg.branch_unique_commits(repo, "main", base="main") == 0
    assert pcg.branch_unique_commits(repo, "does-not-exist", base="main") is None


def test_find_unpreserved_closed_branches_flags_missing_archive_tag(tmp_path):
    repo = _init_repo(tmp_path)
    subprocess.check_call(["git", "checkout", "-b", "feature/no-tag"], cwd=repo)
    (repo / "feat.txt").write_text("x\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "feat.txt"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "feature work"], cwd=repo)
    subprocess.check_call(["git", "checkout", "main"], cwd=repo)

    closed_prs = [
        {"headRefName": "feature/no-tag", "number": 1, "mergedAt": None},
        {"headRefName": "feature/no-tag", "number": 2, "mergedAt": "2026-01-01T00:00:00Z"},  # merged, skip
    ]
    tags = pcg.archive_tags(repo)
    result = pcg.find_unpreserved_closed_branches(repo, closed_prs, tags)
    assert len(result) == 1
    assert result[0]["branch"] == "feature/no-tag"
    assert result[0]["unique_commits"] == 1

    subprocess.check_call(["git", "tag", "archive/feature/no-tag-2026-01-01"], cwd=repo)
    tags = pcg.archive_tags(repo)
    result = pcg.find_unpreserved_closed_branches(repo, closed_prs, tags)
    assert result == []


def test_run_gh_degrades_gracefully_when_missing(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("PATH", "/nonexistent-bin-dir")
    ok, detail = pcg._run_gh(repo, ["pr", "list"])
    assert ok is False
    assert "unavailable" in detail


def test_list_prs_degrades_when_gh_unavailable(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("PATH", "/nonexistent-bin-dir")
    result = pcg.list_prs(repo, state="open")
    assert result["available"] is False
    assert result["prs"] == []
