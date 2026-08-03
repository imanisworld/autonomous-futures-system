"""Tests for ops.strategy_promotion — the strategy promotion proof gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ops import strategy_promotion as sp

RISK_RULES_YAML = """
version: "test"
trading_mode:
  live_trading_enabled: false
  paper_mode: true
  sim_fill_at_entry: false
instruments:
  allowed: [MNQ, MES]
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

INVENTORY_MD = """# STRATEGY INVENTORY

## Master Table

| Strategy | Rules | Detector | Replay parity | Honest fills | Walk-forward | Slippage | Sample | Verdict |
|---|---|---|---|---|---|---|---|---|
| ORB Breakout (MNQ) | OK | OK | OK | ioc_limit | OK | OK | n=25 | **WAIT** |

---

## Detailed Strategy Profiles
"""


def _write_journal(journal_dir: Path, day: str, rows: list[dict]) -> None:
    journal_dir.mkdir(parents=True, exist_ok=True)
    path = journal_dir / f"journal_{day}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _trade(ts: str, instrument: str, strategy: str) -> dict:
    return {
        "ts": ts, "instrument": instrument, "decision": "TRADE", "reason": "signal",
        "setup": {"direction": "LONG", "entry": 100.0, "stop": 95.0, "target": 110.0, "rr_ratio": 2.0, "strategy": strategy},
        "risk_check": {"result": "APPROVED", "failed_rule": None, "reason": None},
    }


def _outcome(ts: str, instrument: str, result: str, exit_reason: str | None, pnl_dollars: float) -> dict:
    return {
        "ts": ts, "instrument": instrument, "type": "OUTCOME", "session": "new_york",
        "outcome": {"result": result, "exit_reason": exit_reason, "pnl_dollars": pnl_dollars},
    }


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "docs" / "strategy-rules").mkdir(parents=True)
    (root / "docs" / "strategy-rules" / "Strategy_Inventory.md").write_text(INVENTORY_MD, encoding="utf-8")
    (root / "risk_rules.yaml").write_text(RISK_RULES_YAML, encoding="utf-8")
    return root


def test_zero_fills_classifies_wait(repo: Path) -> None:
    journal_dir = repo / "logs"
    _write_journal(journal_dir, "2026-08-01", [])
    report = sp.build_promotion_report("orb_breakout", repo_root=repo, journal_dir=journal_dir, to_date="2026-08-02")
    assert report["classification"]["suggested"] == "WAIT"
    assert "zero resolved fills" in " ".join(report["classification"]["reasons"])


def test_thin_positive_sample_classifies_promising_but_unproven(repo: Path) -> None:
    journal_dir = repo / "logs"
    rows = []
    for i in range(3):
        ts_trade = f"2026-08-01T{10+i:02d}:00:00Z"
        ts_outcome = f"2026-08-01T{10+i:02d}:15:00Z"
        rows.append(_trade(ts_trade, "MNQ", "orb_breakout"))
        rows.append(_outcome(ts_outcome, "MNQ", "WIN", "TARGET_HIT", 50.0))
    _write_journal(journal_dir, "2026-08-01", rows)

    report = sp.build_promotion_report("orb_breakout", repo_root=repo, journal_dir=journal_dir, to_date="2026-08-02")
    assert report["classification"]["suggested"] == "PROMISING BUT UNPROVEN"
    assert report["performance"]["combined_net_pnl_dollars"] == 150.0


def test_never_outputs_validated(repo: Path) -> None:
    journal_dir = repo / "logs"
    rows = []
    for i in range(35):
        ts_trade = f"2026-08-01T10:{i:02d}:00Z"
        ts_outcome = f"2026-08-01T10:{i:02d}:30Z"
        rows.append(_trade(ts_trade, "MNQ", "orb_breakout"))
        rows.append(_outcome(ts_outcome, "MNQ", "WIN", "TARGET_HIT", 50.0))
    _write_journal(journal_dir, "2026-08-01", rows)

    report = sp.build_promotion_report("orb_breakout", repo_root=repo, journal_dir=journal_dir, to_date="2026-08-02")
    assert report["classification"]["suggested"] != "VALIDATED"
    assert report["classification"]["suggested"] == "PROMISING BUT UNPROVEN"
    assert "capped at PROMISING BUT UNPROVEN" in " ".join(report["classification"]["reasons"])


def test_adequate_negative_sample_classifies_broken(repo: Path) -> None:
    journal_dir = repo / "logs"
    rows = []
    for i in range(35):
        ts_trade = f"2026-08-01T10:{i:02d}:00Z"
        ts_outcome = f"2026-08-01T10:{i:02d}:30Z"
        rows.append(_trade(ts_trade, "MNQ", "orb_breakout"))
        rows.append(_outcome(ts_outcome, "MNQ", "LOSS", "STOP_HIT", -25.0))
    _write_journal(journal_dir, "2026-08-01", rows)

    report = sp.build_promotion_report("orb_breakout", repo_root=repo, journal_dir=journal_dir, to_date="2026-08-02")
    assert report["classification"]["suggested"] == "BROKEN"


def test_runtime_parity_reflects_risk_rules(repo: Path) -> None:
    journal_dir = repo / "logs"
    _write_journal(journal_dir, "2026-08-01", [])
    report = sp.build_promotion_report("orb_breakout", repo_root=repo, journal_dir=journal_dir, to_date="2026-08-02")
    parity = report["runtime_parity"]
    assert parity["strategy_status"] == "PAPER_ELIGIBLE"
    assert parity["reachable"] is True

    report_other = sp.build_promotion_report("vwap_hold", repo_root=repo, journal_dir=journal_dir, to_date="2026-08-02")
    assert report_other["runtime_parity"]["strategy_status"] == "SHADOW_ONLY"
    assert report_other["runtime_parity"]["reachable"] is False


def test_research_result_matches_inventory_row(repo: Path) -> None:
    journal_dir = repo / "logs"
    _write_journal(journal_dir, "2026-08-01", [])
    report = sp.build_promotion_report("orb_breakout", repo_root=repo, journal_dir=journal_dir, to_date="2026-08-02")
    assert report["research_result"]["match_count"] == 1
    assert report["research_result"]["matched_rows"][0]["verdict_normalized"] == "WAIT"


def test_classification_never_includes_options_beyond_taxonomy(repo: Path) -> None:
    journal_dir = repo / "logs"
    _write_journal(journal_dir, "2026-08-01", [])
    report = sp.build_promotion_report("orb_breakout", repo_root=repo, journal_dir=journal_dir, to_date="2026-08-02")
    assert report["classification"]["suggested"] in sp.CLASSIFICATIONS
