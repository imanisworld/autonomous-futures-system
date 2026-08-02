from __future__ import annotations

import subprocess
from pathlib import Path

from ops import project_check_git as pcg


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "a.txt").write_text("1\n")
    subprocess.run(["git", "add", "a.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


def test_repo_root_and_branch(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    assert pcg.repo_root(str(tmp_path)) == str(tmp_path.resolve())
    assert pcg.current_branch(str(tmp_path)) == "main"
    assert pcg.is_detached(str(tmp_path)) is False
    assert pcg.head_sha(str(tmp_path))


def test_dirty_files_reports_modified_staged_untracked(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("2\n")  # modified, unstaged
    (tmp_path / "b.txt").write_text("new\n")  # untracked
    subprocess.run(["git", "add", "b.txt"], cwd=tmp_path, check=True)  # now staged

    dirty = pcg.dirty_files(str(tmp_path))
    assert dirty["ok"] is True
    assert "a.txt" in dirty["modified"]
    assert "b.txt" in dirty["staged"]
    assert "b.txt" not in dirty["untracked"]


def test_dirty_files_clean_repo(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    dirty = pcg.dirty_files(str(tmp_path))
    assert dirty == {"modified": [], "staged": [], "untracked": [], "ok": True}


def test_worktrees_lists_current_worktree(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    wts = pcg.worktrees(str(tmp_path))
    assert len(wts) == 1
    assert wts[0]["path"] == str(tmp_path.resolve())
    assert wts[0]["branch"] == "main"


def test_stash_list_empty_and_populated(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    assert pcg.stash_list(str(tmp_path)) == []
    (tmp_path / "a.txt").write_text("stashme\n")
    subprocess.run(["git", "stash", "push", "-u", "-m", "wip"], cwd=tmp_path, check=True)
    stashes = pcg.stash_list(str(tmp_path))
    assert len(stashes) == 1
    assert "wip" in stashes[0]["label"]


def test_sync_status_in_sync_ahead_behind_diverged(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    base = pcg.head_sha(str(tmp_path))
    assert pcg.sync_status(base, base, str(tmp_path)) == "IN_SYNC"

    (tmp_path / "c.txt").write_text("ahead\n")
    subprocess.run(["git", "add", "c.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "ahead"], cwd=tmp_path, check=True)
    ahead = pcg.head_sha(str(tmp_path))
    assert pcg.sync_status(ahead, base, str(tmp_path)) == "AHEAD"
    assert pcg.sync_status(base, ahead, str(tmp_path)) == "BEHIND"

    subprocess.run(["git", "checkout", "-q", "-b", "other", base], cwd=tmp_path, check=True)
    (tmp_path / "d.txt").write_text("diverge\n")
    subprocess.run(["git", "add", "d.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "diverge"], cwd=tmp_path, check=True)
    diverged = pcg.head_sha(str(tmp_path))
    assert pcg.sync_status(diverged, ahead, str(tmp_path)) == "DIVERGED"


def test_local_branches_gone_and_local_only(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    subprocess.run(["git", "branch", "feature-a"], cwd=tmp_path, check=True)
    branches = pcg.local_branches(str(tmp_path))
    names = {b["name"] for b in branches}
    assert {"main", "feature-a"} <= names
    feature = next(b for b in branches if b["name"] == "feature-a")
    assert feature["local_only"] is True
    assert feature["gone"] is False


def test_merged_and_not_merged_into(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    base = pcg.head_sha(str(tmp_path))
    subprocess.run(["git", "checkout", "-q", "-b", "feature-b"], cwd=tmp_path, check=True)
    (tmp_path / "e.txt").write_text("unmerged\n")
    subprocess.run(["git", "add", "e.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "unmerged work"], cwd=tmp_path, check=True)
    subprocess.run(["git", "checkout", "-q", "main"], cwd=tmp_path, check=True)

    assert "feature-b" in pcg.not_merged_into("main", str(tmp_path))
    assert "feature-b" not in pcg.merged_into("main", str(tmp_path))


def test_archive_tags_and_tags_pointing_at(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    sha = pcg.head_sha(str(tmp_path))
    subprocess.run(
        ["git", "tag", "-a", "archive/demo-2026-01-01", "-m", "archive"], cwd=tmp_path, check=True
    )
    tags = pcg.archive_tags(str(tmp_path))
    assert any(t["tag"] == "archive/demo-2026-01-01" for t in tags)
    assert "archive/demo-2026-01-01" in pcg.tags_pointing_at(sha, str(tmp_path))


def test_gh_pr_list_returns_none_without_gh(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pcg, "gh_available", lambda: False)
    assert pcg.gh_pr_list(str(tmp_path)) is None
