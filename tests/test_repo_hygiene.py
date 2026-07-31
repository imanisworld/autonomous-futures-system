from __future__ import annotations

import subprocess
from pathlib import Path

from ops import repo_hygiene


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_repo(tmp_path: Path, name: str = "repo") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    subprocess.check_call(["git", "init", "-q", "-b", "main"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "test@example.com"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "Test User"], cwd=repo)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "README.md"], cwd=repo)
    subprocess.check_call(["git", "commit", "-q", "-m", "initial"], cwd=repo)
    return repo


def _make_remote_pair(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    subprocess.check_call(["git", "init", "-q", "--bare", "-b", "main", str(remote)])
    local = _init_repo(tmp_path, "local")
    subprocess.check_call(["git", "remote", "add", "origin", str(remote)], cwd=local)
    subprocess.check_call(["git", "push", "-q", "-u", "origin", "main"], cwd=local)
    return local, remote


def test_repo_identity(tmp_path):
    repo = _init_repo(tmp_path)
    identity = repo_hygiene.repo_identity(repo)
    assert identity["branch"] == "main"
    assert identity["head_sha"] == _git(repo, "rev-parse", "HEAD")
    assert identity["detached_head"] is False


def test_main_sync_status_in_sync(tmp_path):
    local, _remote = _make_remote_pair(tmp_path)
    status = repo_hygiene.main_sync_status(local)
    assert status["relationship"] == "IN_SYNC"


def test_main_sync_status_ahead(tmp_path):
    local, _remote = _make_remote_pair(tmp_path)
    (local / "new.txt").write_text("x\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "new.txt"], cwd=local)
    subprocess.check_call(["git", "commit", "-q", "-m", "second"], cwd=local)
    status = repo_hygiene.main_sync_status(local)
    assert status["relationship"] == "AHEAD"
    assert status["ahead"] == 1
    assert status["behind"] == 0


def test_main_sync_status_diverged(tmp_path):
    local, remote = _make_remote_pair(tmp_path)
    # advance origin/main independently via a second clone
    other = tmp_path / "other"
    subprocess.check_call(["git", "clone", "-q", str(remote), str(other)])
    subprocess.check_call(["git", "config", "user.email", "test@example.com"], cwd=other)
    subprocess.check_call(["git", "config", "user.name", "Test User"], cwd=other)
    (other / "remote_only.txt").write_text("x\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "remote_only.txt"], cwd=other)
    subprocess.check_call(["git", "commit", "-q", "-m", "remote-side"], cwd=other)
    subprocess.check_call(["git", "push", "-q"], cwd=other)

    (local / "local_only.txt").write_text("y\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "local_only.txt"], cwd=local)
    subprocess.check_call(["git", "commit", "-q", "-m", "local-side"], cwd=local)
    subprocess.check_call(["git", "fetch", "-q", "origin"], cwd=local)

    status = repo_hygiene.main_sync_status(local)
    assert status["relationship"] == "DIVERGED"
    assert status["ahead"] == 1
    assert status["behind"] == 1


def test_main_sync_status_unknown_when_remote_ref_missing(tmp_path):
    repo = _init_repo(tmp_path)
    status = repo_hygiene.main_sync_status(repo)
    assert status["relationship"] == "UNKNOWN"


def test_working_tree_status_classifies_staged_dirty_untracked(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("changed but not staged\n", encoding="utf-8")
    (repo / "staged.txt").write_text("staged\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "staged.txt"], cwd=repo)
    (repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")

    status = repo_hygiene.working_tree_status(repo)
    assert "README.md" in status["dirty_files"]
    assert "staged.txt" in status["staged_files"]
    assert "untracked.txt" in status["untracked_files"]
    assert status["clean"] is False


def test_working_tree_status_clean(tmp_path):
    repo = _init_repo(tmp_path)
    status = repo_hygiene.working_tree_status(repo)
    assert status == {
        "staged_files": [],
        "dirty_files": [],
        "untracked_files": [],
        "clean": True,
    }


def test_archive_tags_and_branch_unique_vs_main(tmp_path):
    local, remote = _make_remote_pair(tmp_path)
    subprocess.check_call(["git", "checkout", "-q", "-b", "feature/x"], cwd=local)
    (local / "feature.txt").write_text("feature\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "feature.txt"], cwd=local)
    subprocess.check_call(["git", "commit", "-q", "-m", "feature work"], cwd=local)
    feature_sha = _git(local, "rev-parse", "HEAD")

    # No archive tag yet: unique commits exist, no archive tag on the tip.
    evidence = repo_hygiene.branch_unique_vs_main(local, "feature/x", main_ref="main")
    assert evidence["resolvable"] is True
    assert evidence["unique_commit_count"] == 1
    assert evidence["archive_tags"] == []

    subprocess.check_call(
        ["git", "tag", "-a", "archive/feature-x-2026-01-01", "-m", "archive", feature_sha],
        cwd=local,
    )
    assert repo_hygiene.archive_tags(local) == ["archive/feature-x-2026-01-01"]
    evidence_after = repo_hygiene.branch_unique_vs_main(local, "feature/x", main_ref="main")
    assert evidence_after["archive_tags"] == ["archive/feature-x-2026-01-01"]


def test_branches_tracking_deleted_remotes_and_local_only(tmp_path):
    local, remote = _make_remote_pair(tmp_path)
    subprocess.check_call(["git", "checkout", "-q", "-b", "gone-branch"], cwd=local)
    subprocess.check_call(["git", "push", "-q", "-u", "origin", "gone-branch"], cwd=local)
    subprocess.check_call(["git", "push", "-q", "origin", "--delete", "gone-branch"], cwd=local)
    subprocess.check_call(["git", "fetch", "-q", "--prune", "origin"], cwd=local)

    subprocess.check_call(["git", "checkout", "-q", "-b", "local-only-branch"], cwd=local)

    assert "gone-branch" in repo_hygiene.branches_tracking_deleted_remotes(local)
    assert "local-only-branch" in repo_hygiene.local_only_branches(local)


def test_worktrees_lists_current_checkout(tmp_path):
    repo = _init_repo(tmp_path)
    wts = repo_hygiene.worktrees(repo)
    assert len(wts) == 1
    assert Path(wts[0]["path"]).resolve() == repo.resolve()
    assert wts[0]["branch"] == "refs/heads/main"
    assert wts[0]["dirty"] is False


def test_stashes_lists_entries(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "README.md").write_text("dirty for stash\n", encoding="utf-8")
    subprocess.check_call(["git", "stash", "push", "-q", "-m", "wip work"], cwd=repo)
    entries = repo_hygiene.stashes(repo)
    assert len(entries) == 1
    assert "wip work" in entries[0]["subject"]


def test_gh_pr_list_reports_unavailable_without_gh(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("PATH", "/nonexistent")
    result = repo_hygiene.gh_pr_list(repo)
    assert result["available"] is False
    assert result["prs"] == []
