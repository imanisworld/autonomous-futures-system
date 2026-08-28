from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ops.project_check import gitutil
from ops.project_check.preflight import verified_origin_main, worktree_ownership


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def repo_with_origin(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "tracked.txt").write_text("initial\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-q", "-m", "initial")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "-u", "origin", "main")
    return repo, remote


def _advance_remote(tmp_path: Path, remote: Path) -> str:
    publisher = tmp_path / "publisher"
    _git(tmp_path, "clone", "-q", str(remote), str(publisher))
    _git(publisher, "config", "user.email", "publisher@example.com")
    _git(publisher, "config", "user.name", "Publisher")
    (publisher / "remote.txt").write_text("new remote work\n", encoding="utf-8")
    _git(publisher, "add", "remote.txt")
    _git(publisher, "commit", "-q", "-m", "advance main")
    _git(publisher, "push", "-q", "origin", "main")
    return _git(publisher, "rev-parse", "HEAD")


def test_verified_origin_main_current_and_never_writes(repo_with_origin: tuple[Path, Path]) -> None:
    repo, _remote = repo_with_origin
    before_status = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    before_head = _git(repo, "rev-parse", "HEAD")
    before_origin_main = _git(repo, "rev-parse", "origin/main")

    result = verified_origin_main(repo)

    assert result["freshness"] == "CURRENT"
    assert result["head_contains_verified_main"] is True
    assert _git(repo, "status", "--porcelain=v1", "--untracked-files=all") == before_status
    assert _git(repo, "rev-parse", "HEAD") == before_head
    assert _git(repo, "rev-parse", "origin/main") == before_origin_main


def test_verified_origin_main_stale_local(
    repo_with_origin: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    repo, remote = repo_with_origin
    old_local = _git(repo, "rev-parse", "origin/main")
    new_remote = _advance_remote(tmp_path, remote)

    result = verified_origin_main(repo)

    assert result["freshness"] == "STALE"
    assert result["local_sha"] == old_local
    assert result["remote_sha"] == new_remote
    assert _git(repo, "rev-parse", "origin/main") == old_local


def test_verified_origin_main_unreachable_remote(
    repo_with_origin: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    repo, _remote = repo_with_origin
    _git(repo, "remote", "set-url", "origin", str(tmp_path / "missing.git"))

    result = verified_origin_main(repo)

    assert result["freshness"] == "UNVERIFIED"


def test_verified_origin_main_head_not_containing_verified_main(
    repo_with_origin: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    repo, remote = repo_with_origin
    _git(repo, "switch", "-q", "-c", "research/old-base")
    _advance_remote(tmp_path, remote)
    _git(repo, "fetch", "-q", "origin", "main")

    result = verified_origin_main(repo)

    assert result["freshness"] == "CURRENT"
    assert result["head_contains_verified_main"] is False


def test_worktree_ownership_ok(repo_with_origin: tuple[Path, Path]) -> None:
    repo, _remote = repo_with_origin
    result = worktree_ownership(repo)
    assert result["ok"] is True
    assert result["detached_head"] is False
    assert result["duplicate_branch_owners"] == []


def test_worktree_ownership_detached_head_fails(repo_with_origin: tuple[Path, Path]) -> None:
    repo, _remote = repo_with_origin
    _git(repo, "checkout", "-q", "--detach")

    result = worktree_ownership(repo)

    assert result["ok"] is False
    assert result["detached_head"] is True
    assert any("detached HEAD" in e for e in result["errors"])


def test_worktree_ownership_duplicate_registration_fails(
    repo_with_origin: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _remote = repo_with_origin
    stale = tmp_path / "stale-registration"
    monkeypatch.setattr(
        gitutil,
        "worktrees",
        lambda _root: [
            gitutil.Worktree(str(repo), _git(repo, "rev-parse", "HEAD"), "main", False, False, False),
            gitutil.Worktree(str(stale), _git(repo, "rev-parse", "HEAD"), "main", False, False, False),
        ],
    )

    result = worktree_ownership(repo)

    assert result["ok"] is False
    assert len(result["duplicate_branch_owners"]) == 1
