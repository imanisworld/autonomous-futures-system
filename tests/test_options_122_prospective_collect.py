from datetime import datetime, timezone
from pathlib import Path

from alert_ranker.options_122_prospective import Prospective122Observation
from scripts.options_122_prospective_collect import (
    COLLECTOR_ID,
    COLLECTOR_VERSION,
    DEFAULT_CADENCE_SECONDS,
    DEFAULT_MAX_CAPTURE_LAG_SECONDS,
    POLICY_EPOCH,
    _live_observation,
    _load_state,
)

UTC = timezone.utc


def _obs():
    return Prospective122Observation(
        setup_id="s1", setup_fingerprint="f1", ticker="SPY", session_date="2026-09-18",
        watch_start="2026-09-18T15:00:00+00:00", watch_until="2026-09-18T15:30:00+00:00",
        status="WATCHING", family=None, subtype=None, direction=None,
        trigger_bar_start=None, trigger_detectable_at=None, trigger_level=None,
        structural_opposite_boundary=None, strategy_stop=None, strategy_target=None,
        strategy_geometry_status="UNRESOLVED", final_scenario="inside_bar",
        opposite_side_broken_later=False, reason_code="no_boundary_break_in_watch_window",
        boundary_high=11.0, boundary_low=6.5, reference_direction="two_up",
    )


def test_epoch_policy_is_frozen():
    assert POLICY_EPOCH == "122-IEX-E1"
    assert DEFAULT_CADENCE_SECONDS == 60
    assert DEFAULT_MAX_CAPTURE_LAG_SECONDS == 120


def test_iex_reversal_maps_to_122_and_keeps_geometry_unresolved():
    out = _live_observation(_obs(), {
        "status":"PROVEN", "break_side":"LOW", "direction":"SHORT",
        "timestamp":"2026-09-18T15:06:30Z", "family_side":"REVERSAL",
    })
    assert out.status == "TRIGGERED"
    assert out.family == "OTHER:strat_122"
    assert out.direction == "SHORT"
    assert out.trigger_level == 6.5
    assert out.structural_opposite_boundary == 11.0
    assert out.strategy_stop is None and out.strategy_target is None


def test_iex_same_direction_break_cancels_reversal():
    out = _live_observation(_obs(), {
        "status":"PROVEN", "break_side":"HIGH", "direction":"LONG",
        "timestamp":"2026-09-18T15:03:00Z", "family_side":"CONTINUATION",
    })
    assert out.status == "CANCELLED"
    assert out.family is None


def test_journal_is_version_locked_and_append_only_state(tmp_path: Path):
    p = tmp_path / "j.jsonl"
    p.write_text(
        '{"record_type":"ARMED","collector_id":"%s","collector_version":"%s","policy_epoch":"%s","setup_id":"s1","observed_at":"2026-09-18T15:00:10+00:00","observation":{"setup_fingerprint":"f1"}}\n'
        '{"record_type":"RESOLUTION","collector_id":"%s","collector_version":"%s","policy_epoch":"%s","setup_id":"s1","observation":{"setup_fingerprint":"f1"}}\n'
        '{"record_type":"RECONCILIATION","collector_id":"%s","collector_version":"%s","policy_epoch":"%s","setup_id":"s1","observation":{"setup_fingerprint":"f1"}}\n'
        % (COLLECTOR_ID,COLLECTOR_VERSION,POLICY_EPOCH,COLLECTOR_ID,COLLECTOR_VERSION,POLICY_EPOCH,COLLECTOR_ID,COLLECTOR_VERSION,POLICY_EPOCH)
    )
    armed, terminal, fp, reconciled = _load_state(p)
    assert armed["s1"] == datetime(2026,9,18,15,0,10,tzinfo=UTC)
    assert "s1" in terminal and fp["s1"] == "f1" and "s1" in reconciled


def test_collector_has_no_broker_order_or_risk_imports():
    src = Path("scripts/options_122_prospective_collect.py").read_text()
    forbidden = [
        "execution.paper_broker", "execution.live", "broker.submit", "submit_order",
        "options_scanner.sqlite", "reserve_risk", "ACTIVE_POSITION", "send_trade_alert",
    ]
    assert all(token not in src for token in forbidden)


def test_import_graph_stays_out_of_execution_broker_webhook_and_risk():
    import json
    import subprocess
    import sys

    code = r"""
import json, sys
import scripts.options_122_prospective_collect
forbidden = ("execution", "broker", "webhook", "risk")
loaded = sorted(name for name in sys.modules if any(name == p or name.startswith(p + ".") for p in forbidden))
print(json.dumps(loaded))
"""
    out = subprocess.check_output([sys.executable, "-c", code], text=True)
    assert json.loads(out) == []


def test_systemd_unit_is_observation_only_and_policy_pinned():
    service = Path("ops/systemd/options-122-prospective.service").read_text()
    timer = Path("ops/systemd/options-122-prospective.timer").read_text()
    assert "--max-capture-lag-seconds 120" in service
    assert "/root/afs-shared/logs/options_122_prospective.jsonl" in service
    assert "/root/afs-shared/logs/options_122_source_trades" in service
    assert "options_scanner.sqlite" not in service
    exec_line = next(line for line in service.splitlines() if line.startswith("ExecStart="))
    assert "broker" not in exec_line.lower()
    assert "order" not in exec_line.lower()
    assert "OnCalendar=Mon..Fri *-*-* 09..16:*:00 America/New_York" in timer


def test_closed_session_is_not_an_error(monkeypatch, tmp_path):
    import argparse
    import asyncio
    import scripts.options_122_prospective_collect as mod

    monkeypatch.setattr(mod, "nyse_session_for", lambda _day: None)
    args = argparse.Namespace(
        env_file=None,
        ticker=["SPY"],
        journal=str(tmp_path / "j.jsonl"),
        raw_trade_dir=str(tmp_path / "raw"),
        max_capture_lag_seconds=mod.DEFAULT_MAX_CAPTURE_LAG_SECONDS,
        dry_run=True,
    )
    result = asyncio.run(mod.run(args))
    assert result["status"] == "CLOSED_SESSION"
    assert result["resolutions_written"] == 0
    assert result["option_evidence_captured"] == 0
