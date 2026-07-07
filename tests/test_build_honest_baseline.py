from __future__ import annotations

import json
from pathlib import Path

from ops.build_honest_baseline import (
    EXPECTED_OVERRIDE_COUNT,
    OVERRIDES,
    build_baseline,
    build_instrument_baseline,
)
from ops.proof_30_mnq import read_journal_entries


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _trade(ts: str, instrument: str, strategy: str = "orb_rejection") -> dict:
    return {
        "ts": ts,
        "instrument": instrument,
        "decision": "TRADE",
        "risk_check": {"result": "APPROVED"},
        "setup": {"direction": "LONG", "strategy": strategy, "entry": 100.0, "stop": 95.0, "target": 110.0, "contracts": 1},
    }


def _outcome(ts: str, instrument: str, *, result: str, exit_reason: str, pnl: float = 0.0) -> dict:
    return {
        "ts": ts,
        "type": "OUTCOME",
        "instrument": instrument,
        "outcome": {"result": result, "exit_reason": exit_reason, "pnl_dollars": pnl, "contracts": 1},
    }


def test_override_reclassifies_a_real_erased_win(tmp_path):
    key = ("MES", "2026-07-06T14:45:15.094051+00:00")
    assert key in OVERRIDES

    _write_jsonl(
        tmp_path / "journal_2026-07-06.jsonl",
        [
            _trade(key[1], "MES", "orb_breakout"),
            _outcome("2026-07-06T15:36:45+00:00", "MES", result="CANCELLED", exit_reason="phantom cleared"),
        ],
    )

    entries = read_journal_entries(tmp_path)
    report = build_instrument_baseline(entries, "MES")

    assert report["filled_wl_count"] == 1
    assert report["wins"] == 1
    assert report["net_pnl_dollars"] == 60.60
    assert report["cancelled_nofill_count"] == 0
    trade = report["trades"][0]
    assert trade["override_applied"] is True
    assert trade["result"] == "WIN"


def test_unoverridden_reconciler_row_stays_unclassified(tmp_path):
    _write_jsonl(
        tmp_path / "journal_2026-05-01.jsonl",
        [
            _trade("2026-05-01T10:00:00+00:00", "MES"),
            _outcome("2026-05-01T10:05:00+00:00", "MES", result="CANCELLED", exit_reason="phantom cleared"),
        ],
    )

    entries = read_journal_entries(tmp_path)
    report = build_instrument_baseline(entries, "MES")

    assert report["filled_wl_count"] == 0
    assert report["cancelled_nofill_count"] == 0
    assert report["still_unclassified_reconciler_touched_count"] == 1


def test_plain_win_and_cancelled_need_no_override(tmp_path):
    _write_jsonl(
        tmp_path / "journal_2026-06-23.jsonl",
        [
            _trade("2026-06-23T01:00:00+00:00", "MNQ"),
            _outcome("2026-06-23T01:15:00+00:00", "MNQ", result="WIN", exit_reason="TARGET_HIT", pnl=22.5),
            _trade("2026-06-23T02:00:00+00:00", "MNQ"),
            _outcome("2026-06-23T02:15:00+00:00", "MNQ", result="CANCELLED", exit_reason="execution_failed:CANCELLED"),
        ],
    )

    entries = read_journal_entries(tmp_path)
    report = build_instrument_baseline(entries, "MNQ")

    assert report["filled_wl_count"] == 1
    assert report["wins"] == 1
    assert report["cancelled_nofill_count"] == 1
    assert report["still_unclassified_reconciler_touched_count"] == 0
    assert all(not t["override_applied"] for t in report["trades"])


def test_build_baseline_reports_both_instruments_and_override_totals(tmp_path):
    _write_jsonl(
        tmp_path / "journal_2026-06-23.jsonl",
        [
            _trade("2026-06-23T01:00:00+00:00", "MNQ"),
            _outcome("2026-06-23T01:15:00+00:00", "MNQ", result="WIN", exit_reason="TARGET_HIT", pnl=22.5),
        ],
    )

    report = build_baseline(tmp_path)

    assert set(report["instruments"].keys()) == {"MNQ", "MES"}
    assert report["override_rows_defined"] == EXPECTED_OVERRIDE_COUNT == len(OVERRIDES)
    # None of the 23 known override keys are present in this synthetic fixture.
    assert report["override_rows_applied"] == 0
    assert report["override_count_matches_audit"] is False
