"""Tests for ops/daily_reconciliation.py."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ops.daily_reconciliation import (
    _parse_strategy_inventory,
    _slug_key,
    build_daily_reconciliation_report,
    format_trade_chain_line,
)

RISK_RULES = """
strategy_permission_gate:
  enabled: true
  default_status: SHADOW_ONLY
  strategy_status:
    orb_breakout: PAPER_ELIGIBLE
strategy:
  enabled_concepts:
    - orb_breakout
instruments:
  allowed:
    - MNQ
position_rules:
  max_contracts_per_instrument:
    MNQ: 6
"""

INVENTORY_DRIFT = """# STRATEGY INVENTORY

## Master Table

| Strategy | Rules | Detector | Replay parity | Honest fills | Walk-forward | Slippage | Sample | Verdict |
|---|---|---|---|---|---|---|---|---|
| ORB Breakout (MNQ) | ok | ok | ok | ok | fail | fail | n=25 | **WAIT** |

## Detailed Strategy Profiles
"""

INVENTORY_CLEAN = """# STRATEGY INVENTORY

## Master Table

| Strategy | Rules | Detector | Replay parity | Honest fills | Walk-forward | Slippage | Sample | Verdict |
|---|---|---|---|---|---|---|---|---|
| ORB Breakout (MNQ) | ok | ok | ok | ok | ok | ok | n=200 | **PAPER PROOF** |

## Detailed Strategy Profiles
"""


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True)


def _make_repo(tmp_path: Path, inventory_md: str) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "risk_rules.yaml").write_text(RISK_RULES)
    (root / "docs" / "strategy-rules").mkdir(parents=True)
    (root / "docs" / "strategy-rules" / "Strategy_Inventory.md").write_text(inventory_md)
    (root / "logs").mkdir()
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def test_slug_key_strips_parenthetical_and_normalizes():
    assert _slug_key("ORB Breakout (MNQ)") == "orb_breakout"
    assert _slug_key("60M 3-2-2 First Live") == "60m_3_2_2_first_live"


def test_parse_strategy_inventory_extracts_verdict(tmp_path: Path):
    root = tmp_path / "repo"
    (root / "docs" / "strategy-rules").mkdir(parents=True)
    (root / "docs" / "strategy-rules" / "Strategy_Inventory.md").write_text(INVENTORY_DRIFT)
    rows = _parse_strategy_inventory(root)
    assert len(rows) == 1
    assert rows[0]["display_name"] == "ORB Breakout (MNQ)"
    assert rows[0]["verdict_token"] == "WAIT"


def test_parse_strategy_inventory_missing_file_returns_empty(tmp_path: Path):
    assert _parse_strategy_inventory(tmp_path / "nope") == []


def test_strategy_drift_flags_blocker_when_enabled_but_inventory_wait(tmp_path: Path):
    root = _make_repo(tmp_path, INVENTORY_DRIFT)
    report = build_daily_reconciliation_report(repo_root=root, log_dir="logs", check_prs=False)
    assert report["overall_verdict"] == "BLOCKER"
    findings = report["strategy_source_of_truth"]["findings"]
    blockers = [f for f in findings if f["severity"] == "blocker"]
    assert len(blockers) == 1
    assert "WAIT" in blockers[0]["issue"]
    assert any("strategy drift" in b for b in report["blockers"])


def test_strategy_drift_clean_when_inventory_agrees(tmp_path: Path):
    root = _make_repo(tmp_path, INVENTORY_CLEAN)
    report = build_daily_reconciliation_report(repo_root=root, log_dir="logs", check_prs=False)
    blockers = [f for f in report["strategy_source_of_truth"]["findings"] if f["severity"] == "blocker"]
    assert blockers == []


def test_trade_chain_pass_when_journal_clean(tmp_path: Path):
    root = _make_repo(tmp_path, INVENTORY_CLEAN)
    day = "2026-08-01"
    rows = [
        {"ts": f"{day}T10:00:00Z", "type": "TRADE", "decision": "TRADE", "instrument": "MNQ",
         "risk_check": {"result": "APPROVED"}, "setup": {"strategy": "orb_breakout", "direction": "LONG"}},
        {"ts": f"{day}T10:01:00Z", "type": "ORDER_IDS", "order_ids": {"instrument": "MNQ", "entry": "1"}},
        {"ts": f"{day}T10:05:00Z", "type": "OUTCOME", "instrument": "MNQ",
         "outcome": {"result": "WIN", "pnl_dollars": 50.0, "exit_reason": "target"}},
    ]
    path = root / "logs" / f"journal_{day}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")

    report = build_daily_reconciliation_report(repo_root=root, log_dir="logs", target_date=day, check_prs=False)
    chain = report["trade_chain_integrity"]
    assert chain["summary"]["verdict"] == "PASS"
    assert chain["summary"]["attempts"] == 1
    assert chain["summary"]["fills"] == 1
    assert "PASS" in format_trade_chain_line(chain)


def test_trade_chain_fails_on_duplicate_order_id(tmp_path: Path):
    root = _make_repo(tmp_path, INVENTORY_CLEAN)
    day = "2026-08-01"
    rows = [
        {"ts": f"{day}T10:00:00Z", "type": "ORDER_IDS", "order_ids": {"instrument": "MNQ", "entry": "1"}},
        {"ts": f"{day}T10:01:00Z", "type": "ORDER_IDS", "order_ids": {"instrument": "MNQ", "entry": "1"}},
    ]
    path = root / "logs" / f"journal_{day}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")

    report = build_daily_reconciliation_report(repo_root=root, log_dir="logs", target_date=day, check_prs=False)
    chain = report["trade_chain_integrity"]
    assert chain["summary"]["verdict"] == "FAIL"
    assert any("duplicate" in a for a in chain["anomalies"])
    assert report["overall_verdict"] == "BLOCKER"


def test_writes_checkpoint_and_never_mutates_journal(tmp_path: Path):
    root = _make_repo(tmp_path, INVENTORY_CLEAN)
    day = "2026-08-01"
    path = root / "logs" / f"journal_{day}.jsonl"
    path.write_text(json.dumps({"ts": f"{day}T10:00:00Z", "type": "OUTCOME", "instrument": "MNQ",
                                 "outcome": {"result": "WIN", "pnl_dollars": 1.0}}) + "\n")
    before = path.read_text()
    report = build_daily_reconciliation_report(repo_root=root, log_dir="logs", target_date=day, check_prs=False)
    after = path.read_text()
    assert before == after
    checkpoint_path = root / "logs" / ".daily_reconciliation_checkpoint.json"
    assert checkpoint_path.exists()
    assert report["checkpoint_written"] == str(checkpoint_path)


def test_never_creates_git_tags(tmp_path: Path):
    root = _make_repo(tmp_path, INVENTORY_DRIFT)
    tags_before = subprocess.run(
        ["git", "tag", "-l"], cwd=str(root), capture_output=True, text=True
    ).stdout
    build_daily_reconciliation_report(repo_root=root, log_dir="logs", check_prs=False)
    tags_after = subprocess.run(
        ["git", "tag", "-l"], cwd=str(root), capture_output=True, text=True
    ).stdout
    assert tags_before == tags_after
