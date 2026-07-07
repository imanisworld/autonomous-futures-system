from __future__ import annotations

import json
from pathlib import Path

from ops.reconciler_outcome_audit import (
    build_audit_report,
    is_reconciler_touched_outcome,
    load_operator_overrides,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _trade(ts: str, *, instrument: str = "MNQ", strategy: str = "orb_reclaim") -> dict:
    return {
        "ts": ts,
        "instrument": instrument,
        "decision": "TRADE",
        "risk_check": {"result": "APPROVED"},
        "setup": {
            "direction": "LONG",
            "strategy": strategy,
            "entry": 30000.0,
            "stop": 29990.0,
            "target": 30025.0,
            "contracts": 1,
        },
    }


def _outcome(
    ts: str,
    *,
    instrument: str = "MNQ",
    session: str = "regular",
    result: str = "CANCELLED",
    exit_reason: str = "auto-reconcile: journal showed open but broker is flat (phantom cleared)",
    pnl: float = 0.0,
) -> dict:
    return {
        "ts": ts,
        "type": "OUTCOME",
        "instrument": instrument,
        "session": session,
        "outcome": {
            "result": result,
            "entry_price": 30000.0,
            "exit_price": None,
            "exit_reason": exit_reason,
            "pnl_ticks": 0.0,
            "pnl_dollars": pnl,
            "contracts": 1,
        },
    }


def _override_doc(path: Path) -> Path:
    path.write_text(
        """# Proof Operator Overrides

## 2026-07-06 - MES broker-verified win misbooked as CANCELLED

- Instrument: `MES`
- Session date: `2026-07-06`

### Operator ruling

Count this event as a broker-verified resolved win for manual proof review.
""",
        encoding="utf-8",
    )
    return path


def test_load_operator_overrides_extracts_matching_hints(tmp_path):
    overrides = load_operator_overrides(_override_doc(tmp_path / "overrides.md"))

    assert len(overrides) == 1
    assert overrides[0].instrument == "MES"
    assert overrides[0].session_date == "2026-07-06"
    assert "broker-verified resolved win" in (overrides[0].ruling or "")


def test_touched_detection_uses_session_or_reason_markers():
    assert is_reconciler_touched_outcome(
        _outcome("2026-07-07T12:00:00+00:00", session="reconcile", result="WIN", exit_reason="TARGET_HIT")
    )
    assert is_reconciler_touched_outcome(
        _outcome("2026-07-07T12:00:00+00:00", exit_reason="NAKED_BRACKET_AUTO_FLATTENED")
    )
    assert not is_reconciler_touched_outcome(
        _outcome("2026-07-07T12:00:00+00:00", result="WIN", exit_reason="TARGET_HIT")
    )


def test_audit_groups_classified_override_completed_reconcile_and_unaudited(tmp_path):
    _write_jsonl(
        tmp_path / "journal_2026-07-06.jsonl",
        [
            # Existing docs/proof-operator-overrides.md style exception.
            _trade("2026-07-06T14:45:00+00:00", instrument="MES"),
            _outcome("2026-07-06T15:36:45+00:00", instrument="MES"),
            # Post-fix reconciler completed-trade resolve; already broker-derived.
            _trade("2026-07-06T16:00:00+00:00", instrument="MNQ", strategy="orb_rejection"),
            _outcome(
                "2026-07-06T16:20:00+00:00",
                instrument="MNQ",
                session="reconcile",
                result="WIN",
                exit_reason="TARGET_HIT",
                pnl=42.5,
            ),
            # Still needs broker follow-up.
            _trade("2026-07-06T17:00:00+00:00", instrument="MNQ", strategy="vwap_hold"),
            _outcome("2026-07-06T17:20:00+00:00", instrument="MNQ"),
            # Naked/auto-flatten outcome should be inventoried too.
            _trade("2026-07-06T18:00:00+00:00", instrument="NQ"),
            _outcome(
                "2026-07-06T18:00:03+00:00",
                instrument="NQ",
                exit_reason="NAKED_BRACKET_AUTO_FLATTENED",
            ),
        ],
    )

    report = build_audit_report(
        journal_dir=tmp_path,
        overrides_doc=_override_doc(tmp_path / "overrides.md"),
    )

    assert report["read_only"] is True
    assert report["summary"]["total_touched"] == 4
    assert report["summary"]["classified"] == 2
    assert report["summary"]["unaudited"] == 2
    assert report["summary"]["by_instrument"] == {"MES": 1, "MNQ": 2, "NQ": 1}
    assert report["summary"]["by_marker"]["session:reconcile"] == 1
    assert report["summary"]["by_marker"]["phantom"] == 2
    assert report["summary"]["by_marker"]["naked"] == 1

    classified_ids = {item["instrument"]: item for item in report["classified"]}
    assert classified_ids["MES"]["operator_overrides"][0]["session_date"] == "2026-07-06"
    assert classified_ids["MES"]["classification_source"] == "operator_override"
    assert classified_ids["MES"]["needs_broker_verification"] is False
    assert classified_ids["MNQ"]["classification_reason"].startswith("reconciler resolved")
    assert classified_ids["MNQ"]["classification_source"] == "completed_trade_reconcile"

    unaudited = {(item["instrument"], item["trade"]["strategy"]) for item in report["unaudited"]}
    assert unaudited == {("MNQ", "vwap_hold"), ("NQ", "orb_reclaim")}
    assert all(item["broker_follow_up"]["verify"] for item in report["unaudited"])
    assert all(item["needs_broker_verification"] is True for item in report["unaudited"])
    assert all("journal_2026-07-06.jsonl:" in item["operator_override_fields"]["journal_location"] for item in report["unaudited"])


def test_audit_date_filter_applies_to_journal_rows(tmp_path):
    _write_jsonl(
        tmp_path / "journal_2026-07-05.jsonl",
        [
            _trade("2026-07-05T12:00:00+00:00"),
            _outcome("2026-07-05T12:20:00+00:00"),
        ],
    )
    _write_jsonl(
        tmp_path / "journal_2026-07-06.jsonl",
        [
            _trade("2026-07-06T12:00:00+00:00"),
            _outcome("2026-07-06T12:20:00+00:00"),
        ],
    )

    report = build_audit_report(journal_dir=tmp_path, overrides_doc=None, from_date="2026-07-06")

    assert report["summary"]["total_touched"] == 1
    assert report["unaudited"][0]["outcome_date"] == "2026-07-06"


def test_audit_reports_journal_read_errors(tmp_path):
    (tmp_path / "journal_2026-07-06.jsonl").write_text(
        json.dumps(_trade("2026-07-06T12:00:00+00:00")) + "\n"
        "{not-json}\n"
        + json.dumps(_outcome("2026-07-06T12:20:00+00:00")) + "\n",
        encoding="utf-8",
    )

    report = build_audit_report(journal_dir=tmp_path, overrides_doc=None)

    assert report["ok"] is False
    assert report["summary"]["journal_read_errors"] == 1
    assert report["journal_read_errors"][0]["reason"] == "invalid_json"
    assert report["summary"]["total_touched"] == 1
