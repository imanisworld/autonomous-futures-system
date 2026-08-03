"""Tests for ops.daily_reconciliation — routine 3."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ops import daily_reconciliation as dr

RISK_RULES_YAML = """
version: "test"
trading_mode:
  live_trading_enabled: false
  paper_mode: true
  sim_fill_at_entry: false
instruments:
  allowed: [MNQ]
  required: [MNQ]
sessions:
  allowed: [new_york]
  disabled: []
daily_limits:
  max_trades_per_day: 9999
  max_consecutive_losses: 9999
  max_daily_loss: 150
  max_drawdown_percent: 0.20
  circuit_breaker_losses: 0
  circuit_breaker_pause_minutes: 30
strategy_permission_gate:
  enabled: true
  default_status: SHADOW_ONLY
  strategy_status:
    orb_breakout: PAPER_ELIGIBLE
strategy:
  enabled_concepts:
    - orb_breakout
"""

INVENTORY_ACTIVE_MATCHES_MD = """# STRATEGY INVENTORY

## Master Table

| Strategy | Rules | Detector | Replay parity | Honest fills | Walk-forward | Slippage | Sample | Verdict |
|---|---|---|---|---|---|---|---|---|
| ORB Breakout (MNQ) | OK | OK | OK | market | OK | OK | n=25 | **PAPER PROOF** |

---
"""

INVENTORY_DRIFT_MD = """# STRATEGY INVENTORY

## Master Table

| Strategy | Rules | Detector | Replay parity | Honest fills | Walk-forward | Slippage | Sample | Verdict |
|---|---|---|---|---|---|---|---|---|
| ORB Breakout (MNQ) | OK | OK | OK | market | OK | OK | n=25 | **WAIT** |

---
"""


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(root), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    return result.stdout.strip()


def _init_repo(root: Path, *, inventory: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "risk_rules.yaml").write_text(RISK_RULES_YAML, encoding="utf-8")
    (root / "docs" / "strategy-rules").mkdir(parents=True)
    (root / "docs" / "strategy-rules" / "Strategy_Inventory.md").write_text(inventory, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial commit")


@pytest.fixture
def clean_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    _init_repo(root, inventory=INVENTORY_ACTIVE_MATCHES_MD)
    return root


@pytest.fixture
def drifted_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    _init_repo(root, inventory=INVENTORY_DRIFT_MD)
    return root


def test_daily_report_passes_when_nothing_is_wrong(clean_repo: Path) -> None:
    journal_dir = clean_repo / "logs"
    journal_dir.mkdir()
    (journal_dir / "journal_2026-08-01.jsonl").write_text("", encoding="utf-8")

    report = dr.build_daily_report(
        repo_root=clean_repo, journal_dir=journal_dir, today="2026-08-01",
        from_date="2026-08-01", do_fetch=False, save_checkpoint=False,
    )
    assert report["ok"] is True
    assert report["status"] == "PASS"
    assert report["blockers"] == []
    assert report["strategy_source_of_truth"]["drift"] == []
    assert report["trade_chain"]["ok"] is True


def test_daily_report_flags_strategy_inventory_drift(drifted_repo: Path) -> None:
    journal_dir = drifted_repo / "logs"
    journal_dir.mkdir()
    (journal_dir / "journal_2026-08-01.jsonl").write_text("", encoding="utf-8")

    report = dr.build_daily_report(
        repo_root=drifted_repo, journal_dir=journal_dir, today="2026-08-01",
        from_date="2026-08-01", do_fetch=False, save_checkpoint=False,
    )
    assert report["ok"] is False
    assert report["strategy_source_of_truth"]["drift"]
    assert any("orb_breakout" in b for b in report["blockers"])


def test_daily_report_flags_unpreserved_evidence(clean_repo: Path) -> None:
    _git(clean_repo, "checkout", "-q", "-b", "research/unique-work")
    (clean_repo / "research.txt").write_text("unique\n", encoding="utf-8")
    _git(clean_repo, "add", "research.txt")
    _git(clean_repo, "commit", "-q", "-m", "unique research commit")
    _git(clean_repo, "checkout", "-q", "main")

    journal_dir = clean_repo / "logs"
    journal_dir.mkdir()
    (journal_dir / "journal_2026-08-01.jsonl").write_text("", encoding="utf-8")

    report = dr.build_daily_report(
        repo_root=clean_repo, journal_dir=journal_dir, today="2026-08-01",
        from_date="2026-08-01", do_fetch=False, save_checkpoint=False,
    )
    assert report["ok"] is False
    assert "research/unique-work" in report["evidence_preservation"]["blockers"]
    assert "research/unique-work" in report["blockers"]


def test_daily_report_saves_and_reuses_checkpoint(clean_repo: Path) -> None:
    journal_dir = clean_repo / "logs"
    journal_dir.mkdir()
    (journal_dir / "journal_2026-08-01.jsonl").write_text("", encoding="utf-8")

    dr.build_daily_report(
        repo_root=clean_repo, journal_dir=journal_dir, today="2026-08-01",
        do_fetch=False, save_checkpoint=True,
    )
    checkpoint_path = dr._checkpoint_path(clean_repo)
    assert checkpoint_path is not None and checkpoint_path.exists()
    saved = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert saved["last_to_date"] == "2026-08-01"

    report2 = dr.build_daily_report(
        repo_root=clean_repo, journal_dir=journal_dir, today="2026-08-02",
        do_fetch=False, save_checkpoint=False,
    )
    assert report2["window"]["from_date"] == "2026-08-01"


def test_daily_report_is_read_only(clean_repo: Path) -> None:
    before_branch = _git(clean_repo, "rev-parse", "--abbrev-ref", "HEAD")
    before_head = _git(clean_repo, "rev-parse", "HEAD")
    journal_dir = clean_repo / "logs"
    journal_dir.mkdir()
    (journal_dir / "journal_2026-08-01.jsonl").write_text("", encoding="utf-8")

    dr.build_daily_report(
        repo_root=clean_repo, journal_dir=journal_dir, today="2026-08-01",
        do_fetch=False, save_checkpoint=True,
    )
    assert _git(clean_repo, "rev-parse", "--abbrev-ref", "HEAD") == before_branch
    assert _git(clean_repo, "rev-parse", "HEAD") == before_head
