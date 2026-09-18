from __future__ import annotations

import json
from pathlib import Path

from scripts.broker_fill_calibration import audit


def _write(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _external_fill(instrument: str, client_id: str, adverse: float, signed: float) -> dict:
    return {
        "ts": "2026-09-17T14:30:01+00:00",
        "decision": "TRADE",
        "instrument": instrument,
        "client_order_id": client_id,
        "setup": {"strategy": "orb_reclaim"},
        "execution_audit": {
            "post_fill_validation": {
                "accepted": True,
                "requested_entry": 100.0,
                "actual_entry": 100.0 + signed * 0.25,
                "slippage_ticks": signed,
                "adverse_slippage_ticks": adverse,
                "failed_checks": [],
            }
        },
    }


def test_exact_external_fills_are_measured_and_paper_is_excluded(tmp_path: Path) -> None:
    rows = [
        _external_fill("MNQ", "AFS-a", 0.0, -1.0),
        _external_fill("MNQ", "AFS-b", 1.0, 1.0),
        _external_fill("MNQ", "AFS-c", 3.0, 3.0),
        {
            **_external_fill("MNQ", "AFS-paper", 9.0, 9.0),
            "paper_order_id": "PAPER-1",
        },
    ]
    report = audit([_write(tmp_path / "journal_2026-09-17.jsonl", rows)], source_mode="demo")

    assert report["status"] == "MEASURED"
    assert report["source_mode_declared"] == "demo"
    assert report["source_mode_independently_proven_by_journal"] is False
    assert report["paper_trade_rows_excluded"] == 1
    mnq = report["by_instrument"]["MNQ"]
    assert mnq["exact_fills"] == 3
    assert mnq["adverse_slippage_ticks"]["median"] == 1.0
    assert mnq["adverse_slippage_ticks"]["p95_nearest_rank"] == 3.0
    assert report["automatic_fill_model_or_config_change_authorized"] is False


def test_cancelled_external_attempt_is_not_misclassified_as_fill(tmp_path: Path) -> None:
    rows = [
        {
            "ts": "2026-09-17T14:30:02+00:00",
            "type": "OUTCOME",
            "instrument": "MES",
            "outcome": {
                "result": "CANCELLED",
                "client_order_id": "AFS-no-fill",
                "strategy": "orb_reclaim",
                "no_fill_reason": "NO_FILL_LIMIT_TOO_PASSIVE",
                "order_type": "Limit",
                "broker_status_raw": "ENTRY_NOT_FILLED",
                "seconds_until_cancel": 0.6,
                "requested_entry": 6000.0,
            },
        }
    ]
    report = audit([_write(tmp_path / "journal.jsonl", rows)])

    mes = report["by_instrument"]["MES"]
    assert mes["exact_fills"] == 0
    assert mes["external_cancelled_no_fills"] == 1
    assert mes["classified_no_fill_rate"] == 1.0
    assert mes["no_fill_taxonomy_coverage"] == 1.0


def test_external_confirmed_trade_without_exact_post_fill_audit_stays_unknown(tmp_path: Path) -> None:
    rows = [
        {
            "ts": "2026-09-17T14:30:02+00:00",
            "decision": "TRADE",
            "instrument": "MNQ",
            "client_order_id": "AFS-unknown",
            "setup": {"strategy": "orb_reclaim", "entry": 30000.0},
        }
    ]
    report = audit([_write(tmp_path / "journal.jsonl", rows)])

    assert report["overall"]["exact_fills"] == 0
    assert report["overall"]["external_confirmed_trades_missing_exact_fill_audit"] == 1
    assert len(report["external_trades_missing_exact_fill_audit"]) == 1


def test_corrupt_json_fails_closed_for_calibration(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    path.write_text('{"ts":"ok"}\nnot-json\n', encoding="utf-8")

    report = audit([path])

    assert report["status"] == "CORRUPT_SOURCE"
    assert report["read_errors"]


def test_duplicate_exact_fill_identity_is_counted_once(tmp_path: Path) -> None:
    fill = _external_fill("MNQ", "AFS-dup", 1.0, 1.0)
    report = audit([_write(tmp_path / "journal.jsonl", [fill, dict(fill)])])

    assert report["status"] == "MEASURED"
    assert report["overall"]["exact_fills"] == 1
    assert report["duplicate_identity_rows_deduplicated"] == 1
    assert report["identity_conflicts"] == []


def test_conflicting_fill_identity_fails_closed(tmp_path: Path) -> None:
    a = _external_fill("MNQ", "AFS-conflict", 1.0, 1.0)
    b = _external_fill("MNQ", "AFS-conflict", 3.0, 3.0)
    report = audit([_write(tmp_path / "journal.jsonl", [a, b])])

    assert report["status"] == "CORRUPT_SOURCE"
    assert report["identity_conflicts"]
