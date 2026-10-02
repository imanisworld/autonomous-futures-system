"""Locked metadata for the reproduced #915 fillable-event artifact.

This test does not rebuild the stream, does not call the account overlay, and
does not score a portfolio.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from research.mnq_combined_portfolio_audit import PortfolioEvent, _event_sort_key

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "research/artifacts/pr915-six-family-fillable-events-5a9f14b.jsonl"
MANIFEST = ROOT / "research/artifacts/pr915-six-family-fillable-events-5a9f14b.manifest.json"
COLLISIONS = ROOT / "research/artifacts/pr915-six-family-same-timestamp-collisions-5a9f14b.json"
SHA256 = "d5f949fce98e80ab83fd1c2551b99e7644f4ddc0ad6381aaf978b3282be11942"


def _rows() -> list[dict]:
    return [json.loads(line) for line in ARTIFACT.read_text(encoding="utf-8").splitlines() if line]


def test_artifact_bytes_match_the_reproduction_digest() -> None:
    assert hashlib.sha256(ARTIFACT.read_bytes()).hexdigest() == SHA256


def test_manifest_matches_the_stream_and_records_the_census() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = _rows()
    assert manifest["archive_sha"] == "5a9f14baf714947b98a38a19b45f04a8d18fb365"
    assert manifest["sha256"] == SHA256
    assert manifest["row_count"] == len(rows) == 2257
    assert manifest["same_timestamp_census_count"] == 280
    collisions = json.loads(COLLISIONS.read_text(encoding="utf-8"))
    assert len(collisions) == 280
    assert manifest["family_counts"] == {
        "12HR_MIYAGI": 1,
        "4HR_RETRIGGER": 37,
        "60M_322_FIRST_LIVE": 11,
        "ASIA_D_EMA": 2134,
        "DAILY_22_COMPLETED_CLOSE": 38,
        "SUSTAINED_TREND_V1": 36,
    }


def test_rows_use_the_archived_event_order_and_overlay_fields() -> None:
    rows = _rows()
    events = [PortfolioEvent(**row) for row in rows]
    assert events == sorted(events, key=_event_sort_key)
    assert set(rows[0]) == {
        "family",
        "source_id",
        "signal_ts",
        "eligible_fill_ts",
        "exit_ts",
        "observation_day",
        "direction",
        "entry",
        "stop",
        "target",
        "result",
        "net_pnl",
        "session",
        "source",
    }
