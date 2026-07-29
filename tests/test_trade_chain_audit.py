"""tests/test_trade_chain_audit.py

Proves ops.trade_chain_audit's accounting identities, orphan/legitimate-open
classification, and duplicate-order-identity detection against synthetic
journals -- and that a clean day renders the compact PASS form the daily
routine's spec requires, not a wall of text.
"""

from __future__ import annotations

import json
from pathlib import Path

from ops.trade_chain_audit import audit_trade_chain, format_compact


def _write_journal(log_dir: Path, day: str, rows: list[dict]) -> None:
    path = log_dir / f"journal_{day}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _trade(ts: str, instrument: str = "MNQ", **overrides) -> dict:
    row = {
        "ts": ts,
        "decision": "TRADE",
        "instrument": instrument,
        "setup": {"strategy": "orb_reclaim", "direction": "LONG", "entry": 100, "stop": 99, "target": 102, "contracts": 1},
    }
    row.update(overrides)
    return row


def _order_ids(ts: str, instrument: str = "MNQ", ids: dict | None = None) -> dict:
    return {
        "ts": ts, "type": "ORDER_IDS", "instrument": instrument, "session": "new_york",
        "order_ids": ids or {"entry_order_id": "1", "stop_order_id": "2", "target_order_id": "3"},
    }


def _outcome(ts: str, instrument: str = "MNQ", result: str = "WIN", pnl: float = 30.0, exit_reason: str = "target") -> dict:
    return {"ts": ts, "type": "OUTCOME", "instrument": instrument, "outcome": {"result": result, "pnl_dollars": pnl, "exit_reason": exit_reason}}


def test_fully_resolved_day_passes_and_renders_compact(tmp_path):
    _write_journal(tmp_path, "2026-07-20", [
        _trade("2026-07-20T10:00:00+00:00"),
        _order_ids("2026-07-20T10:00:05+00:00"),
        _outcome("2026-07-20T10:30:00+00:00"),
        {"ts": "2026-07-20T11:00:00+00:00", "decision": "NO_TRADE", "instrument": "MNQ", "reason": "no setup"},
    ])
    report = audit_trade_chain(tmp_path)
    assert report["pass"] is True
    assert report["accounting"]["attempts"] == 1
    assert report["accounting"]["fills"] == 1
    assert report["accounting"]["orphans"] == 0
    rendered = format_compact(report)
    assert rendered.startswith("TRADE CHAIN: PASS")
    assert "1 attempts" in rendered


def test_rejected_decision_without_reason_is_flagged(tmp_path):
    _write_journal(tmp_path, "2026-07-20", [
        {"ts": "2026-07-20T11:00:00+00:00", "decision": "RISK_REJECTED", "instrument": "MNQ"},
    ])
    report = audit_trade_chain(tmp_path)
    assert report["pass"] is False
    assert any("missing a reason" in p for p in report["problems"])


def test_unresolved_trade_with_no_open_state_is_an_orphan(tmp_path):
    _write_journal(tmp_path, "2026-07-20", [_trade("2026-07-20T10:00:00+00:00")])
    report = audit_trade_chain(tmp_path)
    assert report["pass"] is False
    assert report["accounting"]["orphans"] == 1
    assert report["accounting"]["legitimately_open"] == 0
    assert any("orphan" in p for p in report["problems"])


def test_unresolved_trade_matching_open_lane_state_is_legitimate(tmp_path):
    _write_journal(tmp_path, "2026-07-20", [_trade("2026-07-20T10:00:00+00:00", instrument="MNQ")])
    # Match one of the MNQ lane state files the live engine itself writes/reads.
    (tmp_path / "mnq_strat_22_reversal_state.json").write_text(
        json.dumps({"position": {"instrument": "MNQ"}})
    )
    report = audit_trade_chain(tmp_path)
    assert report["accounting"]["legitimately_open"] == 1
    assert report["accounting"]["orphans"] == 0
    assert report["pass"] is True


def test_duplicate_order_identity_across_two_trades_is_flagged(tmp_path):
    ids = {"entry_order_id": "1", "stop_order_id": "2", "target_order_id": "3"}
    _write_journal(tmp_path, "2026-07-20", [
        _trade("2026-07-20T10:00:00+00:00"),
        _order_ids("2026-07-20T10:00:05+00:00", ids=ids),
        _outcome("2026-07-20T10:30:00+00:00"),
        _trade("2026-07-20T11:00:00+00:00"),
        _order_ids("2026-07-20T11:00:05+00:00", ids=ids),
        _outcome("2026-07-20T11:30:00+00:00"),
    ])
    report = audit_trade_chain(tmp_path)
    assert report["pass"] is False
    assert len(report["duplicate_order_identities"]) == 1
    assert any("duplicate order identity" in p for p in report["problems"])


def test_incomplete_bracket_is_flagged(tmp_path):
    _write_journal(tmp_path, "2026-07-20", [
        _trade("2026-07-20T10:00:00+00:00", setup={"strategy": "orb_reclaim", "direction": "LONG", "entry": 100, "stop": None, "target": 102}),
        _order_ids("2026-07-20T10:00:05+00:00"),
        _outcome("2026-07-20T10:30:00+00:00"),
    ])
    report = audit_trade_chain(tmp_path)
    assert report["pass"] is False
    assert any("incomplete stop/target bracket" in p for p in report["problems"])


def test_corrupt_journal_row_is_counted_as_a_problem(tmp_path):
    path = tmp_path / "journal_2026-07-20.jsonl"
    path.write_text("not json\n")
    report = audit_trade_chain(tmp_path)
    assert report["journal_read_errors"] == 1
    assert report["pass"] is False


def test_accounting_identity_holds_across_mixed_outcomes(tmp_path):
    _write_journal(tmp_path, "2026-07-20", [
        _trade("2026-07-20T10:00:00+00:00"),
        _order_ids("2026-07-20T10:00:05+00:00", ids={"entry_order_id": "a"}),
        _outcome("2026-07-20T10:30:00+00:00", result="WIN", pnl=30.0),

        _trade("2026-07-20T11:00:00+00:00"),
        _order_ids("2026-07-20T11:00:05+00:00", ids={"entry_order_id": "b"}),
        _outcome("2026-07-20T11:30:00+00:00", result="CANCELLED", pnl=0.0, exit_reason="ioc no-fill"),
    ])
    report = audit_trade_chain(tmp_path)
    acc = report["accounting"]
    assert acc["attempts"] == 2
    assert acc["fills"] == 1
    assert acc["cancellations"] == 1
    assert acc["identity_attempts_eq_fills_plus_cancellations_plus_rejects_plus_opens"] is True
