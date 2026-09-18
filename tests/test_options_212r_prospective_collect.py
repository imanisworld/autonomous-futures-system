from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from scripts.options_212r_prospective_collect import (
    COLLECTOR_ID,
    COLLECTOR_VERSION,
    _load_journal,
    _week_sessions,
)

UTC = timezone.utc


def _state_row(record_type: str, **kwargs):
    return {
        "record_type": record_type,
        "collector_id": COLLECTOR_ID,
        "collector_version": COLLECTOR_VERSION,
        **kwargs,
    }


def test_journal_recovers_earliest_arm_and_terminal_state(tmp_path: Path):
    path = tmp_path / "evidence.jsonl"
    observation = {"setup_fingerprint": "fp1"}
    rows = [
        _state_row("ARMED", setup_id="abc", observed_at="2026-09-18T15:04:00+00:00", observation=observation),
        _state_row("ARMED", setup_id="abc", observed_at="2026-09-18T15:03:00+00:00", observation=observation),
        _state_row("RESOLUTION", setup_id="abc", observed_at="2026-09-18T15:10:30+00:00", observation=observation),
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
        _state_row("ARMED", setup_id="abc", observed_at="2026-09-18T15:01:00+00:00", observation={"setup_fingerprint": "fp1"}),
        _state_row("RESOLUTION", setup_id="abc", observed_at="2026-09-18T15:10:00+00:00", observation={"setup_fingerprint": "fp2"}),
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


def test_final_selector_capture_uses_exact_cross_not_five_minute_close():
    from scripts.options_212r_prospective_collect import _enforce_final_capture_lag

    obs = _triggered_observation()
    prearmed = datetime(2026, 9, 18, 15, 4, tzinfo=UTC)

    # The five-minute bar closes at 15:10, which would make this look only
    # 45 seconds late. The actual strict crossing occurred at 15:06:30, so
    # the evidence is really 255 seconds late and must be blocked.
    exact_cross = datetime(2026, 9, 18, 15, 6, 30, tzinfo=UTC)
    late = _enforce_final_capture_lag(
        {"status": "CAPTURED", "captured_at": "2026-09-18T15:10:45+00:00"},
        observation=obs,
        prearmed_at=prearmed,
        max_capture_lag_seconds=60,
        trigger_crossed_at=exact_cross,
    )
    assert late["status"] == "DATA_BLOCKED"
    assert late["reason_code"] == "post_selector_decision_time_capture_late"
    assert late["capture_lag_seconds"] == 255

    on_time_cross = datetime(2026, 9, 18, 15, 9, 50, tzinfo=UTC)
    on_time = _enforce_final_capture_lag(
        {"status": "CAPTURED", "captured_at": "2026-09-18T15:10:45+00:00"},
        observation=obs,
        prearmed_at=prearmed,
        max_capture_lag_seconds=60,
        trigger_crossed_at=on_time_cross,
    )
    assert on_time["status"] == "CAPTURED"
    assert on_time["capture_lag_seconds"] == 55


def test_collector_armed_timestamp_uses_per_ticker_observation_clock():
    source = Path("scripts/options_212r_prospective_collect.py").read_text()
    assert "source_observed_at = datetime.now(timezone.utc)" in source
    assert "armed_seen[setup_id] = source_observed_at" in source
    assert '"observed_at": source_observed_at.isoformat()' in source


def test_exact_sip_cross_skips_equal_trigger_and_persists_immutable_window(tmp_path: Path):
    from scripts.options_212r_prospective_collect import _capture_exact_trigger_cross
    from scripts.options_trigger_trade_timestamp_audit import parse_trade

    class FakeProvider:
        async def fetch_trades(self, *, symbol, start, end):
            assert symbol == "SPY"
            return [
                parse_trade(
                    "SPY",
                    {
                        "t": "2026-09-18T15:05:01.000000000Z",
                        "p": 7.0,
                        "s": 100,
                        "x": "Q",
                        "c": ["@"],
                        "z": "C",
                        "i": 1,
                    },
                ),
                parse_trade(
                    "SPY",
                    {
                        "t": "2026-09-18T15:05:02.000000000Z",
                        "p": 6.5,
                        "s": 100,
                        "x": "Q",
                        "c": ["@"],
                        "z": "C",
                        "i": 2,
                    },
                ),
                parse_trade(
                    "SPY",
                    {
                        "t": "2026-09-18T15:05:03.123456789Z",
                        "p": 6.49,
                        "s": 100,
                        "x": "Q",
                        "c": ["@"],
                        "z": "C",
                        "i": 3,
                    },
                ),
            ]

    out = asyncio.run(
        _capture_exact_trigger_cross(
            FakeProvider(),
            observation=_triggered_observation(),
            setup_id="abc",
            sip_trade_dir=tmp_path,
            persist_raw=True,
        )
    )
    assert out["status"] == "PROVEN"
    assert out["trigger_crossed_at"] == "2026-09-18T15:05:03.123456789Z"
    assert out["trigger_cross_trade"]["price"] == 6.49
    assert out["raw_trade_rows"] == 3
    assert out["eligible_trade_rows"] == 3
    raw = Path(out["raw_trade_file"])
    assert raw.exists()
    assert raw.name == "abc.jsonl"

    # Same source bytes are idempotent rather than rewritten.
    again = asyncio.run(
        _capture_exact_trigger_cross(
            FakeProvider(),
            observation=_triggered_observation(),
            setup_id="abc",
            sip_trade_dir=tmp_path,
            persist_raw=True,
        )
    )
    assert again["raw_trade_sha256"] == out["raw_trade_sha256"]


def test_exact_sip_cross_missing_blocks_before_selector_capture(tmp_path: Path):
    from scripts.options_212r_prospective_collect import _capture_exact_trigger_cross
    from scripts.options_trigger_trade_timestamp_audit import parse_trade

    class NoCrossProvider:
        async def fetch_trades(self, *, symbol, start, end):
            return [
                parse_trade(
                    "SPY",
                    {
                        "t": "2026-09-18T15:05:02.000000000Z",
                        "p": 6.5,
                        "s": 100,
                        "x": "Q",
                        "c": ["@"],
                        "z": "C",
                        "i": 1,
                    },
                ),
                parse_trade(
                    "SPY",
                    {
                        "t": "2026-09-18T15:05:03.000000000Z",
                        "p": 6.7,
                        "s": 100,
                        "x": "Q",
                        "c": ["@"],
                        "z": "C",
                        "i": 2,
                    },
                ),
            ]

    out = asyncio.run(
        _capture_exact_trigger_cross(
            NoCrossProvider(),
            observation=_triggered_observation(),
            setup_id="abc",
            sip_trade_dir=tmp_path,
            persist_raw=False,
        )
    )
    assert out["status"] == "DATA_BLOCKED"
    assert out["reason_code"] == "sip_strict_trigger_cross_missing"


def _watching_observation():
    from dataclasses import replace

    return replace(
        _triggered_observation(),
        status="WATCHING",
        family=None,
        subtype=None,
        direction=None,
        trigger_bar_start=None,
        trigger_detectable_at=None,
        trigger_level=None,
        invalidation_level=None,
        source_target=None,
        source_target_r=None,
        source_target_consumed=False,
        final_scenario="inside",
        reason_code="no_boundary_break",
    )


def _history_30m_for_live_212():
    from alert_ranker.causal_bars import Bar

    return [
        Bar(
            start=datetime(2026, 9, 18, 13, 30, tzinfo=UTC),
            open=7,
            high=10,
            low=5,
            close=8,
            volume=1000,
            vwap=8,
        ),
        Bar(
            start=datetime(2026, 9, 18, 14, 0, tzinfo=UTC),
            open=8,
            high=11,
            low=6,
            close=10,
            volume=1000,
            vwap=9,
        ),
        Bar(
            start=datetime(2026, 9, 18, 14, 30, tzinfo=UTC),
            open=9,
            high=10.5,
            low=6.5,
            close=9.5,
            volume=1000,
            vwap=9,
        ),
    ]


def test_live_watching_sip_first_break_resolves_reversal_before_bar_close(tmp_path: Path):
    from scripts.options_212r_prospective_collect import (
        _capture_live_first_boundary,
        _live_observation_from_cross,
    )
    from scripts.options_trigger_trade_timestamp_audit import parse_trade

    class FakeProvider:
        async def fetch_trades(self, *, symbol, start, end):
            assert symbol == "SPY"
            assert start == datetime(2026, 9, 18, 15, 0, tzinfo=UTC)
            assert end == datetime(2026, 9, 18, 15, 2, tzinfo=UTC)
            return [
                parse_trade(
                    "SPY",
                    {
                        "t": "2026-09-18T15:00:01.000000000Z",
                        "p": 8.0,
                        "s": 100,
                        "x": "Q",
                        "c": ["@"],
                        "z": "C",
                        "i": 1,
                    },
                ),
                # Equality with the low is not a strict break.
                parse_trade(
                    "SPY",
                    {
                        "t": "2026-09-18T15:00:02.000000000Z",
                        "p": 6.5,
                        "s": 100,
                        "x": "Q",
                        "c": ["@"],
                        "z": "C",
                        "i": 2,
                    },
                ),
                # First strict break is LOW -> SHORT reversal from prior 2U.
                parse_trade(
                    "SPY",
                    {
                        "t": "2026-09-18T15:00:03.123456789Z",
                        "p": 6.49,
                        "s": 100,
                        "x": "Q",
                        "c": ["@"],
                        "z": "C",
                        "i": 3,
                    },
                ),
                # Opposite side breaks later; first-break ordering is preserved.
                parse_trade(
                    "SPY",
                    {
                        "t": "2026-09-18T15:00:04.000000000Z",
                        "p": 10.6,
                        "s": 100,
                        "x": "Q",
                        "c": ["@"],
                        "z": "C",
                        "i": 4,
                    },
                ),
            ]

    obs = _watching_observation()
    cross = asyncio.run(
        _capture_live_first_boundary(
            FakeProvider(),
            observation=obs,
            observed_until=datetime(2026, 9, 18, 15, 2, tzinfo=UTC),
            setup_id="live-reversal",
            sip_trade_dir=tmp_path,
            persist_raw=True,
        )
    )
    assert cross["status"] == "PROVEN"
    assert cross["break_side"] == "LOW"
    assert cross["direction"] == "SHORT"
    assert cross["trigger_crossed_at"] == "2026-09-18T15:00:03.123456789Z"

    live = _live_observation_from_cross(
        obs,
        cross_evidence=cross,
        history_30m=_history_30m_for_live_212(),
    )
    assert live.status == "TRIGGERED"
    assert live.family == "STRAT_212_REVERSAL"
    assert live.subtype == "REVERSAL"
    assert live.direction == "SHORT"
    assert live.trigger_level == 6.5
    assert live.invalidation_level == 10.5
    assert live.source_target == 6.0
    assert live.source_target_r == 0.125
    assert live.source_target_consumed is False
    assert live.trigger_bar_start == "2026-09-18T15:00:00+00:00"


def test_live_watching_sip_first_break_resolves_continuation_without_option_lane(tmp_path: Path):
    from scripts.options_212r_prospective_collect import (
        _capture_live_first_boundary,
        _live_observation_from_cross,
    )
    from scripts.options_trigger_trade_timestamp_audit import parse_trade

    class FakeProvider:
        async def fetch_trades(self, *, symbol, start, end):
            return [
                parse_trade(
                    "SPY",
                    {
                        "t": "2026-09-18T15:00:01.000000000Z",
                        "p": 10.51,
                        "s": 100,
                        "x": "Q",
                        "c": ["@"],
                        "z": "C",
                        "i": 1,
                    },
                )
            ]

    obs = _watching_observation()
    cross = asyncio.run(
        _capture_live_first_boundary(
            FakeProvider(),
            observation=obs,
            observed_until=datetime(2026, 9, 18, 15, 1, tzinfo=UTC),
            setup_id="live-continuation",
            sip_trade_dir=tmp_path,
            persist_raw=False,
        )
    )
    live = _live_observation_from_cross(
        obs,
        cross_evidence=cross,
        history_30m=_history_30m_for_live_212(),
    )
    assert live.family == "STRAT_212_CONTINUATION"
    assert live.subtype == "CONTINUATION"
    assert live.direction == "LONG"
    assert live.source_target is None
    assert live.source_target_r is None


def test_journal_rejects_prior_collector_version(tmp_path: Path):
    path = tmp_path / "evidence.jsonl"
    row = {
        "record_type": "ARMED",
        "collector_id": COLLECTOR_ID,
        "collector_version": "212r-collector-v0.2",
        "setup_id": "abc",
        "observed_at": "2026-09-18T15:01:00+00:00",
        "observation": {"setup_fingerprint": "fp1"},
    }
    path.write_text(json.dumps(row) + "\n")
    with pytest.raises(RuntimeError, match="journal_collector_version_mismatch"):
        _load_journal(path)


def test_shared_sip_audit_dependency_has_no_execution_or_broker_imports():
    source = Path("scripts/options_trigger_trade_timestamp_audit.py").read_text()
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = ("execution", "risk", "webhook", "notifications", "broker")
    assert not [name for name in imported if name.startswith(forbidden)]
