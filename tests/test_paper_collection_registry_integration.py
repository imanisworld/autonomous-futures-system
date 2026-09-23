from __future__ import annotations

import json
from datetime import date

from scripts import paper_collection_report as report


def _registry():
    return {
        "schema": "evidence_registry_v1",
        "entries": [
            {
                "system": "futures",
                "lane": "asia_d_ema",
                "status": "COLLECTING",
                "evidence_n": 4,
                "window_n": 4,
                "sessions_window": 1,
            },
            {
                "system": "options",
                "lane": "2-1-2 reversal",
                "status": "INSUFFICIENT PROSPECTIVE SAMPLE",
                "evidence_n": 10,
                "window_n": None,
                "sessions_window": 1,
            },
        ],
        "uncertainty": [],
    }


def test_eow_registry_appears_only_in_weekly_output():
    futures = {
        "rows": 1,
        "row_types": {"DECISION": 1},
        "decisions": {"NO_TRADE": 1},
        "shadow_outcomes": {},
        "shadow_strategies": {},
        "shadow_lanes": {},
        "instruments": {"MNQ": 1},
    }
    census = {"collectors": [{"name": "futures journal", "status": "FRESH"}]}
    weekly = report.futures_discord_payload(
        futures,
        census,
        period="eow",
        start=date(2026, 9, 14),
        end=date(2026, 9, 18),
        registry=_registry(),
    )
    assert any(field["name"] == "What we're tracking" for field in weekly["embeds"][0]["fields"])
    registry_text = next(f["value"] for f in weekly["embeds"][0]["fields"] if f["name"] == "What we're tracking")
    assert "Asia session daily-trend lane: collecting · 4 so far · 4 this week · 1 trading day" in registry_text
    assert "n=" not in registry_text and "week+=" not in registry_text
    daily = report.futures_discord_payload(
        futures,
        census,
        period="eod",
        start=date(2026, 9, 18),
        end=date(2026, 9, 18),
        registry=_registry(),
    )
    assert not any(field["name"] == "What we're tracking" for field in daily["embeds"][0]["fields"])

    options = {
        "status": "OK",
        "tables": {
            "scans": {"status": "OK", "rows": 5},
            "options_shadow_journal": {"status": "OK", "rows": 1, "status_counts": {"WATCH": 1}},
        },
    }
    weekly_text = report.format_options_report(
        options,
        census,
        period="eow",
        start=date(2026, 9, 14),
        end=date(2026, 9, 18),
        registry=_registry(),
    )
    assert "**What we're tracking**" in weekly_text
    assert "2-1-2 reversal" in weekly_text


def test_main_eow_writes_registry_into_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "_futures_rows", lambda *args: [])
    monkeypatch.setattr(
        report,
        "summarize_futures",
        lambda rows: {
            "rows": 0,
            "row_types": {},
            "decisions": {},
            "shadow_outcomes": {},
            "shadow_strategies": {},
            "shadow_lanes": {},
            "instruments": {},
        },
    )
    monkeypatch.setattr(
        report,
        "summarize_options",
        lambda *args: {
            "status": "MISSING_DB",
            "tables": {
                "scans": {"status": "MISSING_TABLE", "rows": None},
                "options_shadow_journal": {"status": "MISSING_TABLE", "rows": None, "status_counts": {}},
            },
        },
    )
    monkeypatch.setattr(report, "run_collector_census", lambda path: {"collectors": []})
    monkeypatch.setattr(report, "build_registry", lambda **kwargs: _registry())
    assert report.main([
        "--period", "eow",
        "--date", "2026-09-18",
        "--log-dir", str(tmp_path),
        "--options-db", str(tmp_path / "missing.sqlite"),
        "--coverage-data-dir", str(tmp_path / "coverage"),
        "--no-discord",
    ]) == 0
    artifact = json.loads((tmp_path / "paper_collection_eow_2026-09-18.json").read_text())
    assert artifact["evidence_registry"]["schema"] == "evidence_registry_v1"
