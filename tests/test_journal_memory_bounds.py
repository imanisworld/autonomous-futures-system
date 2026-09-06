from __future__ import annotations

import json
from datetime import datetime, timezone

import journal.journal_logger as journal_logger_module
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


def _accounting(logger):
    """Every all-history view, as one comparable tuple."""
    return (
        logger.get_account_balance(100.0),
        logger.get_account_peak_balance(100.0),
        logger.get_account_state_since(100.0, datetime(2026, 8, 5, tzinfo=timezone.utc)),
        logger.get_performance_stats(100.0),
    )


def _seed_days(tmp_path, days: int, outcomes_per_day: int = 1) -> None:
    for day in range(1, days + 1):
        rows = [{"decision": "NO_TRADE", "payload": "x" * 200}]
        for n in range(outcomes_per_day):
            result = "WIN" if (day + n) % 2 else "LOSS"
            rows.append(
                {
                    "type": "OUTCOME",
                    "ts": f"2026-08-{day:02d}T{(n % 12) + 8:02d}:00:00+00:00",
                    "outcome": {"result": result, "pnl_dollars": 10.0 if result == "WIN" else -4.0},
                }
            )
        _write(tmp_path / f"journal_2026-08-{day:02d}.jsonl", *rows)


def test_outcome_cache_is_bounded_by_file_ceiling(tmp_path, monkeypatch):
    """The cache used to grow one entry per journal forever."""
    JournalLogger._entries_cache.clear()
    JournalLogger._outcome_cache.clear()
    try:
        monkeypatch.setattr(journal_logger_module, "_MAX_OUTCOME_CACHE_FILES", 5)
        logger = JournalLogger(log_dir=str(tmp_path))
        _seed_days(tmp_path, 20)
        logger.get_account_balance(100.0)
        # exactly at the ceiling, not merely under it (and not empty)
        assert len(JournalLogger._outcome_cache) == 5
    finally:
        JournalLogger._entries_cache.clear()
        JournalLogger._outcome_cache.clear()


def test_outcome_cache_is_bounded_by_row_ceiling(tmp_path, monkeypatch):
    JournalLogger._entries_cache.clear()
    JournalLogger._outcome_cache.clear()
    try:
        monkeypatch.setattr(journal_logger_module, "_MAX_OUTCOME_CACHE_ROWS", 10)
        logger = JournalLogger(log_dir=str(tmp_path))
        _seed_days(tmp_path, 8, outcomes_per_day=4)
        logger.get_account_balance(100.0)
        rows = sum(
            len(s["account"]) + len(s["performance"])
            for _sig, s in JournalLogger._outcome_cache.values()
        )
        assert rows <= 10
        # never evicts down to nothing, even if one file alone exceeds the cap
        assert len(JournalLogger._outcome_cache) >= 1
    finally:
        JournalLogger._entries_cache.clear()
        JournalLogger._outcome_cache.clear()


def test_single_oversized_journal_is_returned_but_not_retained(tmp_path, monkeypatch):
    """A file bigger than the row ceiling must be returned and NOT cached.

    Retaining it would leave one entry permanently over budget that eviction
    could not remove, so the "bounded" total would be unbounded in exactly the
    pathological case the ceiling exists for.
    """
    JournalLogger._entries_cache.clear()
    JournalLogger._outcome_cache.clear()
    try:
        monkeypatch.setattr(journal_logger_module, "_MAX_OUTCOME_CACHE_ROWS", 2)
        logger = JournalLogger(log_dir=str(tmp_path))
        _seed_days(tmp_path, 3, outcomes_per_day=6)
        first = _accounting(logger)
        second = _accounting(logger)
        assert first == second, "repeat reads without caching must agree"
        assert JournalLogger._outcome_cache == {}, "oversized summaries must not be retained"
    finally:
        JournalLogger._entries_cache.clear()
        JournalLogger._outcome_cache.clear()


def test_row_ceiling_is_a_hard_bound_with_one_pathological_journal(tmp_path, monkeypatch):
    """The ceiling must hold even when a single journal alone exceeds it."""
    JournalLogger._entries_cache.clear()
    JournalLogger._outcome_cache.clear()
    try:
        monkeypatch.setattr(journal_logger_module, "_MAX_OUTCOME_CACHE_ROWS", 12)
        logger = JournalLogger(log_dir=str(tmp_path))
        _seed_days(tmp_path, 4, outcomes_per_day=2)          # small files, 4 rows each
        pathological = tmp_path / "journal_2026-08-09.jsonl"
        _write(
            pathological,
            *[
                {
                    "type": "OUTCOME",
                    "ts": f"2026-08-09T{8 + (n % 12):02d}:00:00+00:00",
                    "outcome": {"result": "WIN", "pnl_dollars": 1.0},
                }
                for n in range(500)
            ],
        )
        logger.get_account_balance(100.0)
        rows = sum(
            len(s["account"]) + len(s["performance"])
            for _sig, s in JournalLogger._outcome_cache.values()
        )
        assert rows <= 12, f"row ceiling breached: {rows}"
        assert str(pathological) not in JournalLogger._outcome_cache
        # and the uncached pathological file's rows are still counted in full:
        # 100 opening + 500 (500 x +1.0) + 24 (four seeded days netting +6 each)
        assert logger.get_account_balance(100.0) == 624.0
    finally:
        JournalLogger._entries_cache.clear()
        JournalLogger._outcome_cache.clear()


