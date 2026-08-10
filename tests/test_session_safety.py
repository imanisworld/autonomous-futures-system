"""Tests for ops/session_safety.py -- session-start and precommit routines."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ops.session_safety import precommit_report, session_start_report


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "risk_rules.yaml").write_text(
        "strategy_permission_gate:\n"
        "  enabled: true\n"
        "  default_status: SHADOW_ONLY\n"
        "  strategy_status:\n"
        "    orb_breakout: PAPER_ELIGIBLE\n"
        "strategy:\n"
        "  enabled_concepts:\n"
        "    - orb_breakout\n"
        "instruments:\n"
        "  allowed:\n"
        "    - MNQ\n"
        "position_rules:\n"
        "  max_contracts_per_instrument:\n"
        "    MNQ: 6\n"
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def test_session_start_writes_checkpoint(git_repo: Path) -> None:
    report = session_start_report(repo_root=git_repo, log_dir="logs", check_prs=False)
    assert report["mode"] == "session-start"
    checkpoint_path = git_repo / "logs" / ".session_safety_state.json"
    assert checkpoint_path.exists()
    assert report["repo"]["branch"] == "main"


def test_session_start_reports_active_paper_lane(git_repo: Path) -> None:
    report = session_start_report(repo_root=git_repo, log_dir="logs", check_prs=False)
    assert report["strategy_lanes"]["active_paper_eligible_lanes"] == ["orb_breakout"]
    assert report["strategy_lanes"]["quantity_contract_caps"] == {"MNQ": 6}


def test_precommit_without_checkpoint_fails_closed(git_repo: Path) -> None:
    report = precommit_report(repo_root=git_repo, log_dir="logs")
    assert report["verdict"] == "FAIL CLOSED"
    assert "no session-start checkpoint found" in report["reasons"][0]


def test_precommit_after_session_start_passes_when_nothing_changed(git_repo: Path) -> None:
    session_start_report(repo_root=git_repo, log_dir="logs", check_prs=False)
    report = precommit_report(repo_root=git_repo, log_dir="logs")
    assert report["verdict"] == "PASS (read-only)"
    assert report["reasons"] == []


def test_precommit_fails_closed_when_branch_changed(git_repo: Path) -> None:
    session_start_report(repo_root=git_repo, log_dir="logs", check_prs=False)
    _git(git_repo, "checkout", "-q", "-b", "other-branch")
    report = precommit_report(repo_root=git_repo, log_dir="logs")
    assert report["verdict"] == "FAIL CLOSED"
    assert any("branch differs from session-start" in reason for reason in report["reasons"])


def test_precommit_never_mutates_repo(git_repo: Path) -> None:
    session_start_report(repo_root=git_repo, log_dir="logs", check_prs=False)
    before_branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=str(git_repo), capture_output=True, text=True
    ).stdout.strip()
    before_status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(git_repo), capture_output=True, text=True
    ).stdout
    precommit_report(repo_root=git_repo, log_dir="logs")
    after_branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=str(git_repo), capture_output=True, text=True
    ).stdout.strip()
    after_status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(git_repo), capture_output=True, text=True
    ).stdout
    assert before_branch == after_branch
    # The only permitted filesystem change from session_start is the checkpoint
    # file itself (already written before this snapshot); precommit adds nothing.
    assert before_status == after_status
