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
    armed, terminal, fp, reconciled, drifted = _load_state(p)
    assert armed["s1"] == datetime(2026,9,18,15,0,10,tzinfo=UTC)
    assert "s1" in terminal and fp["s1"] == "f1" and "s1" in reconciled
    assert drifted == set()


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


def _row(record_type, setup_id, fp, **extra):
    import json

    return json.dumps({
        "record_type": record_type, "collector_id": COLLECTOR_ID,
        "collector_version": COLLECTOR_VERSION, "policy_epoch": POLICY_EPOCH,
        "setup_id": setup_id, "observed_at": "2026-09-22T16:30:11+00:00",
        "observation": {"setup_fingerprint": fp}, **extra,
    }) + "\n"


def test_own_source_drift_row_reloads_and_blocks_setup(tmp_path: Path):
    # Production 2026-09-22: ARMED(fp1) -> SOURCE_DRIFT(fp2) -> every restart
    # raised journal_setup_fingerprint_drift_58.
    p = tmp_path / "j.jsonl"
    p.write_text(
        _row("ARMED", "drift", "fp1")
        + _row("ARMED", "pending", "fpp")
        + _row("SOURCE_DRIFT", "drift", "fp2", reason_code="public_completed_bar_revision")
    )
    armed, terminal, fp, reconciled, drifted = _load_state(p)
    assert drifted == {"drift"}
    assert fp["drift"] == "fp1"
    assert "drift" not in terminal and "drift" not in reconciled
    assert set(armed) == {"drift", "pending"} and fp["pending"] == "fpp"


def test_fingerprint_drift_outside_source_drift_rows_still_fails_closed(tmp_path: Path):
    import pytest

    p = tmp_path / "j.jsonl"
    p.write_text(_row("ARMED", "s1", "fp1") + _row("RESOLUTION", "s1", "fp2"))
    with pytest.raises(RuntimeError, match="journal_setup_fingerprint_drift_2"):
        _load_state(p)
    p.write_text(
        _row("ARMED", "s1", "fp1") + _row("SOURCE_DRIFT", "s1", "fp2")
        + _row("RESOLUTION", "s1", "fp2")
    )
    with pytest.raises(RuntimeError, match="journal_setup_fingerprint_drift_3"):
        _load_state(p)


def test_source_drift_row_stays_version_locked(tmp_path: Path):
    import pytest

    p = tmp_path / "j.jsonl"
    p.write_text(_row("ARMED", "s1", "fp1") + _row("SOURCE_DRIFT", "s1", "fp2").replace(COLLECTOR_VERSION, "old"))
    with pytest.raises(RuntimeError, match="journal_collector_version_mismatch_2"):
        _load_state(p)


def test_run_survives_source_drift_restart_and_keeps_collecting(monkeypatch, tmp_path):
    import argparse
    import asyncio
    import json
    from dataclasses import replace
    from datetime import timedelta
    from types import SimpleNamespace

    import scripts.options_122_prospective_collect as mod

    now = datetime.now(UTC)
    later = (now + timedelta(hours=1)).isoformat()
    session = SimpleNamespace(date=now.date(), open=now - timedelta(hours=1), close=now + timedelta(hours=1))

    class FakePublic:
        def __init__(self, cfg): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_exc): return None

    async def fake_chart(*_args, **_kwargs):
        return {}

    source_calls = []

    async def fake_source(_provider, *, obs, **_kwargs):
        source_calls.append(obs.setup_id)
        return {"status": "NO_BREAK", "reason_code": "watch_open"}

    base = replace(_obs(), watch_until=later)
    observations = [
        replace(base, setup_id="drift", setup_fingerprint="fp2"),
        replace(base, setup_id="pending", setup_fingerprint="fpp"),
        replace(base, setup_id="new", setup_fingerprint="fpn"),
    ]
    monkeypatch.setattr(mod, "load_config", lambda: SimpleNamespace(alpaca_data_base_url="https://x", public_stale_quote_seconds=30))
    monkeypatch.setattr(mod, "resolve_alpaca_credentials", lambda: (None, None))
    monkeypatch.setattr(mod, "nyse_session_for", lambda _day: session)
    monkeypatch.setattr(mod, "_week_sessions", lambda _day: [])
    monkeypatch.setattr(mod, "PublicMarketDataClient", FakePublic)
    monkeypatch.setattr(mod, "_public_chart", fake_chart)
    monkeypatch.setattr(mod, "parse_regular_market_bars", lambda *_a, **_k: SimpleNamespace(bars=[]))
    monkeypatch.setattr(mod, "build_session_timeframe", lambda *_a, **_k: [])
    monkeypatch.setattr(mod, "observe_122_setups", lambda **_k: observations)
    monkeypatch.setattr(mod, "_source_first_boundary", fake_source)

    journal = tmp_path / "j.jsonl"
    triggered = _row("RESOLUTION", "trig", "fpt", source_outcome="REVERSAL", reconciliation_status="PENDING_DELAYED_SIP")
    triggered = triggered.replace('"observation": {"setup_fingerprint": "fpt"}', '"observation": {"setup_fingerprint": "fpt", "watch_until": "%s"}' % later)
    journal.write_text(
        _row("ARMED", "drift", "fp1") + _row("ARMED", "pending", "fpp")
        + _row("ARMED", "trig", "fpt") + triggered
    )
    args = argparse.Namespace(
        env_file=None, ticker=["SPY"], journal=str(journal), raw_trade_dir=str(tmp_path / "raw"),
        max_capture_lag_seconds=mod.DEFAULT_MAX_CAPTURE_LAG_SECONDS, dry_run=False,
    )

    first = asyncio.run(mod.run(args))  # detects the revision -> one SOURCE_DRIFT row
    after_first = journal.read_bytes()
    second = asyncio.run(mod.run(args))  # restart: must load, not raise
    after_second = journal.read_bytes()

    assert first["status"] == second["status"] == "RTH_COLLECTION"
    assert after_second.startswith(after_first)  # append-only, never rewritten
    rows = [json.loads(line) for line in after_second.decode().splitlines()]
    drift_rows = [r for r in rows if r["setup_id"] == "drift"]
    assert [r["record_type"] for r in drift_rows] == ["ARMED", "SOURCE_DRIFT"]
    assert drift_rows[1]["observation"]["setup_fingerprint"] == "fp2"
    assert "drift" not in source_calls
    assert [r["record_type"] for r in rows if r["setup_id"] == "new"] == ["ARMED"]
    assert source_calls == ["pending", "new", "pending", "new"]
    assert second["armed_written"] == 0 and second["data_blocked"] == 0
    assert second["sip_pending"] == 1  # only the real RESOLUTION waits for SIP
    assert not any(r["record_type"] == "RECONCILIATION" for r in rows)
