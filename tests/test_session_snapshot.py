"""Tests for ops.session_snapshot — session-start report and precommit guard."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ops import session_snapshot as ss


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(root), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "initial commit")
    return root


def test_session_start_report_persists_state(repo: Path) -> None:
    report = ss.session_start_report(repo_root=repo, do_fetch=False)
    assert report["ok"] is True
    assert report["branch"] == "main"
    assert report["session_state_persisted"] is True
    state_path = Path(report["session_state_path"])
    assert state_path.exists()
    assert "main" in state_path.read_text(encoding="utf-8")


def test_session_start_report_never_mutates_git_state(repo: Path) -> None:
    before_branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    before_status = _git(repo, "status", "--porcelain")
    ss.session_start_report(repo_root=repo, do_fetch=False)
    after_branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    # Only the untracked, gitignored-by-nature .git/ops_session_check.json
    # file should differ; tracked-file status must be unchanged.
    after_status = _git(repo, "status", "--porcelain")
    assert before_branch == after_branch
    assert before_status == after_status == ""


def test_precommit_fails_closed_without_prior_session_start(repo: Path) -> None:
    report = ss.precommit_report(repo_root=repo)
    assert report["ok"] is False
    assert report["status"] == "FAIL_CLOSED"
    assert any("session-start state could not be verified" in reason for reason in report["fail_reasons"])


def test_precommit_passes_after_session_start_with_no_drift(repo: Path) -> None:
    ss.session_start_report(repo_root=repo, do_fetch=False)
    report = ss.precommit_report(repo_root=repo)
    assert report["ok"] is True
    assert report["status"] == "PASS"
    assert report["fail_reasons"] == []


def test_precommit_fails_closed_on_branch_change(repo: Path) -> None:
    ss.session_start_report(repo_root=repo, do_fetch=False)
    _git(repo, "checkout", "-q", "-b", "some-other-branch")
    report = ss.precommit_report(repo_root=repo)
    assert report["ok"] is False
    assert any("branch differs from session-start branch" in reason for reason in report["fail_reasons"])


def test_precommit_is_read_only(repo: Path) -> None:
    ss.session_start_report(repo_root=repo, do_fetch=False)
    before_branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    before_head = _git(repo, "rev-parse", "HEAD")
    ss.precommit_report(repo_root=repo)
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == before_branch
    assert _git(repo, "rev-parse", "HEAD") == before_head


def test_runtime_snapshot_reports_unknown_without_release_manifest(repo: Path) -> None:
    snapshot = ss.runtime_snapshot(repo)
    assert snapshot["deployed_release"]["release_manifest_found"] is False
    assert snapshot["deployed_release"]["intended_release_sha"] == "UNKNOWN"
