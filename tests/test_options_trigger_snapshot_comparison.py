from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.options_trigger_snapshot_comparison import (
    load_verified_snapshot,
    summarize,
)


def _write_snapshot(tmp_path: Path):
    observer = tmp_path / "observer.sqlite"
    observer.write_bytes(b"frozen-observer")
    observer_sha = hashlib.sha256(observer.read_bytes()).hexdigest()

    root = tmp_path / "snapshot"
    root.mkdir()
    row = {
        "symbol": "SPY",
        "timeframe": "5Min",
        "start": "2026-09-09T13:30:00+00:00",
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 1000.0,
        "vwap": 100.4,
    }
    payload = (
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    name = "2026-09-09_5Min.jsonl"
    (root / name).write_bytes(payload)
    manifest = {
        "snapshot_id": "OPTIONS_TRIGGER_BAR_SNAPSHOT",
        "snapshot_version": "trigger-bars-v0.1",
        "study_epoch": "NEW_REFETCH_NOT_FROZEN_COV_V0_1",
        "frozen_observer_reference": {
            "filename": observer.name,
            "bytes": len(observer.read_bytes()),
            "sha256": observer_sha,
        },
        "files": [
            {
                "path": name,
                "rows": 1,
                "rows_by_symbol": {"SPY": 1},
                "sha256": hashlib.sha256(payload).hexdigest(),
                "query": {
                    "session_date": "2026-09-09",
                    "timeframe": "5Min",
                },
            }
        ],
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root, observer


def test_verified_snapshot_binds_exact_observer_and_file_counts(tmp_path):
    root, observer = _write_snapshot(tmp_path)
    manifest, loaded = load_verified_snapshot(root, observer)
    assert manifest["snapshot_version"] == "trigger-bars-v0.1"
    assert len(loaded["2026-09-09"]["5Min"]["SPY"]) == 1


def test_verified_snapshot_rejects_tampered_payload(tmp_path):
    root, observer = _write_snapshot(tmp_path)
    path = root / "2026-09-09_5Min.jsonl"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_verified_snapshot(root, observer)


def test_verified_snapshot_rejects_manifest_count_drift(tmp_path):
    root, observer = _write_snapshot(tmp_path)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][0]["rows"] = 2
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="row-count mismatch"):
        load_verified_snapshot(root, observer)


def test_summarize_binds_212_family_direction_levels_and_timing():
    rows = [
        {
            "trigger_status": "TRIGGERED",
            "trigger_family": "STRAT_212_REVERSAL",
            "trigger_direction": "LONG",
            "trigger_level": 101.0,
            "invalidation_level": 99.0,
            "trigger_offset_minutes": 5.0,
            "trigger_bar_start": "2026-09-09T14:35:00+00:00",
            "old_family": "STRAT_212_REVERSAL",
            "old_direction": "LONG",
            "old_entry_trigger": 101.0,
            "old_invalidation": 99.0,
            "old_first_sight_latency_minutes": 42.95,
            "final_scenario": "two_up",
            "session_date": "2026-09-09",
            "symbol": "SPY",
            "watch_bar_start": "2026-09-09T14:30:00+00:00",
            "trigger_reason_code": "first_boundary_break",
        }
    ]
    summary = summarize(rows)
    subset = summary["frozen_212_reversal"]
    assert subset["old_rows"] == 1
    assert subset["recovered_same_family_and_direction"] == 1
    assert subset["exact_trigger_and_invalidation_level_matches"] == 1
    assert subset["trigger_offset_distribution"] == {5.0: 1}
    assert subset["old_first_sight_latency_from_trigger_bar_start_minutes"]["median"] == 42.95
