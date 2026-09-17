"""Regression tests for C8 journal market-condition normalization."""

from journal.journal_logger import JournalLogger


def test_log_decision_preserves_existing_top_level_market_condition(tmp_path):
    journal = JournalLogger(log_dir=str(tmp_path))
    journal.log_decision(
        {
            "ts": "2026-09-18T12:00:00+00:00",
            "decision": "NO_TRADE",
            "market_condition": "TRENDING",
            "context": {"market_condition": "CHOPPY"},
        }
    )

    row = journal.read_day()[0]
    assert row["market_condition"] == "TRENDING"


def test_log_decision_backfills_null_or_absent_top_level_from_context(tmp_path):
    journal = JournalLogger(log_dir=str(tmp_path))
    journal.log_decision(
        {
            "ts": "2026-09-18T12:15:00+00:00",
            "decision": "NO_TRADE",
            "market_condition": None,
            "context": {"market_condition": "RANGE_BOUND"},
        }
    )
    journal.log_decision(
        {
            "ts": "2026-09-18T12:30:00+00:00",
            "decision": "NO_TRADE",
            "context": {"market_condition": "CHOPPY"},
        }
    )

    rows = journal.read_day()
    assert rows[0]["market_condition"] == "RANGE_BOUND"
    assert rows[1]["market_condition"] == "CHOPPY"


def test_log_decision_leaves_null_or_absent_unchanged_without_context_value(tmp_path):
    journal = JournalLogger(log_dir=str(tmp_path))
    journal.log_decision(
        {
            "ts": "2026-09-18T12:45:00+00:00",
            "decision": "NO_TRADE",
            "market_condition": None,
            "context": {"market_condition": None},
        }
    )
    journal.log_decision(
        {
            "ts": "2026-09-18T13:00:00+00:00",
            "decision": "NO_TRADE",
            "context": {},
        }
    )

    rows = journal.read_day()
    assert "market_condition" in rows[0]
    assert rows[0]["market_condition"] is None
    assert "market_condition" not in rows[1]
