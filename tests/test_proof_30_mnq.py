from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.proof_30_mnq import build_report, pair_resolved_trades, read_journal_entries


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _trade(ts: str, strategy: str = "orb_rejection") -> dict:
    return {
        "ts": ts,
        "instrument": "MNQ",
        "decision": "TRADE",
        "risk_check": {"result": "APPROVED"},
        "setup": {
            "direction": "SHORT",
            "strategy": strategy,
            "entry": 29805.25,
            "stop": 29807.25,
            "target": 29790.25,
            "contracts": 1,
        },
    }


def _outcome(ts: str, pnl: float = 22.5) -> dict:
    return {
        "ts": ts,
        "type": "OUTCOME",
        "instrument": "MNQ",
        "outcome": {
            "result": "WIN",
            "exit_reason": "TARGET_HIT",
            "entry_price": 29801.5,
            "exit_price": 29790.25,
            "pnl_dollars": pnl,
            "contracts": 1,
        },
    }


def test_pairs_resolved_mnq_trades_after_freeze(tmp_path):
    journal = tmp_path / "journal_2026-06-23.jsonl"
    _write_jsonl(
        journal,
        [
            _trade("2026-06-23T01:30:00+00:00", "vwap_hold"),
            _outcome("2026-06-23T01:45:00+00:00", 37.0),
            {"ts": "2026-06-23T02:00:00+00:00", "instrument": "MES", "decision": "TRADE", "risk_check": {"result": "APPROVED"}},
            _trade("2026-06-23T15:15:00+00:00"),
            _outcome("2026-06-23T15:30:00+00:00", 22.5),
        ],
    )

    entries = read_journal_entries(tmp_path)
    resolved, unmatched = pair_resolved_trades(
        entries,
        freeze_ts=datetime(2026, 6, 23, 12, 0, tzinfo=timezone.utc),
        limit=30,
    )

    assert len(resolved) == 1
    assert unmatched == []
    assert resolved[0].setup["strategy"] == "orb_rejection"
    assert resolved[0].outcome_body["pnl_dollars"] == 22.5


def test_build_report_reports_errors_log_and_status_payloads(tmp_path):
    _write_jsonl(tmp_path / "journal_2026-06-23.jsonl", [_trade("2026-06-23T15:15:00+00:00"), _outcome("2026-06-23T15:30:00+00:00")])
    (tmp_path / "errors.log").write_text("write failed once\n", encoding="utf-8")
    status = tmp_path / "status.json"
    broker = tmp_path / "broker.json"
    status.write_text(json.dumps({"trade_count": 1, "wins": 1, "losses": 0, "journal_path": "logs/journal_2026-06-23.jsonl"}), encoding="utf-8")
    broker.write_text(json.dumps({"ok": True, "env": "demo", "realized_pnl": 20.0, "open_pnl": 0.0, "position": None}), encoding="utf-8")

    report = build_report(
        journal_dir=tmp_path,
        freeze_ts=None,
        limit=30,
        api_base=None,
        status_json=status,
        broker_json=broker,
    )

    assert report["resolved_mnq_trades"] == 1
    assert report["resolved_trades"] == 1
    assert report["remaining_to_target"] == 29
    assert report["journal_pnl_dollars"] == 22.5
    assert "Replay output" in report["source_of_truth_rule"]
    assert report["errors_log"]["exists"] is True
    assert report["errors_log"]["lines"] == 1
    assert report["status_today"]["journal_path"] == "logs/journal_2026-06-23.jsonl"
    assert report["broker_realized_pnl"] == 20.0
