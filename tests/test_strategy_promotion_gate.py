"""Tests for ops/strategy_promotion_gate.py."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ops.strategy_promotion_gate import build_promotion_report

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


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "logs").mkdir(parents=True)
    (root / "risk_rules.yaml").write_text(RISK_RULES)
    return root


def _write_journal(repo: Path, day: str, rows: list[dict]) -> None:
    path = repo / "logs" / f"journal_{day}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _trade(ts, strategy="orb_breakout", instrument="MNQ"):
    return {
        "ts": ts, "type": "TRADE", "decision": "TRADE", "instrument": instrument,
        "risk_check": {"result": "APPROVED"},
        "setup": {"strategy": strategy, "direction": "LONG", "entry": 100, "stop": 95, "target": 110},
    }


def _outcome(ts, result, pnl, instrument="MNQ"):
    return {
        "ts": ts, "type": "OUTCOME", "instrument": instrument,
        "outcome": {"result": result, "pnl_dollars": pnl, "exit_reason": "target" if result == "WIN" else "stop"},
    }


def test_no_candidates_is_wait(repo: Path) -> None:
    report = build_promotion_report("orb_breakout", repo_root=repo, log_dir="logs")
    assert report["classification"]["verdict"] == "WAIT"
    assert report["gate_attrition"]["candidate_count"] == 0


def test_candidates_but_zero_attempts_is_wait(repo: Path) -> None:
    _write_journal(repo, "2026-08-01", [
        {"ts": "2026-08-01T10:00:00Z", "decision": "RISK_REJECTED", "instrument": "MNQ",
         "setup": {"strategy": "orb_breakout", "direction": "LONG"}, "reason": "daily loss limit",
         "risk_check": {"result": "REJECTED"}},
    ])
    report = build_promotion_report("orb_breakout", repo_root=repo, log_dir="logs")
    assert report["classification"]["verdict"] == "WAIT"
    assert report["execution"]["accounting_identity"]["attempts"] == 0


def test_zero_fills_is_broken(repo: Path) -> None:
    _write_journal(repo, "2026-08-01", [
        _trade("2026-08-01T10:00:00Z"),
        _outcome("2026-08-01T10:05:00Z", "CANCELLED", None),
        _trade("2026-08-01T11:00:00Z"),
        _outcome("2026-08-01T11:05:00Z", "CANCELLED", None),
    ])
    report = build_promotion_report("orb_breakout", repo_root=repo, log_dir="logs")
    assert report["classification"]["verdict"] == "BROKEN"
    assert "zero executable fills" in report["classification"]["reasons"][0]


def test_thin_sample_is_wait_not_promising(repo: Path) -> None:
    rows = []
    for i in range(3):
        rows.append(_trade(f"2026-08-01T{10 + i}:00:00Z"))
        rows.append(_outcome(f"2026-08-01T{10 + i}:05:00Z", "WIN", 50.0))
    _write_journal(repo, "2026-08-01", rows)
    report = build_promotion_report("orb_breakout", repo_root=repo, log_dir="logs")
    assert report["classification"]["verdict"] == "WAIT"
    assert report["performance"]["filled_count"] == 3


def test_healthy_sample_caps_at_promising_but_unproven(repo: Path) -> None:
    rows = []
    for i in range(35):
        ts_hour = 10 + (i % 6)
        rows.append(_trade(f"2026-08-{1 + i // 6:02d}T{ts_hour:02d}:00:00Z"))
        result = "LOSS" if i % 4 == 0 else "WIN"
        pnl = -20.0 if result == "LOSS" else 50.0
        rows.append(_outcome(f"2026-08-{1 + i // 6:02d}T{ts_hour:02d}:05:00Z", result, pnl))
    _write_journal(repo, "2026-08-01", rows)
    report = build_promotion_report("orb_breakout", repo_root=repo, log_dir="logs")
    assert report["classification"]["verdict"] == "PROMISING BUT UNPROVEN"
    assert "VALIDATED requires human review" in " ".join(report["classification"]["reasons"])
    assert report["performance"]["filled_count"] == 35


def test_never_returns_validated_automatically(repo: Path) -> None:
    rows = []
    for i in range(50):
        rows.append(_trade(f"2026-08-01T{10 + (i % 6):02d}:00:00Z"))
        rows.append(_outcome(f"2026-08-01T{10 + (i % 6):02d}:05:00Z", "WIN", 50.0))
    _write_journal(repo, "2026-08-01", rows)
    report = build_promotion_report("orb_breakout", repo_root=repo, log_dir="logs")
    assert report["classification"]["verdict"] != "VALIDATED"


def test_execution_context_reflects_permission_gate_status(repo: Path) -> None:
    report = build_promotion_report("orb_breakout", repo_root=repo, log_dir="logs")
    assert report["execution_context"]["strategy_permission_gate_status"] == "PAPER_ELIGIBLE"


def test_never_edits_risk_rules(repo: Path) -> None:
    before = (repo / "risk_rules.yaml").read_text()
    build_promotion_report("orb_breakout", repo_root=repo, log_dir="logs")
    after = (repo / "risk_rules.yaml").read_text()
    assert before == after
