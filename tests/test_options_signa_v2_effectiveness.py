from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.options_signa_v2_effectiveness import (
    build_effectiveness_report,
    load_resolved_v2_rows,
)


def _make_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE scans (
                id INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL,
                source TEXT NOT NULL,
                ticker TEXT NOT NULL,
                direction TEXT NOT NULL,
                score INTEGER NOT NULL,
                pattern TEXT NOT NULL,
                components_json TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                alert_sent INTEGER NOT NULL,
                alert_suppression_reason TEXT NOT NULL
            );
            CREATE TABLE options_shadow_journal (
                id INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL,
                scan_id INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                direction TEXT NOT NULL,
                score INTEGER NOT NULL,
                pattern TEXT NOT NULL,
                status TEXT NOT NULL,
                setup_inputs_json TEXT NOT NULL,
                provider_snapshot_json TEXT NOT NULL,
                selected_contract_json TEXT NOT NULL,
                outcome_json TEXT NOT NULL
            );
            """
        )


def _insert(
    path: Path,
    *,
    row_id: int,
    day: str,
    direction: str,
    pattern: str,
    signa_direction: str,
    grade: str,
    confidence: float,
    pnl: float | None,
    signa_ok: bool = True,
) -> None:
    raw = {
        "signa_v2_ok": signa_ok,
        "signa_v2_direction": signa_direction,
        "signa_v2_grade": grade,
        "signa_v2_confidence": confidence,
        "signa_v2_timeframe": "1d",
        "signa_v2_component_scores": {"trend": 85, "momentum": 65},
    }
    outcome = {} if pnl is None else {"pnl_dollars": pnl}
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO scans VALUES (?, ?, 'scheduled', 'AAPL', ?, 7, ?, '{}', ?, 0, '')",
            (row_id, f"{day}T14:00:00+00:00", direction, pattern, json.dumps(raw)),
        )
        conn.execute(
            "INSERT INTO options_shadow_journal VALUES (?, ?, ?, 'AAPL', ?, 7, ?, 'WIN', '{}', '{}', '{}', ?)",
            (
                row_id,
                f"{day}T14:00:00+00:00",
                row_id,
                direction,
                pattern,
                json.dumps(outcome),
            ),
        )


def test_loader_reads_existing_evidence_and_tracks_missing_rows(tmp_path: Path) -> None:
    db = tmp_path / "options.sqlite"
    _make_db(db)
    _insert(
        db,
        row_id=1,
        day="2026-09-01",
        direction="LONG",
        pattern="2-1-2",
        signa_direction="LONG",
        grade="A",
        confidence=88,
        pnl=40,
    )
    _insert(
        db,
        row_id=2,
        day="2026-09-02",
        direction="SHORT",
        pattern="2-1-2",
        signa_direction="LONG",
        grade="B",
        confidence=74,
        pnl=-20,
    )
    _insert(
        db,
        row_id=3,
        day="2026-09-03",
        direction="LONG",
        pattern="Daily 2-2",
        signa_direction="WAIT",
        grade="B",
        confidence=60,
        pnl=None,
    )
    _insert(
        db,
        row_id=4,
        day="2026-09-04",
        direction="LONG",
        pattern="Daily 2-2",
        signa_direction="LONG",
        grade="A",
        confidence=90,
        pnl=10,
        signa_ok=False,
    )

    rows, quality = load_resolved_v2_rows(db)
    assert len(rows) == 2
    assert quality == {
        "joined": 4,
        "malformed_json": 0,
        "without_v2": 1,
        "missing_pnl": 1,
    }


def test_report_groups_alignment_grade_confidence_and_components() -> None:
    rows = [
        {
            "timestamp": "2026-09-01T14:00:00+00:00",
            "direction": "LONG",
            "pattern": "2-1-2",
            "raw": {
                "signa_v2_direction": "LONG",
                "signa_v2_grade": "A",
                "signa_v2_confidence": 88,
                "signa_v2_timeframe": "1d",
                "signa_v2_component_scores": {"trend": 90, "momentum": 60},
            },
            "outcome": {"pnl_dollars": 50},
        },
        {
            "timestamp": "2026-09-02T14:00:00+00:00",
            "direction": "LONG",
            "pattern": "2-1-2",
            "raw": {
                "signa_v2_direction": "SHORT",
                "signa_v2_grade": "C",
                "signa_v2_confidence": 55,
                "signa_v2_timeframe": "1d",
                "signa_v2_component_scores": {"trend": 40, "momentum": 75},
            },
            "outcome": {"pnl_dollars": -25},
        },
    ]

    report = build_effectiveness_report(rows)
    assert report["overall"]["sample_size"] == 2
    assert report["overall"]["net_pnl_dollars"] == 25.0
    assert report["by_signa_alignment"]["ALIGNED"]["net_pnl_dollars"] == 50.0
    assert report["by_signa_alignment"]["OPPOSED"]["net_pnl_dollars"] == -25.0
    assert report["by_signa_grade"]["A"]["sample_size"] == 1
    assert report["by_confidence_band"]["80_PLUS"]["sample_size"] == 1
    assert report["components"]["trend"]["80_PLUS"]["sample_size"] == 1
    assert report["components"]["trend"]["BELOW_50"]["sample_size"] == 1
    assert report["policy"]["authority"] == "observational_only"
    assert report["policy"]["promotion"] == "not_permitted_by_this_report"
    assert report["overall"]["ready_for_review"] is False


def test_effectiveness_script_is_read_only_and_reuses_existing_policy() -> None:
    source = Path("scripts/options_signa_v2_effectiveness.py").read_text()
    assert "mode=ro" in source
    assert "CREATE TABLE" not in source
    assert "INSERT INTO" not in source
    assert "UPDATE " not in source
    assert "DELETE FROM" not in source
    assert "_performance_metrics" in source
    assert "CONTEXT_MIN_EXAMPLES" in source
    assert "STRATEGY_MIN_DAYS" in source
    assert "LIVE_TRADING_ENABLED" not in source
    assert ".post(" not in source
    assert ".put(" not in source
    assert ".delete(" not in source
