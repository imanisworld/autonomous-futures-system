"""No-fill taxonomy fields on OUTCOME journal entries: additive, backward-compatible.

Proves:
- existing (no-new-kwargs) log_outcome calls still work and default the new
  fields to null
- the new fields round-trip correctly when supplied
- downstream consumers (fill_realism.is_entry_nofill, proof_30_mnq.classify_outcome)
  are unaffected by the new fields — no metric drift from this change
"""
from __future__ import annotations

import json
from datetime import date

from journal.journal_logger import JournalLogger
from ops.fill_realism import is_entry_nofill
from ops.proof_30_mnq import classify_outcome


def _read_last_outcome(log_dir, for_date):
    path = log_dir / f"journal_{for_date.isoformat()}.jsonl"
    lines = path.read_text().splitlines()
    for line in reversed(lines):
        record = json.loads(line)
        if record.get("type") == "OUTCOME":
            return record["outcome"]
    raise AssertionError("no OUTCOME entry found")


def test_legacy_call_without_new_kwargs_defaults_new_fields_to_none(tmp_path):
    journal = JournalLogger(log_dir=str(tmp_path))
    today = date(2026, 7, 7)
    journal.log_outcome(
        instrument="MES",
        session="new_york",
        result="CANCELLED",
        entry_price=7570.0,
        exit_price=None,
        exit_reason="execution_failed:CANCELLED",
        pnl_ticks=0.0,
        pnl_dollars=0.0,
        contracts=1,
        for_date=today,
    )
    outcome = _read_last_outcome(tmp_path, today)
    assert outcome["result"] == "CANCELLED"
    assert outcome["exit_reason"] == "execution_failed:CANCELLED"
    for key in (
        "no_fill_reason", "order_type", "broker_status_raw", "strategy",
        "signal_timestamp", "submit_timestamp", "cancel_timestamp",
        "seconds_until_cancel", "requested_entry", "last_price_at_submit",
        "last_price_at_cancel", "best_bid_at_submit", "best_ask_at_submit",
        "ticks_moved_from_entry",
    ):
        assert outcome[key] is None, key


def test_new_fields_round_trip_when_supplied(tmp_path):
    journal = JournalLogger(log_dir=str(tmp_path))
    today = date(2026, 7, 7)
    journal.log_outcome(
        instrument="MES",
        session="new_york",
        result="CANCELLED",
        entry_price=7570.0,
        exit_price=None,
        exit_reason="execution_failed:CANCELLED",
        pnl_ticks=0.0,
        pnl_dollars=0.0,
        contracts=1,
        for_date=today,
        no_fill_reason="NO_FILL_LIMIT_TOO_PASSIVE",
        order_type="Limit",
        broker_status_raw="ENTRY_NOT_FILLED",
        strategy="orb_reclaim",
        signal_timestamp="2026-07-07T20:30:00+00:00",
        submit_timestamp="2026-07-07T20:30:01.500000+00:00",
        cancel_timestamp="2026-07-07T20:30:02.100000+00:00",
        seconds_until_cancel=0.6,
        requested_entry=7570.0,
    )
    outcome = _read_last_outcome(tmp_path, today)
    assert outcome["no_fill_reason"] == "NO_FILL_LIMIT_TOO_PASSIVE"
    assert outcome["order_type"] == "Limit"
    assert outcome["broker_status_raw"] == "ENTRY_NOT_FILLED"
    assert outcome["strategy"] == "orb_reclaim"
    assert outcome["seconds_until_cancel"] == 0.6
    assert outcome["last_price_at_submit"] is None  # not supplied -> stays null


def test_downstream_no_fill_classifier_unaffected_by_new_fields():
    # fill_realism.is_entry_nofill still matches on result+exit_reason only;
    # the new fields are additive and must not change this classification.
    assert is_entry_nofill("CANCELLED", "execution_failed:CANCELLED") is True
    assert is_entry_nofill("WIN", "TARGET_HIT") is False


def test_downstream_proof_classifier_unaffected_by_new_fields():
    outcome_body = {
        "result": "CANCELLED",
        "exit_reason": "execution_failed:CANCELLED",
        "no_fill_reason": "NO_FILL_PRICE_MOVED_AWAY",
        "order_type": "Limit",
    }
    assert classify_outcome(outcome_body) == "cancelled_nofill"
