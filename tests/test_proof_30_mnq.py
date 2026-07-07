from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.proof_30_mnq import build_report, pair_resolved_trades, read_journal_entries
from ops.proof_30_mnq import classify_outcome


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


def _outcome(ts: str, pnl: float = 22.5, *, result: str = "WIN", exit_reason: str = "TARGET_HIT", instrument: str = "MNQ") -> dict:
    return {
        "ts": ts,
        "type": "OUTCOME",
        "instrument": instrument,
        "outcome": {
            "result": result,
            "exit_reason": exit_reason,
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


def test_classify_outcome_buckets():
    assert classify_outcome({"result": "WIN", "exit_reason": "TARGET_HIT"}) == "filled_win_loss"
    assert classify_outcome({"result": "LOSS", "exit_reason": "STOP_HIT"}) == "filled_win_loss"
    assert classify_outcome({"result": "BREAKEVEN", "exit_reason": "BE_STOP"}) == "breakeven"
    assert classify_outcome({"result": "CANCELLED", "exit_reason": "execution_failed:CANCELLED"}) == "cancelled_nofill"
    # Reconciler markers win regardless of the result the row happens to carry.
    assert classify_outcome({"result": "CANCELLED", "exit_reason": "auto-reconcile: phantom cleared"}) == "reconciler_touched"
    assert classify_outcome({"result": "WIN", "exit_reason": "naked flatten"}) == "reconciler_touched"
    assert classify_outcome({"result": "UNKNOWN", "exit_reason": ""}) == "other"


def test_filled_count_excludes_cancelled_and_reconciler(tmp_path):
    """The proof bar counts filled W/L only; CANCELLED no-fills and reconciler
    phantom-clears inflate the resolved superset but must not advance it.
    Mirrors the live 2026-07-06 box state (many CANCELLED, one real loss)."""
    _write_jsonl(
        tmp_path / "journal_2026-07-03.jsonl",
        [
            # 1 real filled LOSS
            _trade("2026-07-03T15:00:00+00:00", "orb_reclaim"),
            _outcome("2026-07-03T15:15:00+00:00", -59.0, result="LOSS", exit_reason="STOP_HIT"),
            # 2 no-fill CANCELLEDs
            _trade("2026-07-03T16:00:00+00:00", "orb_reclaim"),
            _outcome("2026-07-03T16:15:00+00:00", 0.0, result="CANCELLED", exit_reason="execution_failed:CANCELLED"),
            _trade("2026-07-03T17:00:00+00:00", "vwap_hold"),
            _outcome("2026-07-03T17:15:00+00:00", 0.0, result="CANCELLED", exit_reason="execution_failed:CANCELLED"),
            # 1 reconciler phantom-clear (needs broker verification, must not count)
            _trade("2026-07-03T18:00:00+00:00", "orb_rejection"),
            _outcome("2026-07-03T18:15:00+00:00", 0.0, result="CANCELLED", exit_reason="auto-reconcile: journal showed open but broker is flat (phantom cleared)"),
            # 1 filled WIN
            _trade("2026-07-03T19:00:00+00:00", "orb_reclaim"),
            _outcome("2026-07-03T19:15:00+00:00", 40.0, result="WIN", exit_reason="TARGET_HIT"),
            # MES exception-class row (the 07-06 template case) must never enter the MNQ tally
            {"ts": "2026-07-03T20:00:00+00:00", "instrument": "MES", "decision": "TRADE", "risk_check": {"result": "APPROVED"}, "setup": {"strategy": "orb_breakout"}},
            _outcome("2026-07-03T20:15:00+00:00", 60.6, result="CANCELLED", exit_reason="auto-reconcile: phantom cleared", instrument="MES"),
        ],
    )

    report = build_report(journal_dir=tmp_path, freeze_ts=None, limit=30, api_base=None)

    # Superset vs proof bar
    assert report["total_resolved_pairs"] == 5      # all MNQ pairs (MES excluded structurally)
    assert report["resolved_mnq_trades"] == 5       # backward-compat key unchanged
    assert report["filled_wl_count"] == 2           # only the LOSS + WIN
    assert report["cancelled_nofill_count"] == 2
    assert report["reconciler_touched_count"] == 1
    assert report["breakeven_count"] == 0
    assert report["filled_remaining_to_target"] == 28
    assert report["remaining_to_target"] == 25      # backward-compat: 30 - 5 resolved
    assert report["filled_wl_pnl_dollars"] == -19.0  # -59 + 40
    # MES exception row is structurally excluded from the MNQ report entirely.
    assert all(t["instrument"] == "MNQ" for t in report["trades"])
    categories = [t["category"] for t in report["trades"]]
    assert categories.count("filled_win_loss") == 2
    assert categories.count("reconciler_touched") == 1


def test_capped_scan_warns_when_filled_short(tmp_path):
    """If the resolved-pair cap is hit while filled is short, filled is a floor
    and the report must say so rather than silently under-report."""
    rows = []
    for i in range(4):
        rows.append(_trade(f"2026-07-03T{10+i:02d}:00:00+00:00", "orb_reclaim"))
        rows.append(_outcome(f"2026-07-03T{10+i:02d}:30:00+00:00", 0.0, result="CANCELLED", exit_reason="execution_failed:CANCELLED"))
    _write_jsonl(tmp_path / "journal_2026-07-03.jsonl", rows)

    report = build_report(journal_dir=tmp_path, freeze_ts=None, limit=3, api_base=None)

    assert report["total_resolved_pairs"] == 3      # capped at limit
    assert report["filled_wl_count"] == 0
    assert any("filled_wl_count is a floor" in w for w in report["warnings"])
