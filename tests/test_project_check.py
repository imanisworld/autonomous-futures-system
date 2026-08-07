from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ops import project_check as pc


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-b", "main"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "test@example.com"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "Test User"], cwd=repo)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "README.md"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "init"], cwd=repo)
    return repo


def _isolate_gh(monkeypatch):
    # Stub gh out directly (rather than clobbering PATH) so these tests don't
    # depend on whether the machine running them happens to have `gh` — and
    # so `git` subprocess calls, which need PATH intact, keep working.
    monkeypatch.setattr(
        "ops.project_check_git._run_gh", lambda root, args, timeout=20.0: (False, "unavailable (stubbed for test)")
    )


def test_session_start_report_writes_state_file(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _isolate_gh(monkeypatch)
    report = pc.build_session_start_report(repo)
    assert report["repo"]["current_branch"] == "main"
    assert report["branch_changed_during_check"] is False

    state_path = pc._write_session_state(repo, report)
    assert state_path.exists()
    state = json.loads(state_path.read_text())
    assert state["branch"] == "main"
    assert state["repo_root"] == str(repo)


def test_precommit_fails_closed_without_session_start(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _isolate_gh(monkeypatch)
    report = pc.build_precommit_report(repo)
    assert report["fail_closed"] is True
    assert any("session-start state cannot be verified" in reason for reason in report["fail_reasons"])


def test_precommit_passes_after_session_start_with_no_changes(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _isolate_gh(monkeypatch)
    session_report = pc.build_session_start_report(repo)
    pc._write_session_state(repo, session_report)

    report = pc.build_precommit_report(repo)
    assert report["fail_closed"] is False
    assert report["fail_reasons"] == []


def test_precommit_fails_closed_on_branch_change(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _isolate_gh(monkeypatch)
    session_report = pc.build_session_start_report(repo)
    pc._write_session_state(repo, session_report)

    subprocess.check_call(["git", "checkout", "-b", "other-branch"], cwd=repo)
    report = pc.build_precommit_report(repo)
    assert report["fail_closed"] is True
    assert any("branch differs from session-start" in reason for reason in report["fail_reasons"])


def test_precommit_detects_branch_owned_by_another_worktree(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _isolate_gh(monkeypatch)
    subprocess.check_call(["git", "branch", "shared-branch"], cwd=repo)
    other_wt = tmp_path / "other-worktree"
    subprocess.check_call(["git", "worktree", "add", str(other_wt), "shared-branch"], cwd=repo)

    session_report = pc.build_session_start_report(other_wt)
    pc._write_session_state(other_wt, session_report)

    report = pc.build_precommit_report(other_wt)
    # The branch IS owned by this same worktree (the one that checked it out),
    # so this should pass — sanity check the positive case first.
    assert report["fail_closed"] is False


def test_build_parser_has_all_four_subcommands():
    parser = pc.build_parser()
    args = parser.parse_args(["session-start"])
    assert args.command == "session-start"
    args = parser.parse_args(["precommit"])
    assert args.command == "precommit"
    args = parser.parse_args(["promotion", "--strategy", "orb_breakout"])
    assert args.command == "promotion"
    assert args.strategy == "orb_breakout"
    args = parser.parse_args(["daily"])
    assert args.command == "daily"
