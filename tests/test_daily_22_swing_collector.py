"""Regression coverage for the isolated MNQ Daily 2-2 swing paper lane."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from config.settings import load_config
from context import daily_22_swing_collector as lane

ET = ZoneInfo("America/New_York")


def _cfg():
    cfg = copy.copy(load_config())
    cfg.wide_stop_ledger_mode = "paper_sim"
    cfg.wide_stop_ledger_epoch_start = "2026-09-01T00:00:00+00:00"
    return cfg


def _bar(ts, o, h, l, c):
    return {
        "ts": ts.isoformat(),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 1000,
    }


def _fixture_bars():
    # Trading day 9/7: H100/L90.
    d1 = datetime(2026, 9, 6, 18, 0, tzinfo=ET)
    # Trading day 9/8: H110/L92 -> 2U relative to 9/7.
    d2 = datetime(2026, 9, 7, 18, 0, tzinfo=ET)
    # Trading day 9/9 first-break bar: takes 110 but closes back at 109.5.
    d3 = datetime(2026, 9, 8, 18, 0, tzinfo=ET)
    return [
        _bar(d1, 95, 100, 90, 96),
        _bar(d1 + timedelta(minutes=5), 96, 99, 91, 97),
        _bar(d2, 98, 105, 94, 103),
        _bar(d2 + timedelta(minutes=5), 103, 110, 92, 106),
        _bar(d3, 109, 111, 108, 109.5),
    ]


def _payload(ts, *, high=111.0, low=108.0, close=109.5, direction="UP"):
    return SimpleNamespace(
        ticker="MNQ1!",
        timestamp=ts.isoformat(),
        timeframe="5m",
        open=109.0,
        high=high,
        low=low,
        close=close,
        volume=1000,
        avg_volume=1000,
        reconstructed_rel_vol=1.0,
        market_condition="TRENDING",
        trend_strength="STRONG",
        trend_direction=direction,
        ema_9=108.0 if direction == "UP" else 112.0,
        ema_21=106.0 if direction == "UP" else 114.0,
        ema_55=104.0 if direction == "UP" else 116.0,
    )


def test_globex_trading_day_and_maintenance_gap():
    assert lane._trading_day(datetime(2026, 9, 8, 18, 0, tzinfo=ET)).isoformat() == "2026-09-09"
    assert lane._trading_day(datetime(2026, 9, 9, 16, 55, tzinfo=ET)).isoformat() == "2026-09-09"
    assert lane._trading_day(datetime(2026, 9, 9, 17, 30, tzinfo=ET)) is None


def test_daily_22_uses_first_boundary_break_and_never_backfills(monkeypatch):
    monkeypatch.setattr(lane, "MIN_COMPLETE_SESSION_BARS", 2)
    bars = _fixture_bars()
    current = datetime.fromisoformat(bars[-1]["ts"])
    candidate = lane._candidate_for_current_bar(bars, current)
    assert candidate is not None
    assert candidate["status"] == "CANDIDATE"
    assert candidate["direction"] == "LONG"
    assert candidate["planned_entry"] == 110.25
    assert candidate["stop"] == 91.75

    # Once a later bar arrives, the earlier first break is not backfilled.
    later = current + timedelta(minutes=5)
    bars.append(_bar(later, 109.5, 112.0, 109.0, 111.0))
    assert lane._candidate_for_current_bar(bars, later) is None


def test_same_bar_two_sided_first_break_fails_closed(monkeypatch):
    monkeypatch.setattr(lane, "MIN_COMPLETE_SESSION_BARS", 2)
    bars = _fixture_bars()
    bars[-1]["low"] = 91.0  # same 5m bar takes previous high and low
    current = datetime.fromisoformat(bars[-1]["ts"])
    result = lane._candidate_for_current_bar(bars, current)
    assert result is not None
    assert result["status"] == "NO_TRADE"
    assert result["reason"] == "AMBIGUOUS_FIRST_BOUNDARY_BREAK"


def test_context_gate_is_the_preregistered_strict_bundle():
    ts = datetime(2026, 9, 8, 18, 0, tzinfo=ET)
    ok, reason, audit = lane._context_gate(_payload(ts), "LONG")
    assert ok and reason == "CONTEXT_APPROVED"
    assert audit["relative_volume"] == 1.0
    bad = _payload(ts)
    bad.market_condition = "RANGE_BOUND"
    ok, reason, _ = lane._context_gate(bad, "LONG")
    assert not ok and reason == "MARKET_NOT_TRENDING"


def test_ioc_admission_uses_current_market_and_actual_fill_rr(monkeypatch):
    monkeypatch.setattr(lane, "MIN_COMPLETE_SESSION_BARS", 2)
    bars = _fixture_bars()
    current = datetime.fromisoformat(bars[-1]["ts"])
    candidate = lane._candidate_for_current_bar(bars, current)
    fill, reason = lane._expected_ioc_fill(candidate, market=109.5)
    assert reason == "IOC_MARKETABLE"
    assert fill == 109.75  # one adverse tick from decision-close market
    assert lane._actual_rr(candidate, fill) >= lane.MIN_ACTUAL_RR
    fill, reason = lane._expected_ioc_fill(candidate, market=113.0)
    assert fill is None and reason == "ENTRY_NOT_FILLED"


def test_process_opens_only_hypothetical_position_and_persists_it(tmp_path, monkeypatch):
    monkeypatch.setattr(lane, "MIN_COMPLETE_SESSION_BARS", 2)
    bars = _fixture_bars()
    current = datetime.fromisoformat(bars[-1]["ts"])
    events = lane.process_five_min_bar(
        payload=_payload(current), cfg=_cfg(), bars_5m=bars, log_dir=tmp_path
    )
    assert events and events[-1]["lane_result"] == "OPEN"
    assert events[-1]["external_broker"] is False
    state = lane._load_state(tmp_path, lane._epoch(_cfg()))
    assert state["position"] is not None
    assert state["balance"] == lane.STARTING_BALANCE
    assert lane._audit_path(tmp_path).exists()


def test_swing_position_is_not_flattened_at_eod_or_session_change(tmp_path, monkeypatch):
    monkeypatch.setattr(lane, "MIN_COMPLETE_SESSION_BARS", 2)
    bars = _fixture_bars()
    current = datetime.fromisoformat(bars[-1]["ts"])
    lane.process_five_min_bar(
        payload=_payload(current), cfg=_cfg(), bars_5m=bars, log_dir=tmp_path
    )
    # Next trading day, well past an intraday EOD, but neither stop nor target touched.
    later = current + timedelta(days=1)
    neutral = _payload(later, high=120.0, low=100.0, close=110.0)
    lane.process_five_min_bar(
        payload=neutral, cfg=_cfg(), bars_5m=bars + [_bar(later, 110, 120, 100, 110)], log_dir=tmp_path
    )
    state = lane._load_state(tmp_path, lane._epoch(_cfg()))
    assert state["position"] is not None


def test_same_bar_stop_and_target_resolves_pessimistically(tmp_path, monkeypatch):
    monkeypatch.setattr(lane, "MIN_COMPLETE_SESSION_BARS", 2)
    bars = _fixture_bars()
    current = datetime.fromisoformat(bars[-1]["ts"])
    lane.process_five_min_bar(
        payload=_payload(current), cfg=_cfg(), bars_5m=bars, log_dir=tmp_path
    )
    next_bar = current + timedelta(minutes=5)
    both = _payload(next_bar, high=150.0, low=90.0, close=110.0)
    events = lane.process_five_min_bar(
        payload=both,
        cfg=_cfg(),
        bars_5m=bars + [_bar(next_bar, 109.5, 150, 90, 110)],
        log_dir=tmp_path,
    )
    outcome = next(row for row in events if row["collector_event"] == "OUTCOME")
    assert outcome["outcome_result"] == "LOSS"
    assert outcome["exit_reason"] == "STOP_HIT"
    assert outcome["net_pnl_dollars"] < 0


def test_30_percent_drawdown_is_a_hard_paper_halt(tmp_path):
    epoch = lane._epoch(_cfg())
    state = lane._empty_state(epoch)
    state["balance"] = 4_300.0
    state["peak"] = 5_000.0
    state["position"] = {
        "candidate_key": "loss-test",
        "direction": "LONG",
        "entry": 1000.0,
        "stop": 500.0,
        "target": 2000.0,
        "entry_time": "2026-09-08T18:05:00-04:00",
        "paper_order_id": "PAPER-loss-test",
        "mae_points": 0.0,
        "mfe_points": 0.0,
    }
    payload = _payload(
        datetime(2026, 9, 8, 18, 5, tzinfo=ET), high=1001.0, low=499.0, close=600.0
    )
    row = lane._resolve_position(state, payload, datetime.fromisoformat(payload.timestamp))
    assert row is not None
    assert state["halted"] is True
    assert row["hard_halt"] is True
    assert row["drawdown_percent"] >= 30.0


def test_risk_constants_pin_the_5k_demo_contract():
    assert lane.STARTING_BALANCE == 5_000.0
    assert lane.CONTRACTS == 1
    assert lane.MAX_PLANNED_RISK_DOLLARS == 1_750.0
    assert lane.MAX_DRAWDOWN == 0.30
    assert lane.IOC_TOLERANCE_TICKS == 8.0
    assert lane.COMMISSION_ROUND_TRIP == 1.48
