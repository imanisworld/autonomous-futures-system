"""Tests for ops/trade_chain.py -- shared pairing/accounting primitives."""
from __future__ import annotations

from ops.trade_chain import (
    build_accounting_identity,
    duplicate_order_ids,
    gate_attrition,
    pair_trades,
)


def _trade(ts, instrument="MNQ", strategy="orb_breakout", direction="LONG"):
    return {
        "ts": ts,
        "type": "TRADE",
        "decision": "TRADE",
        "instrument": instrument,
        "risk_check": {"result": "APPROVED"},
        "setup": {"strategy": strategy, "direction": direction},
    }


def _outcome(ts, instrument="MNQ", result="WIN", pnl=50.0, exit_reason="target hit"):
    return {
        "ts": ts,
        "type": "OUTCOME",
        "instrument": instrument,
        "outcome": {"result": result, "pnl_dollars": pnl, "exit_reason": exit_reason},
    }


def test_pair_trades_matches_trade_to_next_outcome_same_instrument():
    entries = [_trade("t1"), _outcome("t2", result="WIN", pnl=50.0)]
    resolved, still_open, unmatched = pair_trades(entries, lambda e: True)
    assert len(resolved) == 1
    assert still_open == []
    assert unmatched == []
    assert resolved[0].outcome_body["result"] == "WIN"


def test_pair_trades_filters_by_key_fn():
    entries = [
        _trade("t1", strategy="orb_breakout"),
        _trade("t1b", instrument="MES", strategy="vwap_hold"),
        _outcome("t2", instrument="MNQ", result="WIN"),
        _outcome("t2b", instrument="MES", result="LOSS"),
    ]
    resolved, still_open, _ = pair_trades(entries, lambda e: (e.get("setup") or {}).get("strategy") == "orb_breakout")
    assert len(resolved) == 1
    assert resolved[0].trade["instrument"] == "MNQ"
    assert still_open == []


def test_pair_trades_leaves_unresolved_trade_open():
    entries = [_trade("t1")]
    resolved, still_open, _ = pair_trades(entries, lambda e: True)
    assert resolved == []
    assert len(still_open) == 1


def test_build_accounting_identity_balances_fills_cancellations_and_open():
    entries = [
        _trade("t1"), _outcome("t2", result="WIN", pnl=50.0),
        _trade("t3"), _outcome("t4", result="CANCELLED", pnl=None, exit_reason="ioc no fill"),
        _trade("t5"),  # still open
    ]
    resolved, still_open, _ = pair_trades(entries, lambda e: True)
    identity = build_accounting_identity(resolved, still_open)
    assert identity.attempts == 3
    assert identity.fills == 1
    assert identity.cancellations == 1
    assert identity.legitimately_open == 1
    assert identity.ok is True


def test_build_accounting_identity_reconciler_touched_counts_as_needs_manual():
    entries = [_trade("t1"), _outcome("t2", result="LOSS", pnl=-10.0, exit_reason="auto-reconcile phantom clear")]
    resolved, still_open, _ = pair_trades(entries, lambda e: True)
    identity = build_accounting_identity(resolved, still_open)
    assert identity.needs_manual_classification == 1
    assert identity.fills == 0
    assert identity.ok is True


def test_duplicate_order_ids_flags_repeated_id():
    entries = [
        {"type": "ORDER_IDS", "order_ids": {"instrument": "MNQ", "entry": "111"}},
        {"type": "ORDER_IDS", "order_ids": {"instrument": "MNQ", "entry": "111"}},
        {"type": "ORDER_IDS", "order_ids": {"instrument": "MNQ", "entry": "222"}},
    ]
    dupes = duplicate_order_ids(entries)
    assert len(dupes) == 1
    assert dupes[0]["order_id"] == "entry:111"
    assert dupes[0]["count"] == 2


def test_duplicate_order_ids_empty_when_all_unique():
    entries = [
        {"type": "ORDER_IDS", "order_ids": {"instrument": "MNQ", "entry": "111"}},
        {"type": "ORDER_IDS", "order_ids": {"instrument": "MNQ", "entry": "222"}},
    ]
    assert duplicate_order_ids(entries) == []


def test_gate_attrition_counts_failed_gates_on_non_trade_rows():
    entries = [
        {"decision": "TRADE", "setup": {"strategy": "orb_breakout"}, "failed_gates": []},
        {"decision": "NO_TRADE", "setup": {"strategy": "orb_breakout"}, "failed_gates": ["min_rr", "confluence"]},
        {"decision": "RISK_REJECTED", "setup": {"strategy": "orb_breakout"}, "failed_gates": ["min_rr"]},
    ]
    result = gate_attrition(entries)
    assert result["candidate_count"] == 3
    assert result["by_decision"] == {"TRADE": 1, "NO_TRADE": 1, "RISK_REJECTED": 1}
    assert result["failed_gate_counts"]["min_rr"] == 2
    assert result["failed_gate_counts"]["confluence"] == 1
