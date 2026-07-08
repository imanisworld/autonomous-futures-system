from __future__ import annotations

import json
from pathlib import Path

from ops.audit_plain_cancelled import audit_instrument
from ops.proof_30_mnq import read_journal_entries


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _trade(ts: str, instrument: str, *, direction: str, entry: float, close: float, strategy: str = "orb_rejection") -> dict:
    return {
        "ts": ts,
        "instrument": instrument,
        "decision": "TRADE",
        "risk_check": {"result": "APPROVED"},
        "setup": {"direction": direction, "strategy": strategy, "entry": entry, "stop": 0.0, "target": 0.0, "contracts": 1},
        "context": {"close": close},
    }


def _cancelled(ts: str, instrument: str) -> dict:
    return {
        "ts": ts,
        "type": "OUTCOME",
        "instrument": instrument,
        "outcome": {"result": "CANCELLED", "exit_reason": "execution_failed:CANCELLED", "pnl_dollars": 0.0, "contracts": 1},
    }


def test_clearly_unmarketable_row_confirmed_no_fill(tmp_path):
    # MES LONG, tolerance 4.0pt: cap = entry + 4.0. Close far beyond cap.
    _write_jsonl(
        tmp_path / "journal_2026-06-23.jsonl",
        [
            _trade("2026-06-23T01:00:00+00:00", "MES", direction="LONG", entry=7500.0, close=7520.0),
            _cancelled("2026-06-23T01:05:00+00:00", "MES"),
        ],
    )
    entries = read_journal_entries(tmp_path)
    report = audit_instrument(entries, "MES")

    assert report["plain_cancelled_total"] == 1
    assert report["classification_counts"] == {"CONFIRMED_NO_FILL": 1}


def test_marketable_but_cancelled_row_flagged_suspect(tmp_path):
    # MNQ SHORT, tolerance 8.0pt: cap = entry - 8.0. Close comfortably >= cap -> marketable.
    _write_jsonl(
        tmp_path / "journal_2026-06-23.jsonl",
        [
            _trade("2026-06-23T01:00:00+00:00", "MNQ", direction="SHORT", entry=30000.0, close=29995.0),
            _cancelled("2026-06-23T01:05:00+00:00", "MNQ"),
        ],
    )
    entries = read_journal_entries(tmp_path)
    report = audit_instrument(entries, "MNQ")

    assert report["classification_counts"] == {"MISLABELED_FILL_SUSPECT": 1}
    assert report["suspect_rows"][0]["margin_points"] == 3.0


def test_reconciler_touched_rows_are_excluded_not_double_counted(tmp_path):
    _write_jsonl(
        tmp_path / "journal_2026-06-23.jsonl",
        [
            _trade("2026-06-23T01:00:00+00:00", "MES", direction="LONG", entry=7500.0, close=7520.0),
            {
                "ts": "2026-06-23T01:05:00+00:00",
                "type": "OUTCOME",
                "instrument": "MES",
                "outcome": {"result": "CANCELLED", "exit_reason": "phantom cleared", "pnl_dollars": 0.0, "contracts": 1},
            },
        ],
    )
    entries = read_journal_entries(tmp_path)
    report = audit_instrument(entries, "MES")

    # reconciler-touched rows are the phantom-clear audit's domain, not this one
    assert report["plain_cancelled_total"] == 0


def test_missing_context_close_is_a_data_gap(tmp_path):
    trade = _trade("2026-06-23T01:00:00+00:00", "MES", direction="LONG", entry=7500.0, close=7520.0)
    del trade["context"]
    _write_jsonl(tmp_path / "journal_2026-06-23.jsonl", [trade, _cancelled("2026-06-23T01:05:00+00:00", "MES")])

    entries = read_journal_entries(tmp_path)
    report = audit_instrument(entries, "MES")

    assert report["classification_counts"] == {"DATA_GAP_EXCLUDED": 1}
