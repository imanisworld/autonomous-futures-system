from __future__ import annotations

import ast
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from scripts.options_212r_prospective_collect import _load_journal, _week_sessions

UTC = timezone.utc


def test_journal_recovers_earliest_arm_and_terminal_state(tmp_path: Path):
    path = tmp_path / "evidence.jsonl"
    observation = {"setup_fingerprint": "fp1"}
    rows = [
        {"record_type": "ARMED", "setup_id": "abc", "observed_at": "2026-09-18T15:04:00+00:00", "observation": observation},
        {"record_type": "ARMED", "setup_id": "abc", "observed_at": "2026-09-18T15:03:00+00:00", "observation": observation},
        {"record_type": "RESOLUTION", "setup_id": "abc", "observed_at": "2026-09-18T15:10:30+00:00", "observation": observation},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    armed, terminal, fingerprints = _load_journal(path)
    assert armed["abc"] == datetime(2026, 9, 18, 15, 3, tzinfo=UTC)
    assert terminal == {"abc"}
    assert fingerprints == {"abc": "fp1"}


def test_malformed_journal_fails_closed(tmp_path: Path):
    path = tmp_path / "evidence.jsonl"
    path.write_text("not-json\n")
    with pytest.raises(RuntimeError, match="journal_invalid_json"):
        _load_journal(path)


def test_collector_has_no_execution_risk_broker_or_notification_imports():
    source = Path("scripts/options_212r_prospective_collect.py").read_text()
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = ("execution", "risk", "webhook", "notifications", "broker")
    assert not [name for name in imported if name.startswith(forbidden)]


def test_pure_observer_has_no_network_or_storage_imports():
    source = Path("alert_ranker/options_212r_prospective.py").read_text()
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any(name.startswith(("httpx", "requests", "sqlite3", "execution", "risk", "broker")) for name in imported)


def test_journal_source_revision_fails_closed(tmp_path: Path):
    path = tmp_path / "evidence.jsonl"
    rows = [
        {"record_type": "ARMED", "setup_id": "abc", "observed_at": "2026-09-18T15:01:00+00:00", "observation": {"setup_fingerprint": "fp1"}},
        {"record_type": "RESOLUTION", "setup_id": "abc", "observed_at": "2026-09-18T15:10:00+00:00", "observation": {"setup_fingerprint": "fp2"}},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(RuntimeError, match="journal_setup_fingerprint_drift"):
        _load_journal(path)


def test_week_history_includes_previous_friday_for_monday_open_reanchor():
    sessions = _week_sessions(datetime(2026, 9, 21, tzinfo=UTC).date())
    dates = {item.date.isoformat() for item in sessions}
    assert "2026-09-18" in dates
    assert "2026-09-21" in dates


def _triggered_observation():
    from alert_ranker.options_212r_prospective import Prospective212Observation

    return Prospective212Observation(
        setup_id="abc",
        setup_fingerprint="fp1",
        ticker="SPY",
        session_date="2026-09-18",
        watch_start="2026-09-18T15:00:00+00:00",
        watch_until="2026-09-18T15:30:00+00:00",
        status="TRIGGERED",
        family="STRAT_212_REVERSAL",
        subtype="REVERSAL",
        direction="SHORT",
        trigger_bar_start="2026-09-18T15:05:00+00:00",
        trigger_detectable_at="2026-09-18T15:10:00+00:00",
        trigger_level=6.5,
        invalidation_level=10.5,
        source_target=6.0,
        source_target_r=0.125,
        source_target_consumed=False,
        final_scenario="two_down",
        opposite_side_broken_later=False,
        reason_code="first_boundary_break",
        boundary_high=10.5,
        boundary_low=6.5,
        reference_direction="two_up",
    )


def test_final_selector_capture_must_finish_inside_capture_window():
    from scripts.options_212r_prospective_collect import _enforce_final_capture_lag

    obs = _triggered_observation()
    prearmed = datetime(2026, 9, 18, 15, 4, tzinfo=UTC)
    on_time = _enforce_final_capture_lag(
        {"status": "CAPTURED", "captured_at": "2026-09-18T15:10:45+00:00"},
        observation=obs,
        prearmed_at=prearmed,
        max_capture_lag_seconds=60,
    )
    assert on_time["status"] == "CAPTURED"
    assert on_time["capture_lag_seconds"] == 45

    late = _enforce_final_capture_lag(
        {"status": "CAPTURED", "captured_at": "2026-09-18T15:11:30+00:00"},
        observation=obs,
        prearmed_at=prearmed,
        max_capture_lag_seconds=60,
    )
    assert late["status"] == "DATA_BLOCKED"
    assert late["reason_code"] == "post_selector_decision_time_capture_late"
    assert late["capture_lag_seconds"] == 90


def test_collector_armed_timestamp_uses_per_ticker_observation_clock():
    source = Path("scripts/options_212r_prospective_collect.py").read_text()
    assert "source_observed_at = datetime.now(timezone.utc)" in source
    assert "armed_seen[setup_id] = source_observed_at" in source
    assert '"observed_at": source_observed_at.isoformat()' in source
