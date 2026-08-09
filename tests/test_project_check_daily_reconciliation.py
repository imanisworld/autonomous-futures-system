from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

from ops.daily_reconciliation import (
    _parse_strategy_inventory,
    _slug,
    _strategy_source_of_truth_section,
    build_daily_reconciliation,
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
        "instruments:\n  allowed: [MNQ, MES]\n"
        "strategy:\n  enabled_concepts: [orb_breakout]\n"
        "  disabled_concepts_per_instrument: {MES: [orb_breakout]}\n",
        encoding="utf-8",
    )
    (repo / "docs").mkdir()
    (repo / "docs" / "strategy-rules").mkdir()
    (repo / "docs" / "BRANCH_ARCHIVE_INDEX.md").write_text("# Branch Archive Index\n", encoding="utf-8")
    (repo / "docs" / "strategy-rules" / "Strategy_Inventory.md").write_text(
        "| Strategy | Rules | Verdict |\n"
        "|---|---|---|\n"
        "| ORB Breakout (MNQ) | ok | **PAPER PROOF** |\n"
        "| ORB Reclaim (MES) | ok | **BROKEN** |\n",
        encoding="utf-8",
    )
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.check_call(
        ["git", "add", "risk_rules.yaml", "README.md", "docs"], cwd=repo
    )
    subprocess.check_call(["git", "commit", "-m", "init"], cwd=repo)
    subprocess.check_call(["git", "push", "origin", "main"], cwd=repo)
    (repo / "logs").mkdir()
    return repo


def test_parse_strategy_inventory_extracts_rows(tmp_path):
    path = tmp_path / "inv.md"
    path.write_text(
        "| Strategy | Rules | Verdict |\n"
        "|---|---|---|\n"
        "| ORB Breakout (MNQ) | ok | **PAPER PROOF** |\n",
        encoding="utf-8",
    )
    rows = _parse_strategy_inventory(path)
    assert rows == [{"strategy": "ORB Breakout (MNQ)", "verdict": "PAPER PROOF"}]


def test_slug_strips_parens_and_normalizes():
    assert _slug("ORB Breakout (MNQ)") == "orb_breakout"
    assert _slug("ORB Reclaim (MES)") == "orb_reclaim"


def test_strategy_source_of_truth_flags_active_verdict_not_enabled(tmp_path):
    repo = _init_repo_with_origin(tmp_path)
    active_lanes = {
        "available": True,
        "active_lanes_by_instrument": {"MNQ": ["orb_breakout"], "MES": []},
    }
    section = _strategy_source_of_truth_section(repo, active_lanes)
    issues = {row["strategy"]: row["issue"] for row in section["flagged_drift"]}
    # ORB Reclaim (MES) is BROKEN and not enabled anywhere -- no drift expected.
    assert "ORB Reclaim (MES)" not in issues
    # ORB Breakout (MNQ) is PAPER PROOF-labelled and IS enabled -- no drift.
    assert "ORB Breakout (MNQ)" not in issues


def test_strategy_source_of_truth_flags_broken_but_enabled(tmp_path):
    repo = _init_repo_with_origin(tmp_path)
    active_lanes = {
        "available": True,
        # Simulate ORB Reclaim (MES)'s concept being (incorrectly) active.
        "active_lanes_by_instrument": {"MNQ": ["orb_breakout"], "MES": ["orb_reclaim"]},
    }
    section = _strategy_source_of_truth_section(repo, active_lanes)
    issues = {row["strategy"]: row["issue"] for row in section["flagged_drift"]}
    assert "ORB Reclaim (MES)" in issues
    assert "BROKEN" in issues["ORB Reclaim (MES)"] or "concept" in issues["ORB Reclaim (MES)"]


def test_build_daily_reconciliation_smoke(tmp_path):
    repo = _init_repo_with_origin(tmp_path)
    report = build_daily_reconciliation(
        repo_root=repo,
        journal_dir=repo / "logs",
        today=date(2026, 8, 9),
    )

    assert report["read_only"] is True
    assert report["repo_root"] == str(repo)
    assert "github" in report
    assert "branches_worktrees" in report
    assert "evidence_preservation" in report
    assert "deployed_state" in report
    assert "strategy_source_of_truth" in report
    assert "trade_chain_integrity" in report
    assert report["trade_chain_integrity"]["totals"]["attempts"] == 0
    assert report["trade_chain_integrity"]["overall"] == "PASS"


def test_build_daily_reconciliation_never_writes_journal_or_docs(tmp_path):
    repo = _init_repo_with_origin(tmp_path)
    journal_dir = repo / "logs"
    before_logs = sorted(p.name for p in journal_dir.iterdir())
    before_inventory = (repo / "docs" / "strategy-rules" / "Strategy_Inventory.md").read_text()

    build_daily_reconciliation(repo_root=repo, journal_dir=journal_dir, today=date(2026, 8, 9))

    after_logs = sorted(p.name for p in journal_dir.iterdir())
    after_inventory = (repo / "docs" / "strategy-rules" / "Strategy_Inventory.md").read_text()
    assert before_logs == after_logs
    assert before_inventory == after_inventory