class _RacyDict(dict):
    """Evicts the key inside get(), standing in for a concurrent evictor."""

    def get(self, key, default=None):
        value = super().get(key, default)
        super().pop(key, None)
        return value


def test_cache_hit_refresh_survives_concurrent_eviction(tmp_path):
    """The recency refresh must not KeyError if the entry vanishes under it."""
    JournalLogger._entries_cache.clear()
    original = JournalLogger._outcome_cache
    JournalLogger._outcome_cache = {}
    try:
        logger = JournalLogger(log_dir=str(tmp_path))
        _seed_days(tmp_path, 1)
        path = tmp_path / "journal_2026-08-01.jsonl"
        first = logger._read_outcome_summary(path)
        JournalLogger._outcome_cache = _RacyDict(JournalLogger._outcome_cache)
        again = logger._read_outcome_summary(path)   # bare pop() would raise here
        assert again == first
        # the deliberately-evicted entry must not be resurrected
        assert str(path) not in JournalLogger._outcome_cache
    finally:
        JournalLogger._outcome_cache = original
        JournalLogger._entries_cache.clear()
        JournalLogger._outcome_cache.clear()


def test_eviction_never_changes_an_accounting_result(tmp_path, monkeypatch):
    """The invariant that matters: bounding is a memory change, not a math change."""
    JournalLogger._entries_cache.clear()
    JournalLogger._outcome_cache.clear()
    try:
        logger = JournalLogger(log_dir=str(tmp_path))
        _seed_days(tmp_path, 20, outcomes_per_day=3)

        # Ceilings far above the data: nothing is ever evicted.
        unbounded = _accounting(logger)
        assert len(JournalLogger._outcome_cache) == 20

        # Same data, ceilings low enough to force heavy eviction on every sweep.
        JournalLogger._outcome_cache.clear()
        monkeypatch.setattr(journal_logger_module, "_MAX_OUTCOME_CACHE_FILES", 3)
        monkeypatch.setattr(journal_logger_module, "_MAX_OUTCOME_CACHE_ROWS", 4)
        bounded = _accounting(logger)

        assert bounded == unbounded
        assert len(JournalLogger._outcome_cache) <= 3
    finally:
        JournalLogger._entries_cache.clear()
        JournalLogger._outcome_cache.clear()


def test_outcome_cache_hit_refreshes_recency(tmp_path, monkeypatch):
    JournalLogger._entries_cache.clear()
    JournalLogger._outcome_cache.clear()
    try:
        monkeypatch.setattr(journal_logger_module, "_MAX_OUTCOME_CACHE_FILES", 2)
        logger = JournalLogger(log_dir=str(tmp_path))
        _seed_days(tmp_path, 2)
        first = tmp_path / "journal_2026-08-01.jsonl"
        logger.get_account_balance(100.0)
        # Touch the older file so it becomes most-recently-used...
        logger._read_outcome_summary(first)
        assert list(JournalLogger._outcome_cache)[-1] == str(first)
        # ...then a third file must evict the OTHER one, not the refreshed file.
        _seed_days(tmp_path, 3)
        logger._read_outcome_summary(tmp_path / "journal_2026-08-03.jsonl")
        assert str(first) in JournalLogger._outcome_cache
    finally:
        JournalLogger._entries_cache.clear()
        JournalLogger._outcome_cache.clear()


def test_entries_cache_hit_refresh_survives_concurrent_eviction(tmp_path):
    """Same defect class as the outcome cache: a bare pop() could KeyError.

    `_entries_cache` is process-wide, so another caller can evict a key between
    this reader's get() and its recency-refresh pop().
    """
    JournalLogger._outcome_cache.clear()
    original = JournalLogger._entries_cache
    JournalLogger._entries_cache = {}
    try:
        logger = JournalLogger(log_dir=str(tmp_path))
        path = tmp_path / "journal_2026-08-01.jsonl"
        _write(path, {"decision": "NO_TRADE", "index": 0})
        first = logger._read_entries(path)
        assert first[0]["index"] == 0
        JournalLogger._entries_cache = _RacyDict(JournalLogger._entries_cache)
        again = logger._read_entries(path)   # bare pop() would raise here
        assert again == first
        # an entry another caller deliberately evicted must not be resurrected
        assert str(path) not in JournalLogger._entries_cache
    finally:
        JournalLogger._entries_cache = original
        JournalLogger._entries_cache.clear()
        JournalLogger._outcome_cache.clear()
