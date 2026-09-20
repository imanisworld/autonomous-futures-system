from __future__ import annotations

import sqlite3
from pathlib import Path

from alert_ranker.signa_context_store import SignaContextStore
from scripts.signa_storage_report import build_report, render_markdown
from sources.signa_snapshot_store import SignaSnapshotStore


def test_signa_storage_report_is_read_only_and_summarizes_tables(tmp_path: Path):
    db = tmp_path / "options.sqlite"
    snapshots = SignaSnapshotStore(db)
    context = SignaContextStore(db)

    ok = snapshots.record_snapshot(
        endpoint="/api/v1/signals/QQQ",
        symbol="QQQ",
        timeframe="1d",
        params={"symbol": "QQQ", "timeframe": "1d"},
        retrieved_at="2026-09-20T14:00:00Z",
        payload={"data": {"signal": {"direction": "bullish", "grade": "A"}}},
        status="OK",
        http_status=200,
    )
    snapshots.record_snapshot(
        endpoint="/api/v1/enhanced-signal",
        symbol="QQQ",
        timeframe="1d",
        params={"symbol": "QQQ", "timeframe": "1d"},
        retrieved_at="2026-09-20T14:05:00Z",
        payload={"error": "ReadTimeout"},
        status="ERROR",
    )
    context.record({
        "ticker": "QQQ",
        "source": "action_card",
        "endpoint": "/api/v1/signals/QQQ",
        "status": "SIGNA_CONTEXT",
        "snapshot_id": ok.snapshot_id,
        "timeframe": "1d",
    })

    before = sqlite3.connect(db).execute("SELECT COUNT(*) FROM signa_snapshots").fetchone()[0]
    report = build_report(db, now="2026-09-20T15:00:00Z")
    after = sqlite3.connect(db).execute("SELECT COUNT(*) FROM signa_snapshots").fetchone()[0]

    assert before == after == 2
    assert report["read_only"] is True
    assert report["mutates_database"] is False
    assert report["prunes_database"] is False
    assert report["sqlite"]["quick_check"] == "ok"
    assert report["tables"]["signa_snapshots"]["row_count"] == 2
    assert report["tables"]["options_signa_context"]["row_count"] == 1
    assert report["tables"]["signa_snapshots"]["by_status"] == [
        {"key": "ERROR", "count": 1},
        {"key": "OK", "count": 1},
    ]
    health = {item["endpoint"]: item for item in report["endpoint_health"]}
    assert health["api/v1/enhanced-signal"]["errors"] == 1
    assert health["api/v1/signals/qqq"]["ok"] == 1
    assert report["duplicates"]["context_candidate_keys"] == []


def test_signa_storage_report_markdown_contains_safety_and_counts(tmp_path: Path):
    db = tmp_path / "options.sqlite"
    SignaSnapshotStore(db).record_snapshot(
        endpoint="/api/v1/signals/SPY",
        symbol="SPY",
        timeframe="1d",
        params={"symbol": "SPY"},
        retrieved_at="2026-09-20T14:00:00Z",
        payload={"data": {"signal": {"direction": "neutral"}}},
        status="OK",
        http_status=200,
    )
    report = build_report(db, now="2026-09-20T15:00:00Z")
    text = render_markdown(report)

    assert "# Signa Storage Report" in text
    assert "read only: **True**" in text
    assert "prunes database: **False**" in text
    assert "`signa_snapshots`" in text
    assert "rows: **1**" in text
