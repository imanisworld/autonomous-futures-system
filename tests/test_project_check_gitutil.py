from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ops.project_check import gitutil


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


def test_run_git_rejects_unlisted_subcommand(repo: Path) -> None:
    with pytest.raises(ValueError):
        gitutil.run_git(["commit", "-m", "nope"], cwd=repo)


def test_run_git_rejects_mutating_subcommands_even_if_spelled_out(repo: Path) -> None:
    for banned in ("push", "pull", "reset", "rebase", "checkout", "merge", "cherry-pick", "clean"):
        with pytest.raises(ValueError):
            gitutil.run_git([banned], cwd=repo)


@pytest.mark.parametrize(
    "args",
    (
        ["worktree", "remove", "other"],
        ["worktree", "prune"],
        ["stash", "drop"],
        ["tag", "-d", "archive/example"],
        ["branch", "-D", "example"],
    ),
)
def test_run_git_rejects_mutating_shapes_inside_mixed_command_families(
    repo: Path,
    args: list[str],
) -> None:
    with pytest.raises(ValueError):
        gitutil.run_git(args, cwd=repo)


def test_run_git_accepts_only_exact_worktree_list_shape(repo: Path) -> None:
    out, error = gitutil.run_git(["worktree", "list", "--porcelain"], cwd=repo)
    assert error is None
    assert out is not None and f"worktree {repo}" in out


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


def test_verified_origin_main_current_and_ancestor(repo_with_origin: tuple[Path, Path]) -> None:
    repo, _remote = repo_with_origin
    result = gitutil.verified_origin_main(repo)
    assert result["freshness"] == "CURRENT"
    assert result["head_contains_verified_main"] is True


def test_verified_origin_main_stale_when_remote_moved(
    repo_with_origin: tuple[Path, Path], tmp_path: Path
) -> None:
    repo, remote = repo_with_origin
    old_local = _git_out(repo, "rev-parse", "origin/main")
    new_remote = _advance_remote(tmp_path, remote)

    result = gitutil.verified_origin_main(repo)

    assert result["freshness"] == "STALE"
    assert result["local_sha"] == old_local
    assert result["remote_sha"] == new_remote
    # Never fetches/mutates the local remote-tracking ref.
    assert _git_out(repo, "rev-parse", "origin/main") == old_local


def test_verified_origin_main_unverified_when_remote_unreachable(
    repo_with_origin: tuple[Path, Path], tmp_path: Path
) -> None:
    repo, _remote = repo_with_origin
    _git(repo, "remote", "set-url", "origin", str(tmp_path / "missing.git"))

    result = gitutil.verified_origin_main(repo)

    assert result["freshness"] == "UNVERIFIED"


def test_verified_origin_main_head_not_containing_current_remote(
    repo_with_origin: tuple[Path, Path], tmp_path: Path
) -> None:
    repo, remote = repo_with_origin
    _git(repo, "switch", "-q", "-c", "research/old-base")
    _advance_remote(tmp_path, remote)
    _git(repo, "fetch", "-q", "origin", "main")

    result = gitutil.verified_origin_main(repo)

    assert result["freshness"] == "CURRENT"
    assert result["head_contains_verified_main"] is False


def test_worktree_ownership_ok_on_clean_single_worktree(repo: Path) -> None:
    result = gitutil.worktree_ownership(repo)
    assert result["ok"] is True
    assert result["errors"] == []
    assert result["duplicate_branch_owners"] == []


def test_worktree_ownership_detects_detached_head(repo: Path) -> None:
    _git(repo, "checkout", "-q", "--detach")
    result = gitutil.worktree_ownership(repo)
    assert result["ok"] is False
    assert result["detached_head"] is True
    assert any("detached HEAD" in e for e in result["errors"])


def test_worktree_ownership_detects_duplicate_branch_owners(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = tmp_path / "stale-registration"
    monkeypatch.setattr(
        gitutil,
        "worktrees",
        lambda _root: [
            gitutil.Worktree(str(repo), _git_out(repo, "rev-parse", "HEAD"), "main", False, False, False),
            gitutil.Worktree(str(stale), _git_out(repo, "rev-parse", "HEAD"), "main", False, False, False),
        ],
    )
    result = gitutil.worktree_ownership(repo)
    assert result["ok"] is False
    assert result["duplicate_branch_owners"] == [
        {"branch": "main", "paths": sorted([str(repo.resolve()), str(stale.resolve())])}
    ]
