from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from ops.evidence_registry import build_registry, format_registry_lines


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_registry_surfaces_growth_gaps_and_options_summary(tmp_path):
    log_dir = tmp_path / "logs"
    coverage_dir = tmp_path / "coverage"
    log_dir.mkdir()
    coverage_dir.mkdir()

    _write_jsonl(
        log_dir / "forward_ab_2026_08_v1.jsonl",
        [
            {
                "record_type": "CANDIDATE",
                "strategy": "vwap_hold",
                "variant": "control",
                "signal_timestamp": "2026-09-15T14:00:00Z",
            },
            {
                "record_type": "CANDIDATE",
                "strategy": "vwap_hold",
                "variant": "control",
                "signal_timestamp": "2026-09-16T14:00:00Z",
            },
        ],
    )
    _write_jsonl(
        log_dir / "asia_d_ema_cohort" / "evidence.jsonl",
        [
            {
                "campaign_id": "asia_d_ema_2026_09_v1",
                "event": "CANDIDATE",
                "day": "2026-09-16",
                "timestamp": "2026-09-16T01:00:00Z",
            }
        ],
    )
    _write_jsonl(
        coverage_dir / "ledger.jsonl",
        [
            {
                "session_date": "2026-09-15",
                "status": "DONE",
                "collector_version": "col-v0.1",
                "recorded_at": "2026-09-15T20:45:00Z",
            },
            {
                "session_date": "2026-09-16",
                "status": "FAILED",
                "collector_version": "col-v0.1",
                "recorded_at": "2026-09-16T20:35:17Z",
            },
            {
                "session_date": "2026-09-16",
                "status": "DONE",
                "collector_version": "col-v0.1",
                "observer_repair": {"prior_run_id": 6},
                "recorded_at": "2026-09-16T21:09:00Z",
            },
        ],
    )
    family_csv = coverage_dir / "prospective_family_summary.csv"
    family_csv.write_text(
        "Family,Prospective episodes,Sessions,LONG,SHORT,Ex-opening FS diff,Status\n"
        "2-1-2 reversal,10,1,5,5,-3.3,INSUFFICIENT PROSPECTIVE SAMPLE\n"
        "1-2-2,15,1,7,8,3.8,INSUFFICIENT PROSPECTIVE SAMPLE\n",
        encoding="utf-8",
    )

    census = {
        "campaign_arms": {
            "configured": {
                "vwap_hold/control": {
                    "strategy": "vwap_hold",
                    "variant": "control",
                    "count": 9,
                    "last": "2026-09-16T14:00:00+00:00",
                },
                "orb_reclaim/control": {
                    "strategy": "orb_reclaim",
                    "variant": "control",
                    "count": 3,
                    "last": "2026-09-01T14:00:00+00:00",
                },
            }
        },
        "hypothetical_lanes": {
            "lanes": {
                "mes_122_1500": {
                    "exists": True,
                    "open_position": None,
                    "state_last": "2026-09-16T15:00:00+00:00",
                    "heartbeat": "lane journal row on every MES 15m bar",
                    "epoch": None,
                }
            }
        },
    }

    registry = build_registry(
        log_dir=log_dir,
        coverage_dir=coverage_dir,
        census=census,
        start=date(2026, 9, 14),
        end=date(2026, 9, 16),
        family_summary_path=family_csv,
    )

    by_lane = {row["lane"]: row for row in registry["entries"]}
    assert by_lane["forward_ab:vwap_hold/control"]["window_n"] == 2
    assert by_lane["forward_ab:vwap_hold/control"]["status"] == "COLLECTING"
    assert by_lane["forward_ab:orb_reclaim/control"]["status"] == "QUIET_THIS_WEEK"
    assert by_lane["asia_d_ema"]["window_n"] == 1
    assert by_lane["coverage_collector"]["evidence_n"] == 2
    assert by_lane["coverage_collector"]["status"] == "COLLECTING_WITH_REPAIR_PROVENANCE"
    assert by_lane["2-1-2 reversal"]["evidence_n"] == 10
    assert by_lane["1-2-2"]["sessions_window"] == 1
    assert by_lane["inside-bar break"]["status"] == "SUMMARY_NOT_CENTRALIZED"

    futures_lines = format_registry_lines(registry, system="futures")
    options_lines = format_registry_lines(registry, system="options")
    assert any("vwap_hold/control" in line for line in futures_lines)
    assert any("2-1-2 reversal" in line for line in options_lines)
    assert any("least-certain" in line for line in futures_lines)


def test_missing_family_summary_is_explicit_not_zero(tmp_path):
    registry = build_registry(
        log_dir=tmp_path / "logs",
        coverage_dir=tmp_path / "coverage",
        census={},
        start=date(2026, 9, 14),
        end=date(2026, 9, 18),
        family_summary_path=None,
    )
    families = {
        row["lane"]: row
        for row in registry["entries"]
        if row["system"] == "options" and row["lane"] != "coverage_collector"
    }
    assert families["2-1-2 reversal"]["status"] == "SUMMARY_NOT_CENTRALIZED"
    assert families["2-1-2 reversal"]["evidence_n"] is None
    assert families["1-2-2"]["evidence_n"] is None
