from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ops.project_check import gitutil
from ops.project_check.preflight import build_ownership_preflight_report


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


@pytest.mark.parametrize("purpose", ("research", "promotion"))
def test_clean_current_state_passes_without_writes(
    repo_with_origin: tuple[Path, Path],
    purpose: str,
) -> None:
    repo, _remote = repo_with_origin
    before_status = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    before_head = _git(repo, "rev-parse", "HEAD")
    before_origin_main = _git(repo, "rev-parse", "origin/main")

    report = build_ownership_preflight_report(purpose, cwd=repo)

    assert report["ok"] is True
    assert report["bookkeeping_writes"] == []
    assert _git(repo, "status", "--porcelain=v1", "--untracked-files=all") == before_status
    assert _git(repo, "rev-parse", "HEAD") == before_head
    assert _git(repo, "rev-parse", "origin/main") == before_origin_main


@pytest.mark.parametrize("purpose", ("research", "promotion"))
def test_staged_and_untracked_evidence_fail_closed(
    repo_with_origin: tuple[Path, Path],
    purpose: str,
) -> None:
    repo, _remote = repo_with_origin
    (repo / "staged.txt").write_text("staged\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    _git(repo, "add", "staged.txt")

    report = build_ownership_preflight_report(purpose, cwd=repo)

    assert report["ok"] is False
    assert any("staged evidence" in item for item in report["blockers"])
    assert any("untracked evidence" in item for item in report["blockers"])


@pytest.mark.parametrize("purpose", ("research", "promotion"))
def test_duplicate_branch_ownership_fails_closed(
    repo_with_origin: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    purpose: str,
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

    report = build_ownership_preflight_report(purpose, cwd=repo)

    assert report["ok"] is False
    assert any("multiple worktrees" in item for item in report["blockers"])


@pytest.mark.parametrize("purpose", ("research", "promotion"))
def test_detached_head_fails_closed(
    repo_with_origin: tuple[Path, Path],
    purpose: str,
) -> None:
    repo, _remote = repo_with_origin
    _git(repo, "checkout", "-q", "--detach")

    report = build_ownership_preflight_report(purpose, cwd=repo)

    assert report["ok"] is False
    assert report["worktree_ownership"]["detached_head"] is True
    assert any("detached HEAD" in item for item in report["blockers"])


@pytest.mark.parametrize("purpose", ("research", "promotion"))
def test_stale_local_origin_main_fails_closed(
    repo_with_origin: tuple[Path, Path],
    tmp_path: Path,
    purpose: str,
) -> None:
    repo, remote = repo_with_origin
    old_local = _git(repo, "rev-parse", "origin/main")
    new_remote = _advance_remote(tmp_path, remote)

    report = build_ownership_preflight_report(purpose, cwd=repo)

    assert report["ok"] is False
    assert report["origin_main"]["freshness"] == "STALE"
    assert report["origin_main"]["local_sha"] == old_local
    assert report["origin_main"]["remote_sha"] == new_remote
    assert _git(repo, "rev-parse", "origin/main") == old_local


@pytest.mark.parametrize("purpose", ("research", "promotion"))
def test_unreachable_remote_fails_closed(
    repo_with_origin: tuple[Path, Path],
    tmp_path: Path,
    purpose: str,
) -> None:
    repo, _remote = repo_with_origin
    _git(repo, "remote", "set-url", "origin", str(tmp_path / "missing.git"))

    report = build_ownership_preflight_report(purpose, cwd=repo)

    assert report["ok"] is False
    assert report["origin_main"]["freshness"] == "UNVERIFIED"
    assert any("UNVERIFIED" in item for item in report["blockers"])


@pytest.mark.parametrize("purpose", ("research", "promotion"))
def test_head_not_containing_verified_main_fails_closed(
    repo_with_origin: tuple[Path, Path],
    tmp_path: Path,
    purpose: str,
) -> None:
    repo, remote = repo_with_origin
    _git(repo, "switch", "-q", "-c", "research/old-base")
    _advance_remote(tmp_path, remote)
    _git(repo, "fetch", "-q", "origin", "main")

    report = build_ownership_preflight_report(purpose, cwd=repo)

    assert report["ok"] is False
    assert report["origin_main"]["freshness"] == "CURRENT"
    assert report["origin_main"]["head_contains_verified_main"] is False
    assert any("does not contain" in item for item in report["blockers"])
