from __future__ import annotations

import json
from pathlib import Path

from ops.project_check_promotion import (
    _execution_accounting,
    _gate_attrition,
    _performance,
    _strategy_no_trade_and_near_miss,
    build_promotion_report,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _trade(ts: str, *, strategy: str = "orb_breakout", instrument: str = "MNQ", risk: str = "APPROVED", direction: str = "LONG") -> dict:
    row = {
        "ts": ts,
        "instrument": instrument,
        "decision": "TRADE",
        "risk_check": {"result": risk},
        "setup": {"strategy": strategy, "direction": direction},
    }
    if risk == "REJECTED":
        row["risk_check"]["reason"] = "max_trades_per_day"
    return row


def _outcome(ts: str, *, result: str, instrument: str = "MNQ", pnl: float | None = None, no_fill_reason: str | None = None, strategy: str = "orb_breakout") -> dict:
    body = {"result": result, "strategy": strategy, "exit_reason": "target_hit" if result == "WIN" else "stop_hit" if result == "LOSS" else "execution_failed:CANCELLED"}
    if pnl is not None:
        body["pnl_dollars"] = pnl
    if no_fill_reason:
        body["no_fill_reason"] = no_fill_reason
    return {"ts": ts, "type": "OUTCOME", "instrument": instrument, "outcome": body}


def test_gate_attrition_counts_failed_gates_for_strategy() -> None:
    rows = _strategy_no_trade_and_near_miss(
        entries=[
            {"decision": "NO_TRADE", "setup": {"strategy": "orb_breakout"}, "failed_gates": ["MARKET_CONDITION_NOT_TRENDING"]},
            {"decision": "NO_TRADE", "setup": {"strategy": "other_strategy"}, "failed_gates": ["RR_TOO_LOW"]},
            {"decision": "NO_TRADE", "shadow_candidates": [{"strategy": "orb_breakout"}], "failed_gates": ["CONFLUENCE_MISSING"]},
        ],
        strategy="orb_breakout",
    )
    attrition = _gate_attrition(rows)
    assert attrition["candidate_bars_blocked_pre_risk"] == 2
    assert attrition["failed_gate_counts"] == {"MARKET_CONDITION_NOT_TRENDING": 1, "CONFLUENCE_MISSING": 1}


def test_execution_accounting_identity_balances_for_mixed_outcomes() -> None:
    entries = [
        _trade("2026-08-01T14:00:00Z"),
        _outcome("2026-08-01T14:30:00Z", result="WIN", pnl=100),
        _trade("2026-08-01T15:00:00Z"),
        _outcome("2026-08-01T15:30:00Z", result="CANCELLED", no_fill_reason="NO_FILL_PRICE_MOVED_AWAY"),
        _trade("2026-08-01T16:00:00Z", risk="REJECTED"),
    ]
    accounting = _execution_accounting(entries, "orb_breakout")
    assert accounting["candidates_reaching_risk_engine"] == 3
    assert accounting["candidates_approved"] == 2
    assert accounting["candidates_rejected_by_risk_engine"] == 1
    assert accounting["fills_resolved_closed"] == 1
    assert accounting["cancellations_no_fill"] == 1
    assert accounting["zero_executable_fills"] is False
    assert accounting["accounting_mismatch"] is False
    for identity in accounting["accounting_identities"]:
        assert identity["matches"] is True


def test_execution_accounting_flags_zero_fills() -> None:
    entries = [
        _trade("2026-08-01T14:00:00Z"),
        _outcome("2026-08-01T14:30:00Z", result="CANCELLED", no_fill_reason="NO_FILL_PRICE_MOVED_AWAY"),
    ]
    accounting = _execution_accounting(entries, "orb_breakout")
    assert accounting["zero_executable_fills"] is True


def test_execution_accounting_flags_orphan_from_prior_day() -> None:
    entries = [_trade("2020-01-01T14:00:00Z")]  # approved, never resolved, ancient date
    accounting = _execution_accounting(entries, "orb_breakout")
    assert len(accounting["orphans_unresolved_prior_day"]) == 1


def test_performance_computes_win_rate_and_pnl() -> None:
    entries = [
        _trade("2026-08-01T14:00:00Z"),
        _outcome("2026-08-01T14:30:00Z", result="WIN", pnl=100),
        _trade("2026-08-01T15:00:00Z"),
        _outcome("2026-08-01T15:30:00Z", result="LOSS", pnl=-40),
    ]
    perf = _performance(entries, "orb_breakout")
    assert perf["resolved_filled_trades"] == 2
    assert perf["net_pnl_dollars"] == 60
    assert perf["win_rate_pct"] == 50.0
    assert perf["wins"] == 1
    assert perf["losses"] == 1


def test_performance_reports_no_trades_message_when_empty() -> None:
    perf = _performance([], "orb_breakout")
    assert perf["resolved_filled_trades"] == 0
    assert "note" in perf


def test_build_promotion_report_end_to_end(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    _write_jsonl(
        log_dir / "journal_2026-08-01.jsonl",
        [
            _trade("2026-08-01T14:00:00Z"),
            _outcome("2026-08-01T14:30:00Z", result="WIN", pnl=100),
        ],
    )
    report = build_promotion_report("orb_breakout", log_dir=log_dir, days=30, repo_root=Path.cwd())
    assert report["strategy"] == "orb_breakout"
    assert report["classification"] in {
        "VALIDATED", "PROMISING BUT UNPROVEN", "BROKEN", "OVERFIT", "UNSAFE", "WAIT",
    }
    assert report["execution_accounting"]["fills_resolved_closed"] == 1
    assert "no runtime change" in report["rules_note"].lower() or "no runtime" in report["rules_note"].lower()


def test_build_promotion_report_never_writes_journal(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    journal_path = log_dir / "journal_2026-08-01.jsonl"
    _write_jsonl(journal_path, [_trade("2026-08-01T14:00:00Z")])
    before = journal_path.read_text()
    build_promotion_report("orb_breakout", log_dir=log_dir, days=30, repo_root=Path.cwd())
    after = journal_path.read_text()
    assert before == after
