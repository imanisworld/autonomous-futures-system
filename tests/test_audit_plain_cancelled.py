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


def _cancelled(
    ts: str,
    instrument: str,
    *,
    no_fill_reason: str | None = None,
    broker_status_raw: str | None = None,
    signal_timestamp: str | None = None,
    submit_timestamp: str | None = None,
    cancel_timestamp: str | None = None,
    seconds_until_cancel: float | None = None,
) -> dict:
    return {
        "ts": ts,
        "type": "OUTCOME",
        "instrument": instrument,
        "outcome": {
            "result": "CANCELLED",
            "exit_reason": "execution_failed:CANCELLED",
            "pnl_dollars": 0.0,
            "contracts": 1,
            "no_fill_reason": no_fill_reason,
            "broker_status_raw": broker_status_raw,
            "signal_timestamp": signal_timestamp,
            "submit_timestamp": submit_timestamp,
            "cancel_timestamp": cancel_timestamp,
            "seconds_until_cancel": seconds_until_cancel,
        },
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


def test_pre_taxonomy_suspect_row_is_not_flagged_option_c(tmp_path):
    # Marketable-per-arithmetic (MNQ SHORT, cap = entry - 8.0, close comfortably
    # below cap) but dated well before the 2026-07-07T18:35:33Z taxonomy deploy.
    _write_jsonl(
        tmp_path / "journal_2026-06-25.jsonl",
        [
            _trade("2026-06-25T01:00:00+00:00", "MNQ", direction="SHORT", entry=30000.0, close=29995.0),
            _cancelled("2026-06-25T01:05:00+00:00", "MNQ"),
        ],
    )
    entries = read_journal_entries(tmp_path)
    report = audit_instrument(entries, "MNQ")

    row = report["suspect_rows"][0]
    assert row["post_taxonomy"] is False
    assert row["option_c_recurrence"] is False
    assert report["option_c_recurrence_rows"] == []


def test_post_taxonomy_suspect_with_unknown_reason_flags_option_c_recurrence(tmp_path):
    # Same marketable-per-arithmetic signature, but the OUTCOME row postdates
    # the taxonomy deploy and still has no explanatory no_fill_reason -- the
    # exact anomaly the operator asked this tool to watch for.
    _write_jsonl(
        tmp_path / "journal_2026-07-08.jsonl",
        [
            _trade("2026-07-08T02:15:00+00:00", "MNQ", direction="SHORT", entry=30000.0, close=29995.0),
            _cancelled("2026-07-08T02:20:00+00:00", "MNQ", no_fill_reason="NO_FILL_UNKNOWN"),
        ],
    )
    entries = read_journal_entries(tmp_path)
    report = audit_instrument(entries, "MNQ")

    row = report["suspect_rows"][0]
    assert row["post_taxonomy"] is True
    assert row["option_c_recurrence"] is True
    assert report["option_c_recurrence_rows"] == [row]
    assert report["post_taxonomy_total"] == 1


def test_post_taxonomy_suspect_with_real_reason_does_not_flag_option_c(tmp_path):
    # Taxonomy actually explained this one with a specific bucket, so it is
    # not the "generic cancel with no explanation" recurrence signature.
    _write_jsonl(
        tmp_path / "journal_2026-07-08.jsonl",
        [
            _trade("2026-07-08T02:15:00+00:00", "MNQ", direction="SHORT", entry=30000.0, close=29995.0),
            _cancelled("2026-07-08T02:20:00+00:00", "MNQ", no_fill_reason="NO_FILL_BROKER_REJECTED"),
        ],
    )
    entries = read_journal_entries(tmp_path)
    report = audit_instrument(entries, "MNQ")

    row = report["suspect_rows"][0]
    assert row["post_taxonomy"] is True
    assert row["option_c_recurrence"] is False
    assert report["option_c_recurrence_rows"] == []


def test_confirmed_no_fill_row_never_flagged_option_c_even_if_post_taxonomy(tmp_path):
    # Genuinely unmarketable, post-taxonomy, no reason populated -- should
    # stay CONFIRMED_NO_FILL and never be flagged, since option_c_recurrence
    # only applies to the MISLABELED_FILL_SUSPECT classification.
    _write_jsonl(
        tmp_path / "journal_2026-07-08.jsonl",
        [
            _trade("2026-07-08T02:15:00+00:00", "MES", direction="LONG", entry=7500.0, close=7520.0),
            _cancelled("2026-07-08T02:20:00+00:00", "MES"),
        ],
    )
    entries = read_journal_entries(tmp_path)
    report = audit_instrument(entries, "MES")

    row = report["all_rows"][0]
    assert row["classification"] == "CONFIRMED_NO_FILL"
    assert row["post_taxonomy"] is True
    assert row["option_c_recurrence"] is False


def test_signal_to_submit_latency_computed_when_present(tmp_path):
    _write_jsonl(
        tmp_path / "journal_2026-07-08.jsonl",
        [
            _trade("2026-07-08T02:15:00+00:00", "MES", direction="LONG", entry=7500.0, close=7520.0),
            _cancelled(
                "2026-07-08T02:20:00+00:00", "MES",
                signal_timestamp="2026-07-08T02:15:00+00:00",
                submit_timestamp="2026-07-08T02:15:01.500000+00:00",
            ),
        ],
    )
    entries = read_journal_entries(tmp_path)
    report = audit_instrument(entries, "MES")

    row = report["all_rows"][0]
    assert row["signal_to_submit_latency_seconds"] == 1.5


def test_signal_to_submit_latency_none_when_fields_missing(tmp_path):
    _write_jsonl(
        tmp_path / "journal_2026-06-23.jsonl",
        [
            _trade("2026-06-23T01:00:00+00:00", "MES", direction="LONG", entry=7500.0, close=7520.0),
            _cancelled("2026-06-23T01:05:00+00:00", "MES"),
        ],
    )
    entries = read_journal_entries(tmp_path)
    report = audit_instrument(entries, "MES")

    assert report["all_rows"][0]["signal_to_submit_latency_seconds"] is None
