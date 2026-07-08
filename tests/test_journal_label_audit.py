from __future__ import annotations

import json
from pathlib import Path

from ops.journal_label_audit import build_audit, format_report


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def _setup(direction: str = "LONG") -> dict:
    return {
        "direction": direction,
        "strategy": "orb_reclaim",
        "entry": 30000.0,
        "stop": 29990.0,
        "target": 30025.0,
        "contracts": 1,
    }


def test_audit_flags_misleading_decision_risk_and_outcome_labels(tmp_path):
    journal = _write_jsonl(
        tmp_path / "journal_2026-07-08.jsonl",
        [
            {
                "ts": "2026-07-08T13:00:00+00:00",
                "instrument": "MNQ",
                "decision": "TRADE",
                "risk_check": {"result": "REJECTED", "reason": "session cutoff"},
                "setup": _setup(),
            },
            {
                "ts": "2026-07-08T13:05:00+00:00",
                "instrument": "MNQ",
                "decision": "RISK_REJECTED",
                "risk_check": {"result": "APPROVED"},
                "setup": _setup("SHORT"),
            },
            {
                "ts": "2026-07-08T13:10:00+00:00",
                "instrument": "MNQ",
                "decision": "TRADE",
                "setup": _setup(),
            },
            {
                "ts": "2026-07-08T13:15:00+00:00",
                "instrument": "MNQ",
                "type": "OUTCOME",
                "decision": "TRADE",
                "outcome": {"result": "WIN", "exit_reason": "TARGET_HIT"},
            },
            {
                "ts": "2026-07-08T13:20:00+00:00",
                "instrument": "MNQ",
                "decision": "NO_TRADE",
                "risk_check": {"result": "APPROVED"},
            },
        ],
    )

    report = build_audit(paths=[journal])

    assert report["read_only"] is True
    assert report["summary"]["files_scanned"] == 1
    assert report["summary"]["rows_scanned"] == 5
    assert report["summary"]["issues_by_code"] == {
        "non_trade_with_approved_risk": 1,
        "outcome_row_has_decision": 1,
        "risk_rejected_without_rejected_risk": 1,
        "trade_missing_risk_check": 1,
        "trade_with_rejected_risk": 1,
    }
    assert report["summary"]["issues_by_severity"] == {"error": 2, "warning": 3}
    assert {issue["code"] for issue in report["issues"]} == set(report["summary"]["issues_by_code"])

    text = format_report(report)
    assert "Journal Label Consistency Audit" in text
    assert "trade_with_rejected_risk" in text
    assert "risk_rejected_without_rejected_risk" in text


def test_audit_accepts_valid_trade_risk_reject_and_outcome_rows(tmp_path):
    journal = _write_jsonl(
        tmp_path / "journal_2026-07-08.jsonl",
        [
            {
                "ts": "2026-07-08T14:00:00+00:00",
                "instrument": "MNQ",
                "decision": "TRADE",
                "risk_check": {"result": "APPROVED"},
                "setup": _setup(),
            },
            {
                "ts": "2026-07-08T14:05:00+00:00",
                "instrument": "MNQ",
                "decision": "RISK_REJECTED",
                "reason": "session cutoff",
                "risk_check": {"result": "REJECTED", "failed_rule": "session_cutoff"},
                "failed_gates": ["session_cutoff"],
                "setup": _setup("SHORT"),
            },
            {
                "ts": "2026-07-08T14:10:00+00:00",
                "instrument": "MNQ",
                "type": "OUTCOME",
                "outcome": {"result": "CANCELLED", "exit_reason": "execution_failed:CANCELLED"},
            },
        ],
    )

    report = build_audit(paths=[journal])

    assert report["summary"]["issue_count"] == 0
    assert report["issues"] == []
    assert "No label consistency issues found." in format_report(report)


def test_audit_reports_parse_errors(tmp_path):
    journal = tmp_path / "journal_2026-07-08.jsonl"
    journal.write_text('{"decision": "TRADE"}\nnot-json\n', encoding="utf-8")

    report = build_audit(paths=[journal])

    assert report["summary"]["rows_scanned"] == 2
    assert report["summary"]["issues_by_code"]["journal_read_error"] == 1
    assert report["issues"][0]["code"] == "trade_missing_risk_check"
    assert report["issues"][1]["code"] == "journal_read_error"
