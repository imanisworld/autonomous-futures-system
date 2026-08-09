from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ops.trade_chain_audit import build_trade_chain_report

NOW = datetime(2026, 8, 9, 20, 0, tzinfo=timezone.utc)


def _write_journal(tmp_path: Path, day: str, rows: list[dict]) -> None:
    path = tmp_path / f"journal_{day}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _trade(ts, instrument="MNQ", strategy="orb_breakout", direction="LONG", stop=100.0, target=110.0):
    return {
        "ts": ts,
        "instrument": instrument,
        "decision": "TRADE",
        "risk_check": {"result": "APPROVED"},
        "setup": {
            "strategy": strategy,
            "direction": direction,
            "entry": 105.0,
            "stop": stop,
            "target": target,
        },
    }


def _order_ids(ts, instrument="MNQ", order_id="12345678"):
    return {
        "ts": ts,
        "type": "ORDER_IDS",
        "instrument": instrument,
        "order_ids": {"instrument": instrument, "entry_order_id": order_id},
    }


def _outcome(ts, instrument="MNQ", result="WIN", exit_reason="target_hit", pnl=100.0):
    return {
        "ts": ts,
        "type": "OUTCOME",
        "instrument": instrument,
        "outcome": {"result": result, "exit_reason": exit_reason, "pnl_dollars": pnl},
    }


def test_pass_case_resolved_and_no_fill(tmp_path):
    rows = [
        _trade("2026-08-09T14:00:00Z"),
        _order_ids("2026-08-09T14:00:01Z", order_id="AAA"),
        _outcome("2026-08-09T14:30:00Z", result="WIN"),
        _trade("2026-08-09T15:00:00Z"),
        _outcome("2026-08-09T15:05:00Z", result="CANCELLED", exit_reason="ENTRY_NOT_FILLED"),
    ]
    _write_journal(tmp_path, "2026-08-09", rows)

    report = build_trade_chain_report(journal_dir=tmp_path, now=NOW)

    assert report["overall"] == "PASS"
    assert report["totals"]["attempts"] == 2
    assert report["totals"]["resolved_fills"] == 1
    assert report["totals"]["no_fills"] == 1
    assert report["accounting"]["ok"] is True


def test_legitimately_open_within_window(tmp_path):
    rows = [
        _trade("2026-08-09T19:30:00Z"),
        _order_ids("2026-08-09T19:30:01Z", order_id="BBB"),
    ]
    _write_journal(tmp_path, "2026-08-09", rows)

    report = build_trade_chain_report(journal_dir=tmp_path, now=NOW)

    assert report["totals"]["legitimately_open"] == 1
    assert report["totals"]["stale_unresolved"] == 0
    assert report["overall"] == "PASS"


def test_stale_unresolved_flags_and_fails(tmp_path):
    old_ts = "2026-08-01T10:00:00Z"  # far more than 20h before NOW
    rows = [
        _trade(old_ts),
        _order_ids(old_ts, order_id="CCC"),
    ]
    _write_journal(tmp_path, "2026-08-01", rows)

    report = build_trade_chain_report(journal_dir=tmp_path, now=NOW)

    assert report["totals"]["stale_unresolved"] == 1
    assert report["overall"] == "FAIL"


def test_no_order_attempt_logged_fails(tmp_path):
    rows = [_trade("2026-08-09T14:00:00Z")]  # no ORDER_IDS, no OUTCOME
    _write_journal(tmp_path, "2026-08-09", rows)

    report = build_trade_chain_report(journal_dir=tmp_path, now=NOW)

    assert report["totals"]["no_order_attempt_logged"] == 1
    assert report["overall"] == "FAIL"


def test_duplicate_order_ids_detected(tmp_path):
    rows = [
        _trade("2026-08-09T14:00:00Z"),
        _order_ids("2026-08-09T14:00:01Z", order_id="DUPLICATE"),
        _outcome("2026-08-09T14:30:00Z", result="WIN"),
        _trade("2026-08-09T15:00:00Z"),
        _order_ids("2026-08-09T15:00:01Z", order_id="DUPLICATE"),
        _outcome("2026-08-09T15:30:00Z", result="LOSS"),
    ]
    _write_journal(tmp_path, "2026-08-09", rows)

    report = build_trade_chain_report(journal_dir=tmp_path, now=NOW)

    assert report["duplicate_order_ids"] == ["DUPLICATE"]
    assert report["overall"] == "FAIL"


def test_naked_position_flag_when_bracket_fields_missing(tmp_path):
    rows = [
        _trade("2026-08-09T14:00:00Z", stop=None, target=None),
        _order_ids("2026-08-09T14:00:01Z", order_id="EEE"),
        _outcome("2026-08-09T14:30:00Z", result="WIN"),
    ]
    _write_journal(tmp_path, "2026-08-09", rows)

    report = build_trade_chain_report(journal_dir=tmp_path, now=NOW)

    assert report["naked_position_flags"] == 1
    assert report["overall"] == "FAIL"


def test_reconciler_touched_outcome_is_counted_not_hidden(tmp_path):
    rows = [
        _trade("2026-08-09T14:00:00Z"),
        _order_ids("2026-08-09T14:00:01Z", order_id="FFF"),
        _outcome(
            "2026-08-09T14:30:00Z",
            result="LOSS",
            exit_reason="auto-reconcile phantom position cleared",
        ),
    ]
    _write_journal(tmp_path, "2026-08-09", rows)

    report = build_trade_chain_report(journal_dir=tmp_path, now=NOW)

    assert report["reconciler_touched_count"] == 1
    assert report["totals"]["resolved_fills"] == 1


def test_never_writes_to_journal_dir(tmp_path):
    rows = [
        _trade("2026-08-09T14:00:00Z"),
        _order_ids("2026-08-09T14:00:01Z", order_id="GGG"),
        _outcome("2026-08-09T14:30:00Z", result="WIN"),
    ]
    _write_journal(tmp_path, "2026-08-09", rows)
    before = sorted(p.name for p in tmp_path.iterdir())

    build_trade_chain_report(journal_dir=tmp_path, now=NOW)

    after = sorted(p.name for p in tmp_path.iterdir())
    assert before == after
