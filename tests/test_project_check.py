"""Focused tests for the manual, read-only project ownership preflight."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ops.project_check import (
    _git,
    current_worktree_evidence,
    duplicate_branch_owners,
    origin_main_assumption,
    project_check_report,
)


def _run_git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def repo_with_origin(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "origin.git"
    _run_git(tmp_path, "init", "-q", "--bare", str(remote))

    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "-q", "-b", "main")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    (repo / "tracked.txt").write_text("initial\n", encoding="utf-8")
    _run_git(repo, "add", "tracked.txt")
    _run_git(repo, "commit", "-q", "-m", "initial")
    _run_git(repo, "remote", "add", "origin", str(remote))
    _run_git(repo, "push", "-q", "-u", "origin", "main")
    return repo, remote


def _advance_remote(tmp_path: Path, remote: Path) -> str:
    publisher = tmp_path / "publisher"
    _run_git(tmp_path, "clone", "-q", str(remote), str(publisher))
    _run_git(publisher, "config", "user.email", "publisher@example.com")
    _run_git(publisher, "config", "user.name", "Publisher")
    (publisher / "remote.txt").write_text("new remote work\n", encoding="utf-8")
    _run_git(publisher, "add", "remote.txt")
    _run_git(publisher, "commit", "-q", "-m", "advance main")
    _run_git(publisher, "push", "-q", "origin", "main")
    return _run_git(publisher, "rev-parse", "HEAD")


def test_current_worktree_evidence_finds_staged_and_untracked_only(
    repo_with_origin: tuple[Path, Path],
) -> None:
    repo, _remote = repo_with_origin
    (repo / "tracked.txt").write_text("unstaged\n", encoding="utf-8")
    (repo / "staged evidence.txt").write_text("staged\n", encoding="utf-8")
    (repo / "untracked evidence.txt").write_text("untracked\n", encoding="utf-8")
    _run_git(repo, "add", "staged evidence.txt")

    evidence = current_worktree_evidence(repo)

    assert evidence == {
        "ok": True,
        "staged": ["staged evidence.txt"],
        "untracked": ["untracked evidence.txt"],
    }


def test_current_worktree_evidence_does_not_scan_another_worktree(
    repo_with_origin: tuple[Path, Path], tmp_path: Path
) -> None:
    repo, _remote = repo_with_origin
    other = tmp_path / "other-worktree"
    _run_git(repo, "worktree", "add", "-q", "-b", "other", str(other))
    (other / "other-untracked.txt").write_text("belongs elsewhere\n", encoding="utf-8")

    evidence = current_worktree_evidence(repo)

    assert evidence["staged"] == []
    assert evidence["untracked"] == []


def test_duplicate_branch_owners_detects_prunable_and_live_registrations(
    tmp_path: Path,
) -> None:
    current = tmp_path / "current"
    stale = tmp_path / "stale"
    duplicates = duplicate_branch_owners(
        [
            {"path": str(current), "branch": "research/lane"},
            {
                "path": str(stale),
                "branch": "research/lane",
                "prunable": True,
            },
            {"path": str(tmp_path / "other"), "branch": "research/other"},
        ]
    )

    assert duplicates == [
        {
            "branch": "research/lane",
            "paths": sorted([str(current.resolve()), str(stale.resolve())]),
            "includes_prunable_registration": True,
        }
    ]


def test_report_blocks_duplicate_branch_ownership(
    repo_with_origin: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _remote = repo_with_origin
    duplicate = tmp_path / "stale-registration"
    monkeypatch.setattr(
        "ops.project_check.registered_worktrees",
        lambda _root: {
            "ok": True,
            "worktrees": [
                {"path": str(repo), "branch": "main", "head": "abc"},
                {
                    "path": str(duplicate),
                    "branch": "main",
                    "head": "abc",
                    "prunable": True,
                },
            ],
        },
    )

    report = project_check_report("research", root=repo)

    assert report["ok"] is False
    assert report["worktree_ownership"]["duplicates"][0]["branch"] == "main"
    assert any("multiple worktrees" in reason for reason in report["blockers"])


def test_origin_main_assumption_detects_remote_advance_without_fetch(
    repo_with_origin: tuple[Path, Path], tmp_path: Path
) -> None:
    repo, remote = repo_with_origin
    old_local = _run_git(repo, "rev-parse", "origin/main")
    new_remote = _advance_remote(tmp_path, remote)

    state = origin_main_assumption(repo)

    assert state["freshness"] == "STALE"
    assert state["local_sha"] == old_local
    assert state["remote_sha"] == new_remote
    assert state["head_contains_verified_base"] is None
    assert _run_git(repo, "rev-parse", "origin/main") == old_local


def test_report_blocks_when_head_does_not_contain_verified_origin_main(
    repo_with_origin: tuple[Path, Path], tmp_path: Path
) -> None:
    repo, remote = repo_with_origin
    _run_git(repo, "switch", "-q", "-c", "research/old-base")
    _advance_remote(tmp_path, remote)
    _run_git(repo, "fetch", "-q", "origin", "main")

    report = project_check_report("research", root=repo)

    assert report["origin_main"]["freshness"] == "CURRENT"
    assert report["origin_main"]["head_contains_verified_base"] is False
    assert report["ok"] is False
    assert "does not contain" in " ".join(report["blockers"])


def test_clean_current_repo_passes_without_writing_bookkeeping(
    repo_with_origin: tuple[Path, Path],
) -> None:
    repo, _remote = repo_with_origin
    before_status = _run_git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    before_head = _run_git(repo, "rev-parse", "HEAD")
    before_origin_main = _run_git(repo, "rev-parse", "origin/main")

    report = project_check_report("promotion", root=repo)

    assert report["ok"] is True
    assert report["bookkeeping_writes"] == []
    assert _run_git(repo, "status", "--porcelain=v1", "--untracked-files=all") == before_status
    assert _run_git(repo, "rev-parse", "HEAD") == before_head
    assert _run_git(repo, "rev-parse", "origin/main") == before_origin_main


def test_report_blocks_existing_staged_and_untracked_evidence(
    repo_with_origin: tuple[Path, Path],
) -> None:
    repo, _remote = repo_with_origin
    (repo / "staged.txt").write_text("staged\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    _run_git(repo, "add", "staged.txt")

    report = project_check_report("research", root=repo)

    assert report["ok"] is False
    assert any("staged evidence" in reason for reason in report["blockers"])
    assert any("untracked evidence" in reason for reason in report["blockers"])


def test_git_helper_rejects_mutating_subcommands(repo_with_origin: tuple[Path, Path]) -> None:
    repo, _remote = repo_with_origin
    with pytest.raises(ValueError, match="non-read-only"):
        _git(repo, "fetch", "origin")
