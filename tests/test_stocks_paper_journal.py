"""
tests/test_stocks_paper_journal.py

stocks_advisory/paper_journal.py tests. Proves append-only persistence,
correct dedup-key lookups (has_decision_for / latest_record_for /
latest_open_positions), fail-closed handling of a corrupt journal, and
no Robinhood/broker/execution/futures/options_manager coupling.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import stocks_advisory.paper_journal as paper_journal_module
from stocks_advisory.paper_journal import (
    PaperJournalError,
    PaperJournalRecord,
    append_record,
    has_decision_for,
    latest_open_positions,
    latest_record_for,
    read_all_records,
)


def _record(trade_date: str, strategy_version: str = "v1", status: str = "watching", **overrides) -> PaperJournalRecord:
    base = dict(
        trade_date=trade_date,
        strategy_version=strategy_version,
        recorded_at="2026-07-06T10:35:00-04:00",
        data_source="test-fixture",
        signal_symbol="QQQ",
        qqq_price=100.0,
        direction="long_tqqq",
        vehicle_symbol="TQQQ",
        decision="TRADE",
        reason="QQQ broke above first-hour high and holds above VWAP",
        entry_trigger="QQQ above first-hour high 100.00 and above VWAP",
        stop_price=99.5,
        status=status,
    )
    base.update(overrides)
    return PaperJournalRecord(**base)


def test_append_and_read_round_trip(tmp_path):
    path = tmp_path / "journal.jsonl"
    record = _record("2026-07-06")
    append_record(path, record)
    records = read_all_records(path)
    assert len(records) == 1
    assert records[0] == record


def test_missing_file_reads_as_empty(tmp_path):
    path = tmp_path / "does_not_exist.jsonl"
    assert read_all_records(path) == []
    assert has_decision_for(path, "2026-07-06", "v1") is False
    assert latest_open_positions(path, "v1") == []


def test_append_only_preserves_full_history_in_order(tmp_path):
    path = tmp_path / "journal.jsonl"
    r1 = _record("2026-07-06", status="watching")
    r2 = _record("2026-07-06", status="active", modeled_entry_price=101.0)
    r3 = _record("2026-07-06", status="exited", modeled_exit_price=102.0)
    for r in (r1, r2, r3):
        append_record(path, r)
    records = read_all_records(path)
    assert len(records) == 3
    assert [r.status for r in records] == ["watching", "active", "exited"]
    # confirm the file itself is append-only line count, never truncated
    assert len(path.read_text(encoding="utf-8").splitlines()) == 3


def test_has_decision_for_true_only_for_matching_key(tmp_path):
    path = tmp_path / "journal.jsonl"
    append_record(path, _record("2026-07-06", strategy_version="v1"))
    assert has_decision_for(path, "2026-07-06", "v1") is True
    assert has_decision_for(path, "2026-07-06", "v2") is False
    assert has_decision_for(path, "2026-07-07", "v1") is False


def test_latest_record_for_returns_most_recent(tmp_path):
    path = tmp_path / "journal.jsonl"
    append_record(path, _record("2026-07-06", status="watching"))
    append_record(path, _record("2026-07-06", status="active", modeled_entry_price=101.0))
    latest = latest_record_for(path, "2026-07-06", "v1")
    assert latest.status == "active"
    assert latest.modeled_entry_price == 101.0


def test_latest_record_for_returns_none_when_never_journaled(tmp_path):
    path = tmp_path / "journal.jsonl"
    assert latest_record_for(path, "2026-07-06", "v1") is None


def test_latest_open_positions_filters_status_and_strategy_version(tmp_path):
    path = tmp_path / "journal.jsonl"
    append_record(path, _record("2026-07-01", status="watching"))
    append_record(path, _record("2026-07-02", status="active"))
    append_record(path, _record("2026-07-03", status="exited"))
    append_record(path, _record("2026-07-04", status="invalidated"))
    append_record(path, _record("2026-07-05", status="no_trade"))
    append_record(path, _record("2026-07-06", status="watching", strategy_version="v2"))

    open_positions = latest_open_positions(path, "v1")
    open_dates = {r.trade_date for r in open_positions}
    assert open_dates == {"2026-07-01", "2026-07-02"}


def test_latest_open_positions_uses_latest_status_per_date(tmp_path):
    path = tmp_path / "journal.jsonl"
    append_record(path, _record("2026-07-06", status="watching"))
    append_record(path, _record("2026-07-06", status="exited"))  # resolved on a later run
    open_positions = latest_open_positions(path, "v1")
    assert open_positions == []


def test_corrupt_json_line_raises(tmp_path):
    path = tmp_path / "journal.jsonl"
    append_record(path, _record("2026-07-06"))
    with open(path, "a", encoding="utf-8") as f:
        f.write("{not valid json\n")
    with pytest.raises(PaperJournalError):
        read_all_records(path)


def test_line_with_unexpected_field_raises(tmp_path):
    path = tmp_path / "journal.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"trade_date": "2026-07-06", "bogus_field": 1}) + "\n")
    with pytest.raises(PaperJournalError):
        read_all_records(path)


def test_line_missing_required_field_raises(tmp_path):
    path = tmp_path / "journal.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"trade_date": "2026-07-06"}) + "\n")
    with pytest.raises(PaperJournalError):
        read_all_records(path)


def test_non_object_line_raises(tmp_path):
    path = tmp_path / "journal.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps([1, 2, 3]) + "\n")
    with pytest.raises(PaperJournalError):
        read_all_records(path)


def test_no_broker_execution_futures_options_manager_import():
    source = Path(paper_journal_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_prefixes = ("broker", "execution", "futures", "options_manager", "robinhood")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.lower().startswith(forbidden_prefixes), alias.name
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").lower()
            assert not module.startswith(forbidden_prefixes), module
