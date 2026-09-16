from __future__ import annotations

import hashlib
import json
from datetime import date

import scripts.mnq_missed_opportunity_producer as producer


def _write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_discovery_uses_only_requested_journal_file_window(tmp_path):
    ts = "2026-09-16T12:00:00+00:00"
    _write_jsonl(
        tmp_path / "bars_MNQ_2026-09-16.jsonl",
        [
            {
                "ts": ts,
                "timeframe": "15m",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
            }
        ],
    )
    qualifying = {
        "instrument": "MNQ",
        "decision": "NO_TRADE",
        "context": {"timestamp": ts, "timeframe": "15m"},
    }
    _write_jsonl(tmp_path / "journal_2026-09-01.jsonl", [qualifying])
    _write_jsonl(tmp_path / "journal_2026-09-16.jsonl", [qualifying])
    _write_jsonl(tmp_path / "journal_2026-09-17.jsonl", [qualifying])

    inputs = producer.discover_inputs(
        tmp_path,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 16),
    )
    assert [path.name for path in inputs.journal_files] == [
        "journal_2026-09-01.jsonl",
        "journal_2026-09-16.jsonl",
    ]
    assert len(inputs.journal_rows) == 2
    assert inputs.journal_parse_skips == 0


def test_output_hash_is_sha256_of_exact_jsonl_bytes(tmp_path):
    path = tmp_path / "rows.jsonl"
    rows = [
        {"cohort": "A", "sequence": 0, "filled": False, "pnl_dollars": None},
        {"cohort": "A", "sequence": 1, "filled": True, "pnl_dollars": 2.5},
    ]
    reported = producer.write_jsonl(path, rows)
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    assert reported == expected
