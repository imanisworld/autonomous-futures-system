"""Tests for ops/project_check.py — session-start / precommit / daily.

Uses fresh temp git repos (never this checkout) so the tests are hermetic and
never touch real branches, worktrees, or tags.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from ops.project_check import (
    FAIL_CLOSED,
    OK,
    WARN,
    archive_tags,
    build_daily_report,
    build_precommit_report,
    build_session_start_report,
    evidence_preservation_report,
    read_session_start_state,
    write_session_start_state,
)


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    result.stdout = result.stdout.strip()
    return result


def _init_repo(path: Path, default_branch: str = "main") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _run(["init", "--initial-branch", default_branch, "."], cwd=path)
    _run(["config", "user.email", "test@example.com"], cwd=path)
    _run(["config", "user.name", "Test"], cwd=path)
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _run(["add", "README.md"], cwd=path)
    _run(["commit", "-m", "initial commit"], cwd=path)
    return path


def _commit_file(path: Path, name: str, content: str, message: str) -> None:
    (path / name).write_text(content, encoding="utf-8")
    _run(["add", name], cwd=path)
    _run(["commit", "-m", message], cwd=path)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    return _init_repo(tmp_path / "repo")


@pytest.fixture()
def repo_with_remote(tmp_path: Path) -> tuple[Path, Path]:
    """A repo with a real `origin` remote (a local bare repo) so ahead/behind,
    upstream tracking, and [gone] detection all work for real."""
    bare = tmp_path / "origin.git"
    bare.mkdir()
    _run(["init", "--bare", "--initial-branch", "main", "."], cwd=bare)

    work = _init_repo(tmp_path / "work")
    _run(["remote", "add", "origin", str(bare)], cwd=work)
    _run(["push", "-u", "origin", "main"], cwd=work)
    return work, bare


# --------------------------------------------------------------------------- session-start


def test_session_start_reports_repo_root_and_branch(repo: Path):
    report = build_session_start_report(cwd=str(repo), include_gh=False)
    assert report["ok"] is True
    assert report["repo_root"] == str(repo.resolve())
    assert report["current_branch"] == "main"
    assert report["detached_head"] is False
    assert report["head_sha"]
    assert report["dirty_tracked_files"] == []
    assert report["untracked_files"] == []
    assert report["branch_changed_during_check"] is False


def test_session_start_reports_dirty_and_untracked_files(repo: Path):
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    (repo / "scratch.txt").write_text("new\n", encoding="utf-8")
    report = build_session_start_report(cwd=str(repo), include_gh=False)
    assert report["dirty_tracked_files"] == ["README.md"]
    assert report["untracked_files"] == ["scratch.txt"]


def test_session_start_not_a_repo(tmp_path: Path):
    empty = tmp_path / "not_a_repo"
    empty.mkdir()
    report = build_session_start_report(cwd=str(empty), include_gh=False)
    assert report["ok"] is False
    assert "not a git repository" in report["error"]


def test_session_start_writes_and_reads_state(repo: Path):
    report = build_session_start_report(cwd=str(repo), include_gh=False)
    write_session_start_state(str(repo), report)
    baseline = read_session_start_state(str(repo))
    assert baseline is not None
    assert baseline["current_branch"] == "main"
    assert baseline["head_sha"] == report["head_sha"]
    # Never written into the tracked tree.
    status = _run(["status", "--porcelain"], cwd=repo).stdout
    assert "project_check" not in status


def test_session_start_detects_local_only_branch_as_no_deleted_remote(repo: Path):
    report = build_session_start_report(cwd=str(repo), include_gh=False)
    assert report["branches_with_deleted_remote"] == []


# --------------------------------------------------------------------------- precommit


def test_precommit_warns_without_baseline(repo: Path):
    report = build_precommit_report(cwd=str(repo))
    assert report["verdict"] == WARN
    assert report["session_start_baseline_present"] is False
    assert any("no session-start baseline" in r for r in report["reasons"])


def test_precommit_ok_when_nothing_changed(repo: Path):
    start = build_session_start_report(cwd=str(repo), include_gh=False)
    write_session_start_state(str(repo), start)
    report = build_precommit_report(cwd=str(repo))
    assert report["verdict"] == OK
    assert report["ok"] is True


def test_precommit_fails_closed_on_branch_change(repo: Path):
    start = build_session_start_report(cwd=str(repo), include_gh=False)
    write_session_start_state(str(repo), start)
    _run(["checkout", "-b", "other-branch"], cwd=repo)
    report = build_precommit_report(cwd=str(repo))
    assert report["verdict"] == FAIL_CLOSED
    assert report["ok"] is False
    assert any("differs from session-start branch" in r for r in report["reasons"])


def test_precommit_fails_closed_on_detached_head(repo: Path):
    start = build_session_start_report(cwd=str(repo), include_gh=False)
    write_session_start_state(str(repo), start)
    sha = start["head_sha"]
    _run(["checkout", sha], cwd=repo)
    report = build_precommit_report(cwd=str(repo))
    assert report["verdict"] == FAIL_CLOSED
    assert any("DETACHED HEAD" in r for r in report["reasons"])


def test_precommit_fails_closed_when_history_rewritten(repo: Path):
    _commit_file(repo, "a.txt", "a\n", "add a")
    start = build_session_start_report(cwd=str(repo), include_gh=False)
    write_session_start_state(str(repo), start)
    _run(["reset", "--hard", "HEAD~1"], cwd=repo)
    _commit_file(repo, "b.txt", "b\n", "add b instead")
    report = build_precommit_report(cwd=str(repo))
    assert report["verdict"] == FAIL_CLOSED
    assert any("not a descendant" in r for r in report["reasons"])


def test_precommit_reports_changed_and_staged_files(repo: Path):
    start = build_session_start_report(cwd=str(repo), include_gh=False)
    write_session_start_state(str(repo), start)
    (repo / "README.md").write_text("edited\n", encoding="utf-8")
    _run(["add", "README.md"], cwd=repo)
    (repo / "untracked.txt").write_text("x\n", encoding="utf-8")
    report = build_precommit_report(cwd=str(repo))
    assert "README.md" in report["staged_files"]
    assert "untracked.txt" in report["untracked_files"]
    assert report["verdict"] == OK  # editing files isn't itself an anomaly


def test_precommit_never_mutates_repo_state(repo: Path):
    start = build_session_start_report(cwd=str(repo), include_gh=False)
    write_session_start_state(str(repo), start)
    before_sha = _run(["rev-parse", "HEAD"], cwd=repo).stdout
    before_branch = _run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo).stdout
    build_precommit_report(cwd=str(repo))
    after_sha = _run(["rev-parse", "HEAD"], cwd=repo).stdout
    after_branch = _run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo).stdout
    assert before_sha == after_sha
    assert before_branch == after_branch


# --------------------------------------------------------------------------- evidence preservation / daily


def test_evidence_preservation_flags_unmerged_branch_without_tag(repo: Path):
    _run(["checkout", "-b", "research/idea"], cwd=repo)
    _commit_file(repo, "idea.py", "x = 1\n", "unique research commit")
    _run(["checkout", "main"], cwd=repo)
    findings = evidence_preservation_report("main", "main", cwd=str(repo))
    by_branch = {f["branch"]: f for f in findings}
    assert "research/idea" in by_branch
    assert by_branch["research/idea"]["unique_commit_count"] == 1
    assert "idea.py" in by_branch["research/idea"]["unique_files_sample"]
    assert by_branch["research/idea"]["archive_tag"] is None
    assert "REVIEW" in by_branch["research/idea"]["classification"] or "BLOCKER" in by_branch["research/idea"]["classification"]


def test_evidence_preservation_recognizes_archive_tag(repo: Path):
    _run(["checkout", "-b", "research/tagged"], cwd=repo)
    _commit_file(repo, "tagged.py", "x = 1\n", "unique research commit")
    tip = _run(["rev-parse", "HEAD"], cwd=repo).stdout
    _run(["tag", "-a", "archive/research-tagged-2026-01-01", "-m", "archived", tip], cwd=repo)
    _run(["checkout", "main"], cwd=repo)
    findings = evidence_preservation_report("main", "main", cwd=str(repo))
    by_branch = {f["branch"]: f for f in findings}
    assert by_branch["research/tagged"]["archive_tag"] == "archive/research-tagged-2026-01-01"
    assert "OK" in by_branch["research/tagged"]["classification"]


def test_evidence_preservation_ignores_branch_identical_to_default(repo: Path):
    # A branch with zero unique commits is, by definition, already merged into
    # default (`git branch --no-merged` correctly excludes it) — nothing to flag.
    _run(["checkout", "-b", "empty-branch"], cwd=repo)
    _run(["checkout", "main"], cwd=repo)
    findings = evidence_preservation_report("main", "main", cwd=str(repo))
    by_branch = {f["branch"]: f for f in findings}
    assert "empty-branch" not in by_branch


def test_archive_tags_dereferences_annotated_tags(repo: Path):
    tip = _run(["rev-parse", "HEAD"], cwd=repo).stdout
    _run(["tag", "-a", "archive/foo-2026-01-01", "-m", "archived", tip], cwd=repo)
    tags = archive_tags(cwd=str(repo))
    assert tags["archive/foo-2026-01-01"] == tip


def test_daily_report_never_mutates_repo(repo_with_remote):
    work, _bare = repo_with_remote
    before_sha = _run(["rev-parse", "HEAD"], cwd=work).stdout
    report = build_daily_report(cwd=str(work), include_gh=False)
    after_sha = _run(["rev-parse", "HEAD"], cwd=work).stdout
    assert before_sha == after_sha
    assert report["ok"] is True
    assert report["default_branch"] == "main"
    assert report["local_main_vs_origin"]["status"] == "UP TO DATE"


def test_daily_report_flags_deleted_remote_branch(repo_with_remote):
    work, bare = repo_with_remote
    _run(["checkout", "-b", "feature/gone"], cwd=work)
    _commit_file(work, "gone.py", "x = 1\n", "feature work")
    _run(["push", "-u", "origin", "feature/gone"], cwd=work)
    _run(["checkout", "main"], cwd=work)
    # Simulate the PR being merged+deleted upstream, then locally pruned.
    _run(["push", "origin", "--delete", "feature/gone"], cwd=work)
    _run(["fetch", "--prune", "origin"], cwd=work)
    report = build_daily_report(cwd=str(work), include_gh=False)
    assert "feature/gone" in report["branches_with_deleted_remote"]
    blockers = {f["branch"]: f for f in report["evidence_preservation_blockers"]}
    assert "feature/gone" in blockers


def test_daily_report_no_blocker_once_archive_tag_added(repo_with_remote):
    work, bare = repo_with_remote
    _run(["checkout", "-b", "feature/preserved"], cwd=work)
    _commit_file(work, "preserved.py", "x = 1\n", "feature work")
    tip = _run(["rev-parse", "HEAD"], cwd=work).stdout
    _run(["push", "-u", "origin", "feature/preserved"], cwd=work)
    _run(["checkout", "main"], cwd=work)
    _run(["push", "origin", "--delete", "feature/preserved"], cwd=work)
    _run(["fetch", "--prune", "origin"], cwd=work)
    _run(["tag", "-a", "archive/feature-preserved-2026-01-01", "-m", "archived", tip], cwd=work)
    report = build_daily_report(cwd=str(work), include_gh=False)
    assert report["evidence_preservation_blockers"] == []


def test_daily_report_not_a_repo(tmp_path: Path):
    empty = tmp_path / "not_a_repo"
    empty.mkdir()
    report = build_daily_report(cwd=str(empty), include_gh=False)
    assert report["ok"] is False


# --------------------------------------------------------------------------- CLI smoke test


def test_cli_session_start_json(repo: Path):
    result = subprocess.run(
        ["python3", "-m", "ops.project_check", "session-start", "--json", "--no-gh"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=20,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["mode"] == "session-start"
    assert (repo / ".git" / "project_check" / "session_start.json").is_file()


def test_cli_precommit_exit_code_fail_closed(repo: Path):
    start = build_session_start_report(cwd=str(repo), include_gh=False)
    write_session_start_state(str(repo), start)
    _run(["checkout", "-b", "other"], cwd=repo)
    result = subprocess.run(
        ["python3", "-m", "ops.project_check", "precommit"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=20,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )
    assert result.returncode == 2
    assert "FAIL-CLOSED" in result.stdout
