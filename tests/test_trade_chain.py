from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from ops.trade_chain import (
    accounting_identity,
    pair_trades,
    trade_chain_report,
)


def _trade(ts: str, instrument: str = "MNQ", strategy: str = "strat_22_reversal", stop=100.0, target=110.0) -> dict:
    return {
        "decision": "TRADE",
        "instrument": instrument,
        "ts": ts,
        "risk_check": {"result": "APPROVED"},
        "setup": {"direction": "LONG", "strategy": strategy, "entry": 105.0, "stop": stop, "target": target},
    }


def _outcome(ts: str, result: str, instrument: str = "MNQ", *, exit_reason: str = "TARGET_HIT", pnl: float = 10.0, no_fill_reason=None, order_id=None) -> dict:
    body = {"result": result, "exit_reason": exit_reason, "pnl_dollars": pnl}
    if no_fill_reason:
        body["no_fill_reason"] = no_fill_reason
    if order_id:
        body["paper_order_id"] = order_id
    return {"type": "OUTCOME", "instrument": instrument, "ts": ts, "outcome": body}


def _rejected(ts: str, instrument: str = "MNQ", strategy: str = "strat_22_reversal") -> dict:
    return {
        "decision": "RISK_REJECTED",
        "instrument": instrument,
        "ts": ts,
        "risk_check": {"result": "REJECTED", "reason": "MAX_TRADES_PER_DAY"},
        "setup": {"strategy": strategy},
    }


def _write_journal(journal_dir: Path, day: str, rows: list[dict]) -> None:
    journal_dir.mkdir(parents=True, exist_ok=True)
    path = journal_dir / f"journal_{day}.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_pair_trades_resolves_matching_trade_and_outcome():
    entries = [
        _trade("2026-08-01T10:00:00Z"),
        _outcome("2026-08-01T10:30:00Z", "WIN"),
    ]
    pairing = pair_trades(entries)
    assert len(pairing["resolved"]) == 1
    assert pairing["still_open"] == []
    assert pairing["unmatched_outcomes"] == []


def test_pair_trades_leaves_unmatched_trade_open():
    entries = [_trade("2026-08-01T10:00:00Z")]
    pairing = pair_trades(entries)
    assert pairing["still_open"] == [entries[0]]
    assert pairing["resolved"] == []


def test_pair_trades_flags_orphan_outcome():
    entries = [_outcome("2026-08-01T10:30:00Z", "WIN")]
    pairing = pair_trades(entries)
    assert len(pairing["unmatched_outcomes"]) == 1
    assert pairing["resolved"] == []


def test_pair_trades_separates_rejects_from_attempts():
    entries = [_rejected("2026-08-01T09:00:00Z"), _trade("2026-08-01T10:00:00Z"), _outcome("2026-08-01T10:30:00Z", "WIN")]
    pairing = pair_trades(entries)
    assert len(pairing["rejected"]) == 1
    assert len(pairing["resolved"]) == 1


def test_pair_trades_filters_by_instrument_and_strategy():
    entries = [
        _trade("2026-08-01T10:00:00Z", instrument="MES", strategy="trend_consolidation_break"),
        _outcome("2026-08-01T10:30:00Z", "WIN", instrument="MES"),
        _trade("2026-08-01T11:00:00Z", instrument="MNQ", strategy="strat_22_reversal"),
        _outcome("2026-08-01T11:30:00Z", "LOSS", instrument="MNQ"),
    ]
    pairing = pair_trades(entries, instrument="MNQ")
    assert len(pairing["resolved"]) == 1
    assert pairing["resolved"][0][0]["instrument"] == "MNQ"

    pairing = pair_trades(entries, strategy="trend_consolidation_break")
    assert len(pairing["resolved"]) == 1
    assert pairing["resolved"][0][0]["setup"]["strategy"] == "trend_consolidation_break"


