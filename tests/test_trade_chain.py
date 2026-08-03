"""Tests for ops.trade_chain — journal pairing and accounting-identity checks."""

from __future__ import annotations

import json
from pathlib import Path

from ops import trade_chain


def _write_journal(journal_dir: Path, day: str, rows: list[dict]) -> None:
    journal_dir.mkdir(parents=True, exist_ok=True)
    path = journal_dir / f"journal_{day}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _trade(ts: str, instrument: str, strategy: str = "orb_breakout", direction: str = "LONG") -> dict:
    return {
        "ts": ts,
        "instrument": instrument,
        "decision": "TRADE",
        "reason": "signal",
        "market_condition": "TRENDING",
        "setup": {
            "direction": direction, "entry": 100.0, "stop": 95.0, "target": 110.0,
            "rr_ratio": 2.0, "strategy": strategy,
        },
        "risk_check": {"result": "APPROVED", "failed_rule": None, "reason": None},
    }


def _outcome(ts: str, instrument: str, result: str, exit_reason: str | None = None, pnl_dollars: float = 0.0) -> dict:
    return {
        "ts": ts, "instrument": instrument, "type": "OUTCOME", "session": "new_york",
        "outcome": {"result": result, "exit_reason": exit_reason, "pnl_dollars": pnl_dollars},
    }


def _no_trade(ts: str, instrument: str, failed_rule: str | None = None) -> dict:
    return {
        "ts": ts, "instrument": instrument, "decision": "NO_TRADE", "reason": "no setup",
        "risk_check": {"result": "REJECTED", "failed_rule": failed_rule, "reason": "x"} if failed_rule else {},
    }


def test_build_report_win_no_fill_and_orphan(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    _write_journal(journal_dir, "2026-08-01", [
        _trade("2026-08-01T10:00:00Z", "MNQ"),
        _outcome("2026-08-01T10:15:00Z", "MNQ", "WIN", "TARGET_HIT", 100.0),
        _trade("2026-08-01T11:00:00Z", "MNQ"),
        _outcome("2026-08-01T11:05:00Z", "MNQ", "CANCELLED", "execution_failed:CANCELLED (ENTRY_NOT_FILLED)"),
        # Pending trade with no OUTCOME row at all, on an earlier date than
        # the window end -> should be classified as an orphan, not open.
        _trade("2026-08-01T12:00:00Z", "MNQ"),
    ])

    report = trade_chain.build_report(journal_dir, to_date="2026-08-02")

    accounting = report["accounting"]
    assert accounting["resolved_fills"] == 1
    assert accounting["no_fills"] == 1
    assert accounting["other_cancellations"] == 0
    assert accounting["orphaned_pending"] == 1
    assert accounting["legitimately_open"] == 0
    assert accounting["attempts"] == 2  # fill + no-fill; the orphan isn't an attempt outcome
    assert accounting["fills"] == 1
    assert accounting["identity_ok"] is True
    # Orphan pending + zero-order-id filled trade -> chain not fully clean.
    assert report["ok"] is False
    assert len(report["orphaned_pending"]) == 1


def test_build_report_legitimately_open_position(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    _write_journal(journal_dir, "2026-08-01", [
        _trade("2026-08-01T15:00:00Z", "MES", direction="SHORT"),
    ])

    report = trade_chain.build_report(journal_dir, to_date="2026-08-01")
    accounting = report["accounting"]
    assert accounting["legitimately_open"] == 1
    assert accounting["orphaned_pending"] == 0
    assert accounting["fills"] == 1
    assert accounting["resolved_fills"] == 0


def test_build_report_strategy_filter_scopes_pairs(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    _write_journal(journal_dir, "2026-08-01", [
        _trade("2026-08-01T10:00:00Z", "MNQ", strategy="orb_breakout"),
        _outcome("2026-08-01T10:15:00Z", "MNQ", "WIN", "TARGET_HIT", 50.0),
        _trade("2026-08-01T11:00:00Z", "MNQ", strategy="vwap_hold"),
        _outcome("2026-08-01T11:15:00Z", "MNQ", "LOSS", "STOP_HIT", -25.0),
    ])

    orb_report = trade_chain.build_report(journal_dir, strategy="orb_breakout", to_date="2026-08-02")
    assert orb_report["accounting"]["resolved_fills"] == 1
    assert orb_report["pair_count"] == 1

    vwap_report = trade_chain.build_report(journal_dir, strategy="vwap_hold", to_date="2026-08-02")
    assert vwap_report["accounting"]["resolved_fills"] == 1


def test_build_report_read_error_is_surfaced(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir(parents=True)
    (journal_dir / "journal_2026-08-01.jsonl").write_text("{not valid json\n", encoding="utf-8")

    report = trade_chain.build_report(journal_dir)
    assert report["ok"] is False
    assert len(report["read_errors"]) == 1


def test_decision_and_risk_rejection_counts(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    _write_journal(journal_dir, "2026-08-01", [
        _no_trade("2026-08-01T09:00:00Z", "MNQ", failed_rule="max_stop_distance"),
        _no_trade("2026-08-01T09:15:00Z", "MNQ", failed_rule="max_stop_distance"),
        _no_trade("2026-08-01T09:30:00Z", "MNQ"),
        _trade("2026-08-01T10:00:00Z", "MNQ"),
        _outcome("2026-08-01T10:15:00Z", "MNQ", "WIN", "TARGET_HIT", 10.0),
    ])

    report = trade_chain.build_report(journal_dir, to_date="2026-08-02")
    assert report["decision_counts"]["NO_TRADE"] == 3
    assert report["decision_counts"]["TRADE"] == 1
    assert report["risk_rejections"]["max_stop_distance"] == 2
    assert report["market_condition_counts"].get("TRENDING") == 1


def test_bracket_protection_flags_naked_and_stale(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    _write_journal(journal_dir, "2026-08-01", [
        _trade("2026-08-01T10:00:00Z", "MNQ"),
        _outcome("2026-08-01T10:15:00Z", "MNQ", "WIN", "TARGET_HIT", 10.0),
        {"ts": "2026-08-01T09:00:00Z", "instrument": "MES", "type": "ORDER_IDS", "order_ids": ["a", "b", "c"]},
    ])

    report = trade_chain.build_report(journal_dir, to_date="2026-08-02")
    protection = report["protection"]
    # MNQ filled with zero ORDER_IDS rows anywhere in the window.
    assert "MNQ" in protection["possible_naked_positions"]
    # MES has an ORDER_IDS row but no OUTCOME anywhere in the window.
    assert any(item["instrument"] == "MES" for item in protection["possible_stale_protective_orders"])


def test_format_summary_line_pass(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    _write_journal(journal_dir, "2026-08-01", [])
    report = trade_chain.build_report(journal_dir, to_date="2026-08-01")
    line = trade_chain.format_summary_line(report)
    assert line.startswith("TRADE CHAIN: PASS")
    assert "0 attempts" in line
