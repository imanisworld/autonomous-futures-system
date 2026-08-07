from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from ops import project_check_trade_chain as pctc


def _entry(**overrides):
    base = {
        "ts": "2026-08-01T14:30:00Z",
        "type": "DECISION",
        "decision": "TRADE",
        "instrument": "MNQ",
        "setup": {"strategy": "orb_breakout", "direction": "LONG"},
        "risk_check": {"result": "APPROVED"},
        "paper_order_id": "ord-1",
    }
    base.update(overrides)
    return base


def _outcome(**overrides):
    base = {
        "ts": "2026-08-01T14:35:00Z",
        "type": "OUTCOME",
        "instrument": "MNQ",
        "outcome": {"result": "WIN", "exit_reason": "target_hit", "pnl_dollars": 40.0},
    }
    base.update(overrides)
    return base


def test_trade_chain_integrity_pass_case():
    entries = [_entry(), _outcome()]
    result = pctc.trade_chain_integrity(entries, since=None)
    assert result["status"] == "PASS"
    assert result["attempts"] == 1
    assert result["fills"] == 1
    assert result["resolved"] == 1
    assert result["orphans"] == 0
    assert result["issues"] == []


def test_trade_chain_integrity_detects_orphan_outcome():
    entries = [_outcome()]  # OUTCOME with no preceding TRADE
    result = pctc.trade_chain_integrity(entries, since=None)
    assert result["status"] == "FAIL"
    assert result["orphans"] == 1
    assert any("orphan" in issue for issue in result["issues"])


def test_trade_chain_integrity_detects_duplicate_order_ids():
    entries = [
        _entry(ts="2026-08-01T14:30:00Z", paper_order_id="same-id"),
        _outcome(ts="2026-08-01T14:35:00Z"),
        _entry(ts="2026-08-01T15:00:00Z", paper_order_id="same-id"),
        _outcome(ts="2026-08-01T15:05:00Z", outcome={"result": "LOSS", "pnl_dollars": -5.0}),
    ]
    result = pctc.trade_chain_integrity(entries, since=None)
    assert result["status"] == "FAIL"
    assert result["duplicate_identities"] == 1
    assert result["duplicate_identity_detail"] == {"same-id": 2}


def test_trade_chain_integrity_legitimately_open_position():
    entries = [_entry()]  # attempt with no resolution yet
    result = pctc.trade_chain_integrity(entries, since=None)
    assert result["status"] == "PASS"
    assert result["legitimate_opens"] == 1
    assert result["resolved"] == 0


def test_trade_chain_integrity_since_filters_old_rows():
    entries = [
        _entry(ts="2026-08-01T00:00:00Z"),
        _outcome(ts="2026-08-01T00:05:00Z"),
        _entry(ts="2026-08-02T00:00:00Z", paper_order_id="ord-2"),
        _outcome(ts="2026-08-02T00:05:00Z"),
    ]
    since = datetime(2026, 8, 2, tzinfo=timezone.utc)
    result = pctc.trade_chain_integrity(entries, since=since)
    assert result["attempts"] == 1
    assert result["resolved"] == 1


def test_trade_chain_integrity_reconciler_touched_flagged_not_failed_alone():
    entries = [
        _entry(),
        _outcome(outcome={"result": "WIN", "exit_reason": "reconcile_phantom_clear", "pnl_dollars": 40.0}),
    ]
    result = pctc.trade_chain_integrity(entries, since=None)
    assert result["reconciler_touched_needing_manual_verification"] == 1
    assert result["status"] == "FAIL"  # reconciler-touched rows always need a human look


def test_format_trade_chain_summary_pass():
    tc = {
        "status": "PASS",
        "attempts": 4,
        "fills": 2,
        "no_fills": 2,
        "resolved": 2,
        "legitimate_opens": 0,
        "orphans": 0,
        "stale_orders": "UNKNOWN",
        "duplicate_identities": 0,
        "broker_journal_parity": "UNKNOWN",
    }
    text = pctc.format_trade_chain_summary(tc)
    assert "TRADE CHAIN: PASS" in text
    assert "4 attempts" in text


def test_format_trade_chain_summary_fail_lists_issues():
    tc = {"status": "FAIL", "issues": ["something broke"]}
    text = pctc.format_trade_chain_summary(tc)
    assert "TRADE CHAIN: FAIL" in text
    assert "something broke" in text


def test_strategy_source_of_truth_flags_conflict(tmp_path):
    inventory_path = tmp_path / "Strategy_Inventory.md"
    inventory_path.write_text(
        "\n".join(
            [
                "## Master Table",
                "| Strategy | Verdict |",
                "|---|---|",
                "| ORB Breakout (MNQ) | **WAIT** |",
            ]
        ),
        encoding="utf-8",
    )
    config = SimpleNamespace(
        strategy_permission_gate_enabled=True,
        strategy_permission_default_status="SHADOW_ONLY",
        strategy_status={"orb_breakout": "PAPER_ELIGIBLE"},
    )
    result = pctc.strategy_source_of_truth(config, inventory_path)
    assert result["available"] is True
    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["configured_status"] == "PAPER_ELIGIBLE"


def test_strategy_source_of_truth_no_conflict_when_statuses_agree(tmp_path):
    inventory_path = tmp_path / "Strategy_Inventory.md"
    inventory_path.write_text(
        "\n".join(
            [
                "## Master Table",
                "| Strategy | Verdict |",
                "|---|---|",
                "| ORB Breakout (MNQ) | **PAPER PROOF** |",
            ]
        ),
        encoding="utf-8",
    )
    config = SimpleNamespace(
        strategy_permission_gate_enabled=True,
        strategy_permission_default_status="SHADOW_ONLY",
        strategy_status={"orb_breakout": "PAPER_ELIGIBLE"},
    )
    result = pctc.strategy_source_of_truth(config, inventory_path)
    assert result["conflicts"] == []


def test_checkpoint_round_trip(tmp_path):
    repo = tmp_path
    (repo / "logs").mkdir()
    assert pctc.load_checkpoint(repo) is None
    path = pctc.write_checkpoint(repo, "2026-08-01T00:00:00+00:00")
    assert path.exists()
    assert pctc.load_checkpoint(repo) == "2026-08-01T00:00:00+00:00"


def test_evidence_preservation_unavailable_when_github_unavailable(tmp_path):
    result = pctc.evidence_preservation(tmp_path, {"available": False})
    assert result["available"] is False
    assert result["blockers"] == []
