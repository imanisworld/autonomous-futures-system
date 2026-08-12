"""Tests for ops.project_check (session-safety, promotion, daily/trade-chain routines)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ops.project_check import (
    _relationship,
    _strategy_inventory_row,
    _trade_chain_report,
    evidence_preservation_report,
    local_only_branches,
    precommit_report,
    working_tree_status,
)


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    (repo / "a.txt").write_text("hello\n", encoding="utf-8")
    _git("add", "a.txt", cwd=repo)
    _git("commit", "-q", "-m", "initial", cwd=repo)
    return repo


# ─── pure helpers ───────────────────────────────────────────────────────────

def test_relationship_in_sync():
    assert _relationship(0, 0) == "IN_SYNC"


def test_relationship_ahead():
    assert _relationship(2, 0) == "AHEAD"


def test_relationship_behind():
    assert _relationship(0, 3) == "BEHIND"


def test_relationship_diverged():
    assert _relationship(1, 1) == "DIVERGED"


def test_relationship_unknown_when_missing_data():
    assert _relationship(None, 3) == "UNKNOWN"


def test_local_only_branches():
    local = ["main", "feature-a", "feature-b"]
    remote = ["origin/main", "origin/feature-a"]
    assert local_only_branches(None, local, remote) == ["feature-b"]


# ─── git-backed helpers (real temp repo, read-only operations only) ────────

def test_working_tree_status_classifies_staged_dirty_untracked(git_repo: Path):
    (git_repo / "a.txt").write_text("changed\n", encoding="utf-8")
    (git_repo / "staged.txt").write_text("new\n", encoding="utf-8")
    _git("add", "staged.txt", cwd=git_repo)
    (git_repo / "untracked.txt").write_text("scratch\n", encoding="utf-8")

    status = working_tree_status(git_repo)

    assert "a.txt" in status["dirty"]
    assert "staged.txt" in status["staged"]
    assert "untracked.txt" in status["untracked"]
    assert "a.txt" not in status["untracked"]


def test_evidence_preservation_flags_unique_branch_without_archive_tag(git_repo: Path):
    _git("checkout", "-q", "-b", "research/no-archive", cwd=git_repo)
    (git_repo / "b.txt").write_text("unique work\n", encoding="utf-8")
    _git("add", "b.txt", cwd=git_repo)
    _git("commit", "-q", "-m", "unique commit", cwd=git_repo)
    _git("checkout", "-q", "main", cwd=git_repo)
    # No origin remote in this throwaway repo: point "origin/main" at main so
    # unique_commits_vs_main has something to diff against, mirroring a repo
    # with a real remote-tracking ref.
    _git("update-ref", "refs/remotes/origin/main", "main", cwd=git_repo)

    report = evidence_preservation_report(git_repo)
    by_branch = {item["branch"]: item for item in report}

    assert by_branch["research/no-archive"]["unique_commits_vs_origin_main"] == 1
    assert by_branch["research/no-archive"]["blocker"] is True


def test_evidence_preservation_respects_existing_archive_tag(git_repo: Path):
    _git("checkout", "-q", "-b", "research/archived", cwd=git_repo)
    (git_repo / "c.txt").write_text("preserved work\n", encoding="utf-8")
    _git("add", "c.txt", cwd=git_repo)
    _git("commit", "-q", "-m", "preserved commit", cwd=git_repo)
    _git("tag", "archive/research-archived-2026-01-01", cwd=git_repo)
    _git("checkout", "-q", "main", cwd=git_repo)
    _git("update-ref", "refs/remotes/origin/main", "main", cwd=git_repo)

    report = evidence_preservation_report(git_repo)
    by_branch = {item["branch"]: item for item in report}

    assert by_branch["research/archived"]["blocker"] is False
    assert by_branch["research/archived"]["archive_tag"] == "archive/research-archived-2026-01-01"


# ─── precommit fail-closed logic (session-state comparison is pure once mocked) ──

def test_precommit_fails_closed_with_no_session_state(monkeypatch):
    monkeypatch.setattr("ops.project_check.load_session_state", lambda: None)
    monkeypatch.setattr(
        "ops.project_check.collect_repo_state",
        lambda: {
            "repo_root": "/tmp/x", "current_branch": "b", "head_sha": "abc",
            "current_worktree": "/tmp/x", "worktrees": [], "upstream": "UNKNOWN",
            "dirty_files": [], "staged_files": [], "untracked_files": [],
        },
    )
    report = precommit_report()
    assert report["ok"] is False
    assert any("session-start state cannot be verified" in reason for reason in report["fail_closed_reasons"])


def test_precommit_fails_closed_on_branch_mismatch(monkeypatch):
    monkeypatch.setattr(
        "ops.project_check.load_session_state",
        lambda: {"branch": "original-branch", "worktree": "/tmp/x", "captured_at": "t0"},
    )
    monkeypatch.setattr(
        "ops.project_check.collect_repo_state",
        lambda: {
            "repo_root": "/tmp/x", "current_branch": "different-branch", "head_sha": "abc",
            "current_worktree": "/tmp/x", "worktrees": [], "upstream": "UNKNOWN",
            "dirty_files": [], "staged_files": [], "untracked_files": [],
        },
    )
    report = precommit_report()
    assert report["ok"] is False
    assert any("branch differs" in reason for reason in report["fail_closed_reasons"])


def test_precommit_passes_when_state_matches(monkeypatch):
    monkeypatch.setattr(
        "ops.project_check.load_session_state",
        lambda: {"branch": "same-branch", "worktree": "/tmp/x", "captured_at": "t0"},
    )
    monkeypatch.setattr(
        "ops.project_check.collect_repo_state",
        lambda: {
            "repo_root": "/tmp/x", "current_branch": "same-branch", "head_sha": "abc",
            "current_worktree": "/tmp/x", "worktrees": [], "upstream": "UNKNOWN",
            "dirty_files": [], "staged_files": [], "untracked_files": [],
        },
    )
    report = precommit_report()
    assert report["ok"] is True
    assert report["fail_closed_reasons"] == []


# ─── strategy inventory parsing ────────────────────────────────────────────

def test_strategy_inventory_row_finds_known_strategy():
    row = _strategy_inventory_row("orb_breakout")
    assert row is not None
    assert "ORB Breakout" in row["row_label"]
    assert row["verdict"]


def test_strategy_inventory_row_returns_none_for_unknown_strategy():
    assert _strategy_inventory_row("totally_made_up_strategy_xyz") is None


# ─── daily trade-chain integrity accounting ────────────────────────────────

def _write_journal(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_trade_chain_report_clean_pass(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("ops.project_check.DEFAULT_JOURNAL_DIR", tmp_path)
    _write_journal(tmp_path / "journal_2026-08-11.jsonl", [
        {"ts": "2026-08-11T14:00:00Z", "type": "DECISION", "decision": "TRADE",
         "instrument": "MNQ", "setup": {"strategy": "orb_breakout", "direction": "LONG"}},
        {"ts": "2026-08-11T14:05:00Z", "type": "OUTCOME", "instrument": "MNQ",
         "outcome": {"result": "WIN", "exit_reason": "target_hit", "pnl_dollars": 25.0}},
    ])
    report = _trade_chain_report("2026-08-11", "2026-08-11")
    assert report["attempts"] == 1
    assert report["fills"] == 1
    assert report["orphans"] == 0
    assert report["accounting_identity_holds"] is True
    assert report["pass"] is True


def test_trade_chain_report_flags_orphan_outcome(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("ops.project_check.DEFAULT_JOURNAL_DIR", tmp_path)
    _write_journal(tmp_path / "journal_2026-08-11.jsonl", [
        {"ts": "2026-08-11T14:05:00Z", "type": "OUTCOME", "instrument": "MNQ",
         "outcome": {"result": "WIN", "exit_reason": "target_hit", "pnl_dollars": 25.0}},
    ])
    report = _trade_chain_report("2026-08-11", "2026-08-11")
    assert report["orphans"] == 1
    assert report["pass"] is False


def test_trade_chain_report_counts_legitimately_open_position(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("ops.project_check.DEFAULT_JOURNAL_DIR", tmp_path)
    _write_journal(tmp_path / "journal_2026-08-11.jsonl", [
        {"ts": "2026-08-11T14:00:00Z", "type": "DECISION", "decision": "TRADE",
         "instrument": "MNQ", "setup": {"strategy": "orb_breakout", "direction": "LONG"}},
    ])
    report = _trade_chain_report("2026-08-11", "2026-08-11")
    assert report["attempts"] == 1
    assert report["legitimately_open"] == 1
    assert report["accounting_identity_holds"] is True
