from __future__ import annotations

import json
from datetime import date

from fastapi.testclient import TestClient

from ops.fill_realism import build_fill_realism_status
from webhook.app import _demo_path_allowed, _render_dashboard, app


def _write(path, records):
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n")


def test_status_reports_actual_journal_no_fill_rate_by_setup(tmp_path):
    _write(
        tmp_path / "journal_2026-06-26.jsonl",
        [
            {
                "timestamp": "2026-06-26T14:00:00Z",
                "instrument": "MES",
                "decision": "TRADE",
                "setup": {"strategy": "vwap_hold", "direction": "LONG"},
            },
            {
                "timestamp": "2026-06-26T14:00:01Z",
                "type": "OUTCOME",
                "instrument": "MES",
                "outcome": {
                    "result": "CANCELLED",
                    "exit_reason": "ENTRY_NOT_FILLED",
                },
            },
            {
                "instrument": "MNQ",
                "decision": "TRADE",
                "setup": {"strategy": "orb_breakout", "direction": "LONG"},
            },
            {
                "type": "OUTCOME",
                "instrument": "MNQ",
                "outcome": {"result": "WIN", "exit_reason": "target"},
            },
        ],
    )

    payload = build_fill_realism_status(
        tmp_path, days=1, through_date=date(2026, 6, 26)
    )

    assert payload["source"] == "journal_only"
    assert payload["overall"] == {
        "resolved_attempts": 2,
        "no_fills": 1,
        "no_fill_rate_pct": 50.0,
    }
    assert payload["by_setup"][0] == {
        "setup": "orb_breakout",
        "entry_type": "stop_or_other",
        "resolved_attempts": 1,
        "no_fills": 0,
        "no_fill_rate_pct": 0.0,
    }
    assert payload["by_setup"][1]["setup"] == "vwap_hold"
    assert payload["by_setup"][1]["no_fill_rate_pct"] == 100.0
    assert payload["recent_no_fills"][0]["decision_timestamp"] == "2026-06-26T14:00:00Z"


def test_status_exposes_empty_window_and_unresolved_attempts(tmp_path):
    _write(
        tmp_path / "journal_2026-06-26.jsonl",
        [
            {
                "instrument": "MES",
                "decision": "TRADE",
                "setup": {"strategy": "vwap_hold"},
            }
        ],
    )

    payload = build_fill_realism_status(
        tmp_path, days=2, through_date=date(2026, 6, 26)
    )

    assert payload["window"]["journal_files_found"] == 1
    assert payload["window"]["unresolved_attempts"] == 1
    assert payload["overall"]["resolved_attempts"] == 0
    assert payload["overall"]["no_fill_rate_pct"] is None
    assert payload["by_setup"] == []


def test_status_cache_invalidates_when_journal_grows(tmp_path):
    path = tmp_path / "journal_2026-06-26.jsonl"
    trade = {
        "instrument": "MES",
        "decision": "TRADE",
        "setup": {"strategy": "vwap_hold"},
    }
    _write(path, [trade])
    first = build_fill_realism_status(
        tmp_path, days=1, through_date=date(2026, 6, 26)
    )

    _write(
        path,
        [
            trade,
            {
                "type": "OUTCOME",
                "instrument": "MES",
                "outcome": {
                    "result": "CANCELLED",
                    "exit_reason": "ENTRY_NOT_FILLED",
                },
            },
        ],
    )
    second = build_fill_realism_status(
        tmp_path, days=1, through_date=date(2026, 6, 26)
    )

    assert first["overall"]["resolved_attempts"] == 0
    assert second["overall"]["resolved_attempts"] == 1
    assert second["overall"]["no_fill_rate_pct"] == 100.0


def test_fill_realism_endpoint_uses_configured_journal_dir(monkeypatch, tmp_path):
    import webhook.app as app_module

    monkeypatch.setattr(app_module._config, "log_dir", str(tmp_path))
    response = TestClient(app).get("/status/fill-realism?days=3&recent_limit=4")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "journal_only"
    assert payload["window"]["days_requested"] == 3
    assert payload["recent_no_fills"] == []


def test_fill_realism_is_available_in_public_demo_mode():
    assert _demo_path_allowed("/status/fill-realism") is True


def test_dashboard_contains_fill_realism_card_and_detail_link():
    html = _render_dashboard({"date": "2026-06-27", "paper_mode": True})

    assert "Fill Realism" in html
    assert 'id="fill-realism-details"' in html
    assert 'target="_blank" rel="noopener"' in html
    assert "fetch('/status/fill-realism?days=7'" in html
