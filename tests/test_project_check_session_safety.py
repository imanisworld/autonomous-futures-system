from __future__ import annotations

import subprocess
from pathlib import Path

from ops.session_safety import (
    _branch_sync_status,
    _dirty_staged_untracked,
    build_precommit_report,
    build_session_start_report,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_repo_with_origin(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    subprocess.check_call(["git", "init", "--bare", "-b", "main", str(origin)])

    repo = tmp_path / "repo"
    subprocess.check_call(["git", "clone", str(origin), str(repo)])
    subprocess.check_call(["git", "config", "user.email", "test@example.com"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "Test User"], cwd=repo)
    (repo / "risk_rules.yaml").write_text(
        "trading_mode:\n  live_trading_enabled: false\n  paper_mode: true\n"
        "instruments:\n  allowed: [MNQ]\n"
        "strategy:\n  enabled_concepts: [orb_breakout]\n  disabled_concepts_per_instrument: {}\n",
        encoding="utf-8",
    )
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "risk_rules.yaml", "README.md"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "init"], cwd=repo)
    subprocess.check_call(["git", "push", "origin", "main"], cwd=repo)
    return repo


def test_dirty_staged_untracked_classification(tmp_path):
    repo = _init_repo_with_origin(tmp_path)
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    (repo / "new_untracked.txt").write_text("x\n", encoding="utf-8")
    (repo / "staged.txt").write_text("y\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "staged.txt"], cwd=repo)

    result = _dirty_staged_untracked(repo)
    assert "README.md" in result["dirty_tracked"]
    assert "staged.txt" in result["staged"]
    assert "new_untracked.txt" in result["untracked"]
    assert "README.md" not in result["untracked"]


def test_branch_sync_status_in_sync_then_ahead(tmp_path):
    repo = _init_repo_with_origin(tmp_path)
    status, detail = _branch_sync_status(repo, "main", "origin/main")
    assert status == "IN_SYNC"
    assert detail["ahead"] == 0 and detail["behind"] == 0

    (repo / "new_file.txt").write_text("x\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "new_file.txt"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "local only"], cwd=repo)

    status, detail = _branch_sync_status(repo, "main", "origin/main")
    assert status == "AHEAD"
    assert detail["ahead"] == 1 and detail["behind"] == 0


def test_branch_sync_status_unknown_when_ref_missing(tmp_path):
    repo = _init_repo_with_origin(tmp_path)
    status, detail = _branch_sync_status(repo, "does-not-exist", "origin/main")
    assert status == "UNKNOWN"


def test_build_session_start_report_smoke(tmp_path):
    repo = _init_repo_with_origin(tmp_path)
    report = build_session_start_report(repo_root=repo)

    assert report["mode"] == "session-start"
    assert report["read_only"] is True
    assert report["repo_root"] == str(repo)
    assert report["branch"] == "main"
    assert report["local_main_status"] == "IN_SYNC"
    assert report["dirty_tracked_files"] == []
    assert report["staged_files"] == []
    assert report["untracked_files"] == []
    assert report["branch_changed_during_check"] is False
    snapshot = report["runtime_snapshot"]
    assert snapshot["active_paper_forward_strategy_lanes"]["active_lanes_by_instrument"] == {
        "MNQ": ["orb_breakout"]
    }


def test_precommit_passes_when_state_unchanged(tmp_path):
    repo = _init_repo_with_origin(tmp_path)
    baseline = build_session_start_report(repo_root=repo)
    report = build_precommit_report(baseline=baseline, repo_root=repo)
    assert report["ok"] is True
    assert report["fail_closed"] is False
    assert report["failures"] == []


def test_precommit_fails_closed_on_branch_change(tmp_path):
    repo = _init_repo_with_origin(tmp_path)
    baseline = build_session_start_report(repo_root=repo)
    subprocess.check_call(["git", "checkout", "-b", "other-branch"], cwd=repo)

    report = build_precommit_report(baseline=baseline, repo_root=repo)
    assert report["ok"] is False
    codes = {f["code"] for f in report["failures"]}
    assert "branch_changed" in codes


def test_precommit_fails_closed_on_unexpected_files(tmp_path):
    repo = _init_repo_with_origin(tmp_path)
    baseline = build_session_start_report(repo_root=repo)
    (repo / "surprise.txt").write_text("uh oh\n", encoding="utf-8")

    report = build_precommit_report(
        baseline=baseline, repo_root=repo, expected_files=["only_this_file.txt"]
    )
    assert report["ok"] is False
    codes = {f["code"] for f in report["failures"]}
    assert "unexpected_files_changed" in codes


def test_precommit_never_mutates_git_state(tmp_path):
    repo = _init_repo_with_origin(tmp_path)
    baseline = build_session_start_report(repo_root=repo)
    head_before = _git(repo, "rev-parse", "HEAD")
    branch_before = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    status_before = _git(repo, "status", "--porcelain")

    build_precommit_report(baseline=baseline, repo_root=repo)

    assert _git(repo, "rev-parse", "HEAD") == head_before
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == branch_before
    assert _git(repo, "status", "--porcelain") == status_before
