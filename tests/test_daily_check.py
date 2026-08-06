from __future__ import annotations

import json
import subprocess
from pathlib import Path

import ops.daily_check as daily_check
from ops.daily_check import (
    build_daily_report,
    evidence_preservation,
    format_daily_report,
    github_reconciliation,
    strategy_source_of_truth,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_remote_pair(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    subprocess.check_call(["git", "init", "--bare", "-b", "main", str(remote)])
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-b", "main"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "test@example.com"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "Test User"], cwd=repo)
    (repo / "a.txt").write_text("one\n")
    subprocess.check_call(["git", "add", "a.txt"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "init"], cwd=repo)
    subprocess.check_call(["git", "remote", "add", "origin", str(remote)], cwd=repo)
    subprocess.check_call(["git", "push", "-u", "origin", "main"], cwd=repo)
    return repo, remote


def test_github_reconciliation_reports_unknown_without_gh(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_check.shutil, "which", lambda _name: None)
    from datetime import date
    result = github_reconciliation(tmp_path, today=date(2026, 8, 1))
    assert result["status"] == "UNKNOWN"
    assert "gh CLI not found" in result["reason"]


def test_evidence_preservation_flags_unique_unarchived_branch(tmp_path):
    repo, _remote = _init_remote_pair(tmp_path)
    subprocess.check_call(["git", "checkout", "-b", "codex/unique-work"], cwd=repo)
    (repo / "unique.txt").write_text("unique content\n")
    subprocess.check_call(["git", "add", "unique.txt"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "unique work"], cwd=repo)
    subprocess.check_call(["git", "checkout", "main"], cwd=repo)

    report = evidence_preservation(repo, base_branch="main")
    findings = {f["branch"]: f for f in report["findings"]}
    assert findings["codex/unique-work"]["status"] == "BLOCKER"
    assert report["blockers"][0]["branch"] == "codex/unique-work"


def test_evidence_preservation_marks_preserved_when_archive_tag_matches(tmp_path):
    repo, _remote = _init_remote_pair(tmp_path)
    subprocess.check_call(["git", "checkout", "-b", "codex/unique-work"], cwd=repo)
    (repo / "unique.txt").write_text("unique content\n")
    subprocess.check_call(["git", "add", "unique.txt"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "unique work"], cwd=repo)
    subprocess.check_call(
        ["git", "tag", "-a", "archive/codex-unique-work-2026-08-01", "-m", "archive"], cwd=repo
    )
    subprocess.check_call(["git", "checkout", "main"], cwd=repo)

    report = evidence_preservation(repo, base_branch="main")
    findings = {f["branch"]: f for f in report["findings"]}
    assert findings["codex/unique-work"]["status"] == "PRESERVED"
    assert report["blockers"] == []


def test_evidence_preservation_never_creates_or_deletes_tags(tmp_path):
    repo, _remote = _init_remote_pair(tmp_path)
    subprocess.check_call(["git", "checkout", "-b", "codex/unique-work"], cwd=repo)
    (repo / "unique.txt").write_text("unique content\n")
    subprocess.check_call(["git", "add", "unique.txt"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "unique work"], cwd=repo)
    subprocess.check_call(["git", "checkout", "main"], cwd=repo)

    evidence_preservation(repo, base_branch="main")
    tags = _git(repo, "tag", "-l")
    assert tags == ""
    branches = _git(repo, "branch", "--list")
    assert "codex/unique-work" in branches


def _write_risk_rules(repo: Path, enabled_concepts: list[str]) -> None:
    (repo / "risk_rules.yaml").write_text(
        "strategy:\n  enabled_concepts:\n" + "".join(f"    - {c}\n" for c in enabled_concepts),
        encoding="utf-8",
    )


def test_strategy_source_of_truth_flags_broken_but_enabled(tmp_path):
    inventory = tmp_path / "docs" / "strategy-rules" / "Strategy_Inventory.md"
    inventory.parent.mkdir(parents=True)
    inventory.write_text(
        "## Master Table\n\n"
        "| Strategy | Verdict |\n|---|---|\n"
        "| Strat 22 Reversal | **BROKEN** |\n",
        encoding="utf-8",
    )
    _write_risk_rules(tmp_path, ["strat_22_reversal"])
    (tmp_path / "logs").mkdir()

    result = strategy_source_of_truth(tmp_path, log_dir=tmp_path / "logs")
    assert result["status"] == "OK"
    flags = {f["strategy"]: f for f in result["flags"]}
    assert flags["Strat 22 Reversal"]["flag"] == "DOCUMENTED_BROKEN_BUT_TOKEN_MATCH_IN_ENABLED_CONCEPTS"


def test_strategy_source_of_truth_unknown_when_inventory_missing(tmp_path):
    (tmp_path / "risk_rules.yaml").write_text("strategy:\n  enabled_concepts: []\n", encoding="utf-8")
    result = strategy_source_of_truth(tmp_path, log_dir=tmp_path / "logs")
    assert result["status"] == "UNKNOWN"


def test_build_daily_report_end_to_end_smoke(tmp_path, monkeypatch):
    repo, _remote = _init_remote_pair(tmp_path)
    monkeypatch.setattr(daily_check.shutil, "which", lambda _name: None)

    report = build_daily_report(repo_root=repo, log_dir=repo / "logs")
    assert report["github"]["status"] == "UNKNOWN"
    assert report["branches_worktrees"]["current_branch"] == "main"
    assert report["trade_chain"]["ok"] is True
    text = format_daily_report(report)
    assert "DAILY RECONCILIATION" in text
    assert "TRADE CHAIN: PASS" in text


def test_build_daily_report_never_mutates_repo(tmp_path, monkeypatch):
    repo, _remote = _init_remote_pair(tmp_path)
    monkeypatch.setattr(daily_check.shutil, "which", lambda _name: None)
    before_head = _git(repo, "rev-parse", "HEAD")
    before_status = _git(repo, "status", "--porcelain", "--", ":!logs")

    build_daily_report(repo_root=repo, log_dir=repo / "logs")

    after_head = _git(repo, "rev-parse", "HEAD")
    after_status = _git(repo, "status", "--porcelain", "--", ":!logs")
    assert before_head == after_head
    assert before_status == after_status
