from __future__ import annotations

import json
from datetime import datetime, timezone

from journal.journal_logger import (
    JournalLogger,
    _MAX_PARSED_JOURNAL_CACHE_FILES,
)


def _write(path, *rows: dict) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_all_history_accounting_uses_compact_outcome_cache(tmp_path):
    JournalLogger._entries_cache.clear()
    JournalLogger._outcome_cache.clear()
    try:
        logger = JournalLogger(log_dir=str(tmp_path))
        for day in range(1, 13):
            result = "WIN" if day % 2 else "LOSS"
            pnl = 10.0 if result == "WIN" else -4.0
            _write(
                tmp_path / f"journal_2026-08-{day:02d}.jsonl",
                {"decision": "NO_TRADE", "payload": "x" * 10_000},
                {
                    "type": "OUTCOME",
                    "ts": f"2026-08-{day:02d}T16:00:00+00:00",
                    "outcome": {"result": result, "pnl_dollars": pnl},
                },
            )

        assert logger.get_account_balance(100.0) == 136.0
        assert logger.get_account_peak_balance(100.0) == 140.0
        assert logger.get_account_state_since(
            100.0, datetime(2026, 8, 7, tzinfo=timezone.utc)
        ) == (118.0, 122.0)
        stats = logger.get_performance_stats(100.0)
        assert stats["wins"] == 6
        assert stats["losses"] == 6

        assert JournalLogger._entries_cache == {}
        assert len(JournalLogger._outcome_cache) == 12
    finally:
        JournalLogger._entries_cache.clear()
        JournalLogger._outcome_cache.clear()


def test_full_entry_cache_is_lru_bounded_and_reparses_changes(tmp_path):
    JournalLogger._entries_cache.clear()
    JournalLogger._outcome_cache.clear()
    try:
        logger = JournalLogger(log_dir=str(tmp_path))
        paths = []
        for index in range(_MAX_PARSED_JOURNAL_CACHE_FILES + 3):
            path = tmp_path / f"journal_2026-07-{index + 1:02d}.jsonl"
            _write(path, {"decision": "NO_TRADE", "index": index})
            paths.append(path)
            assert logger._read_entries(path)[0]["index"] == index

        assert len(JournalLogger._entries_cache) == _MAX_PARSED_JOURNAL_CACHE_FILES
        assert str(paths[0]) not in JournalLogger._entries_cache
        assert str(paths[-1]) in JournalLogger._entries_cache

        outcome_path = tmp_path / "journal_2026-09-01.jsonl"
        _write(
            outcome_path,
            {
                "type": "OUTCOME",
                "ts": "2026-09-01T12:00:00+00:00",
                "outcome": {"result": "WIN", "pnl_dollars": 5.0},
            },
        )
        assert logger.get_account_balance(100.0) == 105.0
        with outcome_path.open("a") as handle:
            handle.write(
                json.dumps(
                    {
                        "type": "OUTCOME",
                        "ts": "2026-09-01T13:00:00+00:00",
                        "outcome": {"result": "LOSS", "pnl_dollars": -2.0},
                    }
                )
                + "\n"
            )
        assert logger.get_account_balance(100.0) == 103.0
    finally:
        JournalLogger._entries_cache.clear()
        JournalLogger._outcome_cache.clear()
