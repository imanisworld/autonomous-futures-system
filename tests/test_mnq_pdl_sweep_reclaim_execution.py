from datetime import datetime, timedelta, timezone

from alert_ranker.causal_bars import Bar
from research.mnq_pdl_sweep_reclaim_execution import (
    Candidate,
    geometry,
    simulate_candidate,
)


def _bar(ts, o, h, l, c):
    return Bar(start=ts, open=o, high=h, low=l, close=c, volume=100.0)


def _candidate(**overrides):
    base = dict(
        session_date="2026-01-05",
        half="H2",
        episode_id="ep1",
        event_bar_start="2026-01-05T15:00:00+00:00",
        event_idx=0,
        session_rank=1,
        planned_entry=100.0,
        sweep_low=98.0,
        pdl=99.0,
        pdh=110.0,
        vwap_at_signal=105.0,
        volume_ratio=1.0,
    )
    base.update(overrides)
    return Candidate(**base)


def test_geometry_uses_wick_minus_one_tick_and_no_tuned_stop():
    stop, target = geometry(_candidate(), "TARGET_VWAP_AT_SIGNAL")
    assert stop == 97.75
    assert target == 105.0


def test_ioc_no_fill_when_next_open_exceeds_32_tick_cap():
    ts = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    bars = [
        _bar(ts, 99, 101, 97.5, 100),
        _bar(ts + timedelta(minutes=5), 108.25, 109, 108, 108.5),
    ]
    row = simulate_candidate(
        _candidate(), bars, target_model="TARGET_VWAP_AT_SIGNAL", slippage_ticks=1.0
    )
    assert row.status == "ENTRY_NOT_FILLED"


def test_pessimistic_same_bar_resolution_and_risk_fields():
    ts = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    bars = [
        _bar(ts, 99, 101, 97.5, 100),
        # fills near 100 then touches both 97.75 stop and 105 target
        _bar(ts + timedelta(minutes=5), 100, 106, 97, 101),
    ]
    row = simulate_candidate(
        _candidate(), bars, target_model="TARGET_VWAP_AT_SIGNAL", slippage_ticks=1.0
    )
    assert row.status == "RESOLVED"
    assert row.result == "LOSS"
    assert row.exit_reason == "STOP_HIT"
    assert row.stop_ticks_actual is not None
    assert row.rr_actual is not None


def test_target_below_entry_fails_closed():
    ts = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    bars = [
        _bar(ts, 99, 101, 97.5, 100),
        _bar(ts + timedelta(minutes=5), 100, 101, 99, 100),
    ]
    row = simulate_candidate(
        _candidate(vwap_at_signal=99.5), bars,
        target_model="TARGET_VWAP_AT_SIGNAL", slippage_ticks=1.0
    )
    assert row.status == "GEOMETRY_INVALID"