def test_accounting_identity_holds_for_mixed_outcomes():
    entries = [
        _trade("2026-08-01T09:00:00Z"),
        _outcome("2026-08-01T09:30:00Z", "WIN"),
        _trade("2026-08-01T10:00:00Z"),
        _outcome("2026-08-01T10:30:00Z", "CANCELLED", no_fill_reason="NO_FILL_PRICE_MOVED_AWAY"),
        _trade("2026-08-01T11:00:00Z"),  # still open
    ]
    pairing = pair_trades(entries)
    accounting = accounting_identity(pairing)
    assert accounting["fills"] == 2  # 1 resolved win + 1 still open
    assert accounting["cancellations_or_no_fill"] == 1
    assert accounting["attempts"] == 3
    assert accounting["legitimately_open"] == 1
    assert accounting["identity_holds"]["attempts_eq_fills_plus_cancellations"] is True
    assert accounting["identity_holds"]["fills_eq_resolved_filled_plus_open"] is True
    assert accounting["no_fill_reason_breakdown"] == {"NO_FILL_PRICE_MOVED_AWAY": 1}


def test_trade_chain_report_flags_orphan_outcome_as_problem(tmp_path):
    _write_journal(tmp_path, "2026-08-01", [_outcome("2026-08-01T10:30:00Z", "WIN")])
    report = trade_chain_report(tmp_path)
    assert report["ok"] is False
    assert len(report["orphan_outcomes"]) == 1
    assert any("orphan" in problem for problem in report["problems"])


def test_trade_chain_report_flags_duplicate_order_ids(tmp_path):
    rows = [
        _trade("2026-08-01T09:00:00Z"),
        _outcome("2026-08-01T09:30:00Z", "WIN", order_id="ORD-1"),
        _trade("2026-08-01T10:00:00Z"),
        _outcome("2026-08-01T10:30:00Z", "LOSS", order_id="ORD-1"),
    ]
    _write_journal(tmp_path, "2026-08-01", rows)
    report = trade_chain_report(tmp_path)
    assert report["ok"] is False
    assert report["duplicate_order_ids"] == ["ORD-1"]


def test_trade_chain_report_flags_naked_fill(tmp_path):
    rows = [
        _trade("2026-08-01T09:00:00Z", stop=None, target=None),
        _outcome("2026-08-01T09:30:00Z", "WIN"),
    ]
    _write_journal(tmp_path, "2026-08-01", rows)
    report = trade_chain_report(tmp_path)
    assert report["ok"] is False
    assert len(report["naked_fills"]) == 1


def test_trade_chain_report_flags_day_only_violation(tmp_path):
    rows = [_trade("2026-08-01T09:00:00Z", strategy="strat_322_first_live")]
    _write_journal(tmp_path, "2026-08-01", rows)
    report = trade_chain_report(tmp_path, today=date(2026, 8, 2))
    assert report["ok"] is False
    assert len(report["day_only_violations"]) == 1


def test_trade_chain_report_clean_case_passes(tmp_path):
    rows = [
        _trade("2026-08-01T09:00:00Z"),
        _outcome("2026-08-01T09:30:00Z", "WIN"),
        _trade("2026-08-01T10:00:00Z"),
        _outcome("2026-08-01T10:30:00Z", "CANCELLED", no_fill_reason="NO_FILL_LIMIT_TOO_PASSIVE"),
    ]
    _write_journal(tmp_path, "2026-08-01", rows)
    report = trade_chain_report(tmp_path, today=date(2026, 8, 1))
    assert report["ok"] is True
    assert report["problems"] == []
    assert report["broker_journal_parity"]["status"] == "UNKNOWN"


def test_format_trade_chain_summary_pass_and_fail(tmp_path):
    from ops.trade_chain import format_trade_chain_summary

    rows = [_trade("2026-08-01T09:00:00Z"), _outcome("2026-08-01T09:30:00Z", "WIN")]
    _write_journal(tmp_path, "2026-08-01", rows)
    passing = trade_chain_report(tmp_path, today=date(2026, 8, 1))
    text = format_trade_chain_summary(passing)
    assert text.startswith("TRADE CHAIN: PASS")

    _write_journal(tmp_path, "2026-08-02", [_outcome("2026-08-02T09:30:00Z", "WIN")])
    failing = trade_chain_report(tmp_path)
    failing_text = format_trade_chain_summary(failing)
    assert failing_text.startswith("TRADE CHAIN: FAIL")
    assert "Problems:" in failing_text
