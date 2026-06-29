from __future__ import annotations

import json
import asyncio
from dataclasses import replace
from datetime import date, timedelta

from ops.evidence_readiness import build_evidence_readiness
import webhook.app as app_module


def _write(path, rows):
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")


def _trade(ts, pnl, *, gex=True, signa="PASS"):
    decision = {
        "ts": ts,
        "instrument": "MNQ",
        "decision": "TRADE",
        "risk_check": {"result": "APPROVED"},
        "setup": {"strategy": "orb_reclaim", "direction": "LONG"},
        "signa_status": signa,
    }
    if gex:
        decision["gex_observed"] = {"ok": True, "regime": "positive"}
    outcome = {
        "ts": ts,
        "type": "OUTCOME",
        "instrument": "MNQ",
        "outcome": {
            "result": "WIN" if pnl > 0 else "LOSS",
            "pnl_dollars": pnl,
        },
    }
    return decision, outcome


def _track(report, key):
    return next(item for item in report["tracks"] if item["key"] == key)


def test_empty_report_is_read_only_and_explicit(tmp_path, config):
    report = build_evidence_readiness(
        tmp_path, days=30, through_date=date(2026, 6, 28), config=config
    )

    assert report["mode"] == "read_only"
    assert report["strategy_or_gate_changed"] is False
    assert _track(report, "range_signal")["status"] == "NOT COLLECTING"
    assert _track(report, "adaptive_schedule_shadow")["status"] == "DISABLED"
    assert report["promotion_policy"].startswith("READY FOR REVIEW")


def test_live_range_and_shadow_candidates_report_collecting_without_fake_outcomes(
    tmp_path, config
):
    today = date(2026, 6, 28)
    _write(
        tmp_path / f"journal_{today}.jsonl",
        [
            {
                "ts": f"{today}T03:15:01+00:00",
                "instrument": "MNQ",
                "decision": "NO_TRADE",
                "range_signal": {"signal_type": "RANGE_REJECT"},
                "wall_context": {"ok": True},
            },
            {
                "ts": f"{today}T03:30:01+00:00",
                "instrument": "MNQ",
                "decision": "NO_TRADE",
                "shadow_candidates": [{
                    "strategy": "gap_fill",
                    "direction": "SHORT",
                    "entry": 100,
                    "stop": 110,
                    "target": 80,
                }],
            },
        ],
    )

    report = build_evidence_readiness(
        tmp_path, through_date=today, config=config
    )

    range_track = _track(report, "range_signal")
    assert range_track["status"] == "COLLECTING"
    assert range_track["resolved_examples"] == 0
    assert range_track["outcome_resolution_available"] is False
    shadow = _track(report, "shadow_setups")
    assert shadow["status"] == "COLLECTING"
    assert shadow["observations"] == 1


def test_context_track_requires_50_trades_and_10_days(tmp_path, config):
    end = date(2026, 6, 28)
    cfg = replace(
        config, gex_shadow_analysis_enabled=True, signa_api_enabled=True
    )
    for offset in range(10):
        day = end - timedelta(days=offset)
        rows = []
        for index in range(5):
            rows.extend(_trade(f"{day}T{14 + index:02d}:30:00+00:00", 20))
        _write(tmp_path / f"journal_{day}.jsonl", rows)

    report = build_evidence_readiness(
        tmp_path, days=30, through_date=end, config=cfg
    )

    for key in ("gex_context", "signa_context"):
        track = _track(report, key)
        assert track["status"] == "READY FOR REVIEW"
        assert track["metrics"]["sample_size"] == 50
        assert track["metrics"]["distinct_days"] == 10
        assert track["metrics"]["expectancy"] == 20.0


def test_malformed_shadow_bracket_blocks_data_quality(tmp_path, config):
    today = date(2026, 6, 28)
    _write(
        tmp_path / f"journal_{today}.jsonl",
        [{
            "ts": f"{today}T03:30:01+00:00",
            "shadow_candidates": [{
                "strategy": "bad",
                "direction": "LONG",
                "entry": 100,
                "stop": 110,
                "target": 90,
            }],
        }],
    )

    report = build_evidence_readiness(
        tmp_path, through_date=today, config=config
    )

    track = _track(report, "shadow_setups")
    assert track["status"] == "DATA QUALITY BLOCKED"
    assert track["malformed_examples"] == 1


def test_status_endpoint_and_diagnostics_share_read_only_scorecard(
    tmp_path, config, monkeypatch
):
    cfg = replace(config, log_dir=str(tmp_path))
    monkeypatch.setattr(app_module, "_config", cfg)

    endpoint = asyncio.run(app_module.status_evidence_readiness(days=30))
    diagnostics = app_module._diagnostics_payload(date.today())

    assert endpoint["strategy_or_gate_changed"] is False
    assert diagnostics["evidence_readiness"]["mode"] == "read_only"
    item = next(
        row for row in diagnostics["items"]
        if row["component"] == "Research evidence"
    )
    assert item["status"] == "info"
