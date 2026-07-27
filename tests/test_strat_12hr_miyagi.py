"""
tests/test_strat_12hr_miyagi.py

12HR Miyagi 1-3-1 reversal — live-wiring readiness pass (2026-07-27).

Rules: docs/strategy-rules/12HR_Miyagi_Rules.md
Evidence: docs/strategy-rules/12HR_MIYAGI_CANONICAL_EVIDENCE_2026-07-26.md
          (coded-detector canonical study, PR #343 — MNQ 15 candidates /
          8 fills / 7W-1L / net $516.33 / PF 2.81; MES 19 candidates /
          10 fills / 8W-2L / net $198.85 / PF 1.98. The rules doc's own
          §9 table, n=13/n=20/$102.35/$25.78, is the OLDER pre-coded-
          detector manual study — explicitly labeled "non-reproducible"
          in the canonical evidence doc, not the current figure.)

HEADLINE FINDING under test: the evidence's own stop-reference formula
(research/detector_12hr_miyagi.py Step 7) has a confirmed lookahead defect
— see strategy/strat_12hr_miyagi.py's module docstring. This runtime
computes the stop causally, at the actual moment of entry, which
necessarily diverges from the evidenced stop values. Setup detection
(direction/trigger/target) is verified byte-identical to the research
detector.

Locks the pure state machine, its wiring into DecisionEngine
(canonical_4hr_only 5m-native path, generic-transform bypass,
ORB-continuation exemption, TRENDING-gate exemption with 3-way collision
safety, day-only exit contract), and the risk_rules.yaml activation
(MNQ+MES enabled, PAPER_ELIGIBLE).
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from context.market_context import (
    MarketState,
    OHLCData,
    ORBData,
    PreviousDayData,
    PriceData,
    TrendData,
    VolumeData,
    VWAPData,
)
from execution.day_only_exit import strategy_is_day_only
from risk.risk_engine import DailyState
from strategy.signal_engine import DecisionEngine
from strategy.strat_12hr_miyagi import advance_strat_12hr_miyagi

ET = ZoneInfo("America/New_York")
# Bar D (live) opens this date at 4AM. Bar C = prior day 4PM, Bar B = prior
# day 4AM, Bar A = 2 days prior 4PM, Bar Z = 2 days prior 4AM.
DAY = date(2026, 6, 17)  # a Wednesday
DAY_MINUS_1 = DAY - timedelta(days=1)
DAY_MINUS_2 = DAY - timedelta(days=2)


def _dt(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET)


def _bar(day: date, hour: int, minute: int, o, h, l, c) -> dict:
    return {"ts": _dt(day, hour, minute).isoformat(), "open": o, "high": h, "low": l, "close": c}


def _flat_5m_series(day: date, start_hour: int, end_hour: int, o, h, l, c) -> list[dict]:
    """A flat run of 5m bars covering [start_hour, end_hour) that, once
    12h-aggregated, produces a single bar with the given OHLC."""
    bars = []
    for i in range(0, (end_hour - start_hour) * 60, 5):
        hour = start_hour + i // 60
        minute = i % 60
        bars.append(_bar(day, hour, minute, o, h, l, c))
    # Ensure the extremes land somewhere in the series (first bar carries them)
    bars[0] = _bar(day, start_hour, 0, o, h, l, bars[0]["close"])
    return bars


def _bar_a_z_series() -> list[dict]:
    """Bar Z (4AM-4PM, day-2): inside reference. Bar A (4PM day-2 - 4AM
    day-1): inside bar, e.g. [100,110]."""
    bars = []
    bars += _flat_5m_series(DAY_MINUS_2, 4, 16, 95, 115, 90, 100)  # Bar Z [90,115]
    bars += _flat_5m_series(DAY_MINUS_2, 16, 24, 100, 110, 100, 105)  # Bar A [100,110] inside Z
    return bars


def _long_setup_bars() -> list[dict]:
    """1-3-1 pattern producing a LONG (CALLS) setup:
    Bar A [100,110]; Bar B (4AM day-1) outside A -> [90,120];
    Bar C (4PM day-1) inside B -> [95,105], trigger=100, T1=105 (bar_c high);
    Bar D live: 9:30 open BELOW bar_c low (95) -> 2D -> LONG (CALLS).
    """
    bars = _bar_a_z_series()
    bars += _flat_5m_series(DAY_MINUS_1, 4, 16, 100, 120, 90, 110)  # Bar B outside A
    bars += _flat_5m_series(DAY_MINUS_1, 16, 24, 102, 105, 95, 100)  # Bar C inside B
    # premarket 4:00-9:30 today: stays within Bar C range [95,105]
    for h in range(4, 9):
        bars.append(_bar(DAY, h, 0, 100, 102, 98, 100))
    bars.append(_bar(DAY, 9, 25, 100, 101, 99, 100))
    # 9:30 bar opens BELOW bar_c low (95) -> LONG
    bars.append(_bar(DAY, 9, 30, 90, 92, 88, 90))
    return bars


def _short_setup_bars() -> list[dict]:
    """Mirror of the LONG fixture: Bar D opens ABOVE bar_c high -> SHORT."""
    bars = _bar_a_z_series()
    bars += _flat_5m_series(DAY_MINUS_1, 4, 16, 100, 120, 90, 110)  # Bar B outside A
    bars += _flat_5m_series(DAY_MINUS_1, 16, 24, 102, 105, 95, 100)  # Bar C inside B
    for h in range(4, 9):
        bars.append(_bar(DAY, h, 0, 100, 102, 98, 100))
    bars.append(_bar(DAY, 9, 25, 100, 101, 99, 100))
    # 9:30 bar opens ABOVE bar_c high (105) -> SHORT
    bars.append(_bar(DAY, 9, 30, 110, 112, 108, 110))
    return bars


def _drive(bars_at_each_step: list[list[dict]], instrument: str = "MNQ"):
    state = {}
    candidate = None
    for cumulative in bars_at_each_step:
        ts = datetime.fromisoformat(cumulative[-1]["ts"])
        state, cand = advance_strat_12hr_miyagi(
            bars_5m=cumulative, current_bar_ts=ts, instrument=instrument,
            persisted_state=state,
        )
        if cand:
            candidate = cand
    return state, candidate


def _cumulative(bars: list[dict]) -> list[list[dict]]:
    return [bars[: i + 1] for i in range(len(bars))]


def _with_stop_reference_hour_and_trigger(bars: list[dict], *, long: bool) -> list[dict]:
    """Append the 10:00-11:59 gap (so a completed 60m stop-reference bar
    exists) plus a 12:00/12:05 crossing, matching the pure state-machine
    trigger tests. Shared by every DecisionEngine-level test that needs an
    actual TRIGGERED candidate rather than just an ARMED one."""
    out = list(bars)
    if long:
        for m in range(0, 60, 5):
            out.append(_bar(DAY, 10, m, 90, 92, 88, 90))
        for m in range(0, 60, 5):
            out.append(_bar(DAY, 11, m, 90, 94, 80, 92))
        out.append(_bar(DAY, 12, 0, 92, 98, 91, 97))
        out.append(_bar(DAY, 12, 5, 97, 102, 96, 101))
    else:
        for m in range(0, 60, 5):
            out.append(_bar(DAY, 10, m, 110, 112, 108, 110))
        for m in range(0, 60, 5):
            out.append(_bar(DAY, 11, m, 110, 118, 105, 108))
        out.append(_bar(DAY, 12, 0, 108, 109, 102, 103))
        out.append(_bar(DAY, 12, 5, 103, 104, 99, 100))
    return out


# ── pure state machine ───────────────────────────────────────────────────────


def test_long_setup_arms_at_930_with_correct_bracket():
    bars = _long_setup_bars()
    state, candidate = _drive(_cumulative(bars))
    assert state["status"] == "ARMED"
    assert state["direction"] == "LONG"
    assert state["trigger"] == 100.0  # (105+95)/2
    assert state["target"] == 105.0  # bar_c high
    assert state["target_2"] == 120.0  # bar_b high
    assert candidate is None  # not triggered yet — trigger not crossed


def test_short_setup_arms_at_930_with_correct_bracket():
    bars = _short_setup_bars()
    state, candidate = _drive(_cumulative(bars))
    assert state["status"] == "ARMED"
    assert state["direction"] == "SHORT"
    assert state["trigger"] == 100.0
    assert state["target"] == 95.0  # bar_c low
    assert state["target_2"] == 90.0  # bar_b low


def test_long_setup_triggers_and_stop_is_causal_not_snapshot():
    bars = _long_setup_bars()
    # 10:00-10:55 hour: flat, doesn't cross trigger=100 (price stays 90-92 area is below, need to rise back to 100)
    for m in range(0, 60, 5):
        bars.append(_bar(DAY, 10, m, 90, 92, 88, 90))
    # 11:00 hour completes with a specific range -> becomes the "last completed hour" stop reference once entry happens after 12:00
    for m in range(0, 60, 5):
        bars.append(_bar(DAY, 11, m, 90, 94, 80, 92))  # 11:00 hour range [80,94]
    # 12:00-12:10: price rises and crosses trigger (100)
    bars.append(_bar(DAY, 12, 0, 92, 98, 91, 97))
    bars.append(_bar(DAY, 12, 5, 97, 102, 96, 101))  # crosses 100
    state, candidate = _drive(_cumulative(bars))
    assert state["status"] == "TRIGGERED"
    assert candidate["direction"] == "LONG"
    assert candidate["entry"] == 100.0  # fills at trigger, no gap/IOC cap
    assert candidate["target"] == 105.0
    # causal stop = the last FULLY COMPLETED 60m bar before entry (close at
    # 12:10) = the 11:00-12:00 hour -> low = 80 (LONG stop = completed bar's low)
    assert candidate["stop"] == 80.0
    assert candidate["stop_bar_ts"] == _dt(DAY, 11, 0)


def test_short_setup_triggers_correctly():
    bars = _short_setup_bars()
    for m in range(0, 60, 5):
        bars.append(_bar(DAY, 10, m, 110, 112, 108, 110))
    for m in range(0, 60, 5):
        bars.append(_bar(DAY, 11, m, 110, 118, 105, 108))  # 11:00 hour range [105,118]
    bars.append(_bar(DAY, 12, 0, 108, 109, 102, 103))
    bars.append(_bar(DAY, 12, 5, 103, 104, 99, 100))  # crosses trigger 100 (low<=100)
    state, candidate = _drive(_cumulative(bars))
    assert state["status"] == "TRIGGERED"
    assert candidate["direction"] == "SHORT"
    assert candidate["entry"] == 100.0
    assert candidate["target"] == 95.0
    assert candidate["stop"] == 118.0  # SHORT stop = completed bar's high


def test_bar_c_not_inside_bar_b_rejects():
    bars = _bar_a_z_series()
    bars += _flat_5m_series(DAY_MINUS_1, 4, 16, 100, 120, 90, 110)  # Bar B [90,120]
    bars += _flat_5m_series(DAY_MINUS_1, 16, 24, 102, 125, 95, 100)  # Bar C NOT inside B (high=125>120)
    for h in range(4, 9):
        bars.append(_bar(DAY, h, 0, 100, 102, 98, 100))
    bars.append(_bar(DAY, 9, 30, 90, 92, 88, 90))
    state, candidate = _drive(_cumulative(bars))
    assert state["status"] == "INVALIDATED"
    assert state["invalidation"] == "BAR_C_NOT_INSIDE_BAR_B"


def test_bar_b_not_outside_bar_a_rejects():
    bars = _bar_a_z_series()  # Bar A [100,110]
    bars += _flat_5m_series(DAY_MINUS_1, 4, 16, 100, 108, 95, 105)  # Bar B NOT outside A (high 108<110)
    bars += _flat_5m_series(DAY_MINUS_1, 16, 24, 100, 104, 96, 100)  # Bar C
    for h in range(4, 9):
        bars.append(_bar(DAY, h, 0, 100, 102, 98, 100))
    bars.append(_bar(DAY, 9, 30, 90, 92, 88, 90))
    state, candidate = _drive(_cumulative(bars))
    assert state["status"] == "INVALIDATED"
    assert state["invalidation"] == "BAR_B_NOT_OUTSIDE_BAR_A"


def test_bar_a_not_inside_bar_z_rejects():
    bars = []
    bars += _flat_5m_series(DAY_MINUS_2, 4, 16, 104, 108, 102, 105)  # Bar Z [102,108] (narrow)
    bars += _flat_5m_series(DAY_MINUS_2, 16, 24, 100, 110, 100, 105)  # Bar A [100,110] NOT inside narrow Z
    bars += _flat_5m_series(DAY_MINUS_1, 4, 16, 100, 120, 90, 110)  # Bar B [90,120] outside A
    bars += _flat_5m_series(DAY_MINUS_1, 16, 24, 102, 105, 95, 100)  # Bar C [95,105] inside B
    for h in range(4, 9):
        bars.append(_bar(DAY, h, 0, 100, 102, 98, 100))
    bars.append(_bar(DAY, 9, 30, 90, 92, 88, 90))
    state, candidate = _drive(_cumulative(bars))
    assert state["status"] == "INVALIDATED"
    assert state["invalidation"] == "BAR_A_NOT_INSIDE_BAR_Z"


def test_candle3_becomes_outside_bar_premarket_invalidates():
    bars = _bar_a_z_series()
    bars += _flat_5m_series(DAY_MINUS_1, 4, 16, 100, 120, 90, 110)
    bars += _flat_5m_series(DAY_MINUS_1, 16, 24, 102, 105, 95, 100)  # Bar C [95,105]
    for h in range(4, 9):
        bars.append(_bar(DAY, h, 0, 100, 102, 98, 100))
    # a single premarket bar engulfs Bar C's range (high>105 AND low<95)
    bars.append(_bar(DAY, 9, 0, 100, 108, 92, 100))
    bars.append(_bar(DAY, 9, 30, 90, 92, 88, 90))
    state, candidate = _drive(_cumulative(bars))
    assert state["status"] == "INVALIDATED"
    assert state["invalidation"] == "CANDLE3_BECAME_OUTSIDE_BAR"


def test_price_inside_trigger_range_at_930_rejects():
    bars = _bar_a_z_series()
    bars += _flat_5m_series(DAY_MINUS_1, 4, 16, 100, 120, 90, 110)
    bars += _flat_5m_series(DAY_MINUS_1, 16, 24, 102, 105, 95, 100)  # Bar C [95,105]
    for h in range(4, 9):
        bars.append(_bar(DAY, h, 0, 100, 102, 98, 100))
    bars.append(_bar(DAY, 9, 25, 100, 101, 99, 100))
    # 9:30 open is BETWEEN bar_c low/high (95-105) -> no setup
    bars.append(_bar(DAY, 9, 30, 100, 101, 99, 100))
    state, candidate = _drive(_cumulative(bars))
    assert state["status"] == "INVALIDATED"
    assert state["invalidation"] == "PRICE_INSIDE_TRIGGER_RANGE_AT_0930"


def test_no_trigger_by_day_close_expires():
    bars = _long_setup_bars()
    for h in range(10, 16):
        for m in range(0, 60, 5):
            bars.append(_bar(DAY, h, m, 90, 92, 88, 90))  # never rises to trigger 100
    bars.append(_bar(DAY, 16, 0, 90, 92, 88, 90))
    state, candidate = _drive(_cumulative(bars))
    assert state["status"] == "EXPIRED"
    assert state["invalidation"] == "TRIGGER_NOT_HIT_BY_DAY_CLOSE"
    assert candidate is None


def test_setup_evaluates_exactly_once_at_930_boundary():
    """Regression: partial premarket/Bar-D data must never trip a spurious
    invalidation before the 9:30 evaluation boundary closes, ON THE TARGET
    DAY. (Earlier calendar days in this multi-day fixture legitimately
    resolve their OWN independent REFERENCE_DATA_MISSING at their OWN 9:30
    boundary — Bar Z's day has no prior days at all — which is correct,
    unrelated behavior for those days, not a premature evaluation of DAY's
    own setup; each day's distinct `trading_date` resets state indepen-
    dently, verified by only checking bars whose date equals DAY.)"""
    bars = _long_setup_bars()
    state = {}
    saw_forming_before_930 = True
    for cumulative in _cumulative(bars):
        ts = datetime.fromisoformat(cumulative[-1]["ts"])
        state, _ = advance_strat_12hr_miyagi(
            bars_5m=cumulative, current_bar_ts=ts, instrument="MNQ", persisted_state=state,
        )
        if ts.date() == DAY and ts < _dt(DAY, 9, 30) and state["status"] != "FORMING":
            saw_forming_before_930 = False
    assert saw_forming_before_930
    assert state["status"] == "ARMED"


def test_mnq_and_mes_supported_other_instruments_rejected():
    bars = _long_setup_bars()
    for instrument in ("MNQ", "MES"):
        state, _ = _drive(_cumulative(bars), instrument=instrument)
        assert state["status"] != "INVALIDATED" or state["invalidation"] != "UNSUPPORTED_INSTRUMENT"
    state, _ = _drive(_cumulative(bars), instrument="MGC")
    assert state["invalidation"] == "UNSUPPORTED_INSTRUMENT"


def test_setup_detection_matches_research_detector_on_real_data():
    """Differential parity against real historical signals (confirmed
    2026-07-27): 34/34 real signals match on direction/entry/target;
    stop_reference legitimately diverges on the majority (lookahead defect
    in the offline detector — see module docstring). This test locks the
    setup-detection parity claim using one of the confirmed real dates."""
    from research.bars_12hr_miyagi_loader import (
        load_5m_day, load_60m_bars_for_date, load_12h_bars_for_date,
        load_5m_premarket_window,
    )
    from research.detector_12hr_miyagi import detect_12hr_miyagi

    cache15 = "data/replay_polygon"
    cache5 = "data/replay_polygon_5m"
    d = date(2024, 8, 22)
    if not __import__("pathlib").Path(cache15).exists():
        pytest.skip("local Polygon data caches not present in this environment")

    all_bars = []
    cur = d - timedelta(days=3)
    while cur <= d:
        try:
            all_bars.extend(load_5m_day(cache5, "MNQ", cur))
        except Exception:
            pass
        cur += timedelta(days=1)
    all_bars.sort(key=lambda b: b["ts"])
    raw_bars = [
        {"ts": b["ts"].isoformat(), "open": b["open"], "high": b["high"],
         "low": b["low"], "close": b["close"]}
        for b in all_bars
    ]
    state = {}
    candidate = None
    t = datetime(d.year, d.month, d.day, 0, 0, tzinfo=ET)
    end_t = datetime(d.year, d.month, d.day, 23, 55, tzinfo=ET)
    while t <= end_t:
        state, cand = advance_strat_12hr_miyagi(
            bars_5m=raw_bars, current_bar_ts=t, instrument="MNQ", persisted_state=state,
        )
        if cand:
            candidate = cand
        t += timedelta(minutes=5)

    bars12 = load_12h_bars_for_date(cache15, "MNQ", d)
    bars60 = load_60m_bars_for_date(cache15, "MNQ", d)
    pm = load_5m_premarket_window(cache5, cache15, "MNQ", d)
    ref = detect_12hr_miyagi(bars12, pm["bars"], bars60, d, "MNQ")

    assert candidate is not None
    assert candidate["direction"] == ref["direction"]
    assert candidate["entry"] == ref["entry_trigger"]
    assert candidate["target"] == ref["target"]
    # Stop MATCHES on this specific date (entry occurred late enough that
    # the causal and offline-snapshot stops happen to coincide) — confirmed
    # 2026-07-27. This is NOT true for all dates (see module docstring).
    assert candidate["stop"] == ref["stop_reference"]


# ── day-only exit contract ───────────────────────────────────────────────────


def test_registered_as_day_only_strategy():
    assert strategy_is_day_only("strat_12hr_miyagi")


# ── DecisionEngine / runtime wiring ──────────────────────────────────────────


def _market_state(bars: list[dict], *, instrument: str = "MNQ") -> MarketState:
    current = bars[-1]
    ts = datetime.fromisoformat(current["ts"])
    return MarketState(
        timestamp=ts,
        instrument=instrument,
        session="new_york",
        price=PriceData(last=current["close"], bid=current["close"], ask=current["close"]),
        ohlc=OHLCData(
            open=current["open"], high=current["high"],
            low=current["low"], close=current["close"], timeframe="5m",
        ),
        vwap=VWAPData(value=94, price_vs_vwap="above"),
        orb=ORBData(high=200, low=50, timeframe_minutes=15, status="above"),
        previous_day=PreviousDayData(high=100, low=90, close=95),
        volume=VolumeData(current_bar=1000, avg_bar=1000, relative=1),
        market_condition="TRENDING",
        trend=TrendData(direction="UP", strength="STRONG"),
        bar_history_5m=deepcopy(bars),
        canonical_4hr_only=True,
        raw={},
    )


def _enabled_cfg(config, **overrides):
    base = dict(
        enabled_concepts=["strat_12hr_miyagi"],
        disabled_concepts_per_instrument={},
        strategy_permission_gate_enabled=False,
        require_trending_condition=False,
        min_rr_ratio=0.0,
        min_target_points={"MNQ": 0, "MES": 0},
    )
    base.update(overrides)
    return replace(config, **base)


def _armed_miyagi_state(day: date = DAY) -> dict:
    return {
        "trading_date": day.isoformat(), "status": "ARMED", "direction": "LONG",
        "trigger": 100.0, "target": 105.0, "target_2": 120.0,
        "bar_c_high": 105.0, "bar_c_low": 95.0, "bar_b_high": 120.0, "bar_b_low": 90.0,
        "setup_bar_ts": _dt(day, 4, 0).isoformat(),
        "expires_at": _dt(day, 16, 0).isoformat(), "invalidation": None,
    }


def test_decision_engine_produces_trade_from_5m_feed(config):
    cfg = _enabled_cfg(config)
    bars = _with_stop_reference_hour_and_trigger(_long_setup_bars(), long=True)
    state = _market_state(bars)
    daily = DailyState()
    daily.strat_12hr_miyagi_state["MNQ"] = _armed_miyagi_state()
    out = DecisionEngine(cfg).evaluate(state, daily)
    assert out.decision == "TRADE"
    assert out.setup.strategy == "strat_12hr_miyagi"
    assert out.setup.direction == "LONG"
    assert out.setup.entry == 100.0
    assert out.setup.target == 105.0
    assert out.setup.stop == 80.0  # causal: the completed 11:00 hour's low


def test_generic_transforms_do_not_mutate_the_canonical_bracket(config):
    cfg = _enabled_cfg(config, min_target_points={"MNQ": 9999, "MES": 9999})
    bars = _with_stop_reference_hour_and_trigger(_long_setup_bars(), long=True)
    state = _market_state(bars)
    daily = DailyState()
    daily.strat_12hr_miyagi_state["MNQ"] = _armed_miyagi_state()
    out = DecisionEngine(cfg).evaluate(state, daily)
    assert out.decision == "TRADE"
    assert out.setup.target == 105.0  # NOT expanded to a 9999-point minimum


def test_cross_instrument_state_does_not_leak(config):
    cfg = _enabled_cfg(config)
    daily = DailyState()
    bars = _long_setup_bars()
    DecisionEngine(cfg).evaluate(_market_state(bars), daily)
    assert "MNQ" in daily.strat_12hr_miyagi_state
    assert "MES" not in daily.strat_12hr_miyagi_state


def test_mes_also_wired_end_to_end(config):
    cfg = _enabled_cfg(config)
    bars = _with_stop_reference_hour_and_trigger(_short_setup_bars(), long=False)
    state = _market_state(bars, instrument="MES")
    daily = DailyState()
    daily.strat_12hr_miyagi_state["MES"] = {
        **_armed_miyagi_state(), "direction": "SHORT", "target": 95.0, "target_2": 90.0,
    }
    out = DecisionEngine(cfg).evaluate(state, daily)
    assert out.decision == "TRADE"
    assert out.setup.strategy == "strat_12hr_miyagi"
    assert out.setup.direction == "SHORT"


# ── TRENDING gate exemption (3-way collision safety) ────────────────────────


def test_trending_gate_exempt_for_miyagi_alone(config):
    cfg = _enabled_cfg(config, require_trending_condition=True)
    bars = _with_stop_reference_hour_and_trigger(_long_setup_bars(), long=True)
    state = _market_state(bars)
    state.market_condition = "RANGE_BOUND"
    daily = DailyState()
    daily.strat_12hr_miyagi_state["MNQ"] = _armed_miyagi_state()
    out = DecisionEngine(cfg).evaluate(state, daily)
    assert out.decision == "TRADE"
    assert out.setup.strategy == "strat_12hr_miyagi"


def test_trending_gate_exempt_helper_handles_three_way_collision(config):
    """Both strat_322_first_live and strat_12hr_miyagi are exempt;
    strat_4hr_retrigger is not. The helper must exempt when exactly one
    5-minute-native candidate exists (regardless of WHICH exempt strategy),
    and never exempt when two or more exist simultaneously — including two
    EXEMPT strategies both firing on the same bar (structurally near
    impossible given wildly different setups, but the helper must still be
    correct: it counts ALL fired candidates, not just non-exempt ones)."""
    engine = DecisionEngine(config=_enabled_cfg(config))
    bars = _long_setup_bars()
    bars.append(_bar(DAY, 12, 0, 92, 98, 91, 97))
    bars.append(_bar(DAY, 12, 5, 97, 102, 96, 101))
    state = _market_state(bars)

    state.strat_322_first_live_candidate = None
    state.strat_12hr_miyagi_candidate = None
    state.four_hr_retrigger_candidate = None
    assert engine._trending_gate_exempt_candidate(state) is False

    state.strat_12hr_miyagi_candidate = {"direction": "LONG"}
    assert engine._trending_gate_exempt_candidate(state) is True  # miyagi alone

    state.strat_322_first_live_candidate = {"direction": "LONG"}
    assert engine._trending_gate_exempt_candidate(state) is False  # 2 exempt candidates -> off

    state.strat_322_first_live_candidate = None
    state.four_hr_retrigger_candidate = {"direction": "LONG"}
    assert engine._trending_gate_exempt_candidate(state) is False  # miyagi + non-exempt -> off

    state.strat_12hr_miyagi_candidate = None
    assert engine._trending_gate_exempt_candidate(state) is False  # 4HR alone, not exempt


def test_trending_gate_still_blocks_four_hr_retrigger_alone_unaffected(config):
    """Explicit regression lock: adding Miyagi's exemption must not change
    strat_4hr_retrigger's own unrelated, already-live TRENDING-gated
    behavior in any way."""
    cfg = _enabled_cfg(
        config, enabled_concepts=["strat_4hr_retrigger"], require_trending_condition=True,
    )
    bars = _long_setup_bars()
    state = _market_state(bars)
    state.market_condition = "RANGE_BOUND"
    state.four_hr_retrigger_candidate = {
        "direction": "LONG", "entry": 100.0, "stop": 95.0, "target": 110.0,
        "entry_time": state.timestamp,
    }
    daily = DailyState()
    daily.four_hr_retrigger_state["MNQ"] = {"status": "TRIGGERED"}
    out = DecisionEngine(cfg).evaluate(state, daily)
    assert out.decision == "NO_TRADE"
    assert "MARKET_CONDITION_NOT_TRENDING" in out.failed_gates


def test_blocked_candidate_audit_visible_when_exemption_does_not_apply(config):
    cfg = _enabled_cfg(config, require_trending_condition=True)
    bars = _with_stop_reference_hour_and_trigger(_long_setup_bars(), long=True)
    state = _market_state(bars)
    state.market_condition = "RANGE_BOUND"
    state.canonical_4hr_only = False  # forces the exemption helper False
    daily = DailyState()
    daily.strat_12hr_miyagi_state["MNQ"] = _armed_miyagi_state()
    out = DecisionEngine(cfg).evaluate(state, daily)
    assert out.decision == "NO_TRADE"
    rows = out.blocked_candidate_audit["candidates"]
    row = next(r for r in rows if r["strategy"] == "strat_12hr_miyagi")
    assert row["blocking_gate"] == "MARKET_CONDITION_NOT_TRENDING"
    assert row["direction"] == "LONG"
    assert row["entry"] == 100.0
    assert "blocked_candidate_audit" in out.to_dict()


# ── risk_rules.yaml activation ───────────────────────────────────────────────


def test_risk_rules_yaml_enables_mnq_and_mes():
    from config.settings import load_config

    cfg = load_config()
    assert "strat_12hr_miyagi" in cfg.enabled_concepts
    assert "strat_12hr_miyagi" not in cfg.disabled_concepts_per_instrument.get("MNQ", [])
    assert "strat_12hr_miyagi" not in cfg.disabled_concepts_per_instrument.get("MES", [])
    assert cfg.strategy_status.get("strat_12hr_miyagi") == "PAPER_ELIGIBLE"
