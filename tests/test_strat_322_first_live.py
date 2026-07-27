"""
tests/test_strat_322_first_live.py

MNQ 60M 3-2-2 First Live — live-wiring readiness pass (2026-07-27).

Rules: docs/strategy-rules/60M_322_FirstLive_Rules.md
Evidence: docs/strategy-rules/60M_322_EXPANDED_EVIDENCE_2026-07-26.md
          (34 candidates, 21 fills, 20 resolved, net $1,595.70, PF 10.36)

Locks the pure state machine (strategy/strat_322_first_live.py), its wiring
into DecisionEngine (canonical_4hr_only 5m-native path, generic-transform
bypass, ORB-continuation exemption, day-only exit contract), and the
risk_rules.yaml activation (MNQ-only enabled_concepts + strategy_permission
PAPER_ELIGIBLE, MES explicitly excluded).
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
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
from strategy.strat_322_first_live import advance_strat_322_first_live

ET = ZoneInfo("America/New_York")
DAY = date(2026, 6, 15)  # a Monday


def _dt(hour: int, minute: int = 0, day: date = DAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET)


def _bar(hour: int, minute: int, o, h, l, c, *, day: date = DAY) -> dict:
    return {
        "ts": _dt(hour, minute, day).isoformat(),
        "open": o, "high": h, "low": l, "close": c,
    }


def _long_day_bars(*, include_10am: bool = True) -> list[dict]:
    """7AM [100,110]; 8AM outside [95,115]; 9AM 2D->LONG trigger=104 stop=90
    target=115 (8AM high); 10:15 bar crosses trigger (non-gap) at 104.5 high."""
    bars = []
    for i in range(12):
        bars.append(_bar(7, i * 5, 100, 110 if i == 0 else 105, 100, 103))
    for i in range(12):
        bars.append(
            _bar(8, i * 5, 105, 115 if i == 0 else 108, 95 if i == 1 else 100, 103)
        )
    for i in range(12):
        bars.append(_bar(9, i * 5, 103, 104, 90 if i == 5 else 100, 92))
    if include_10am:
        bars.append(_bar(10, 0, 100, 101, 99, 100))
        bars.append(_bar(10, 5, 100, 105, 99, 104.5))
    return bars


def _short_day_bars() -> list[dict]:
    """7AM [100,110]; 8AM outside [95,115]; 9AM 2U->SHORT trigger=96 stop=125
    target=95 (8AM low); 10:05 bar crosses trigger (non-gap) at 95.5 low."""
    bars = []
    for i in range(12):
        bars.append(_bar(7, i * 5, 100, 110 if i == 0 else 105, 100, 103))
    for i in range(12):
        bars.append(
            _bar(8, i * 5, 105, 115 if i == 0 else 108, 95 if i == 1 else 100, 103)
        )
    for i in range(12):  # breaks ABOVE 8AM high (115) via i==5's 125 high
        bars.append(_bar(9, i * 5, 103, 125 if i == 5 else 104, 96, 108))
    bars.append(_bar(10, 0, 100, 101, 99, 100))       # doesn't gap (open 100 > trigger 96)
    bars.append(_bar(10, 5, 100, 101, 95.5, 96))      # crosses below trigger 96
    return bars


def _drive(bars_at_each_step: list[list[dict]], instrument: str = "MNQ"):
    """Advance the pure state machine bar-by-bar; return (final_state, candidate)."""
    state = {}
    candidate = None
    for cumulative in bars_at_each_step:
        ts = datetime.fromisoformat(cumulative[-1]["ts"])
        state, cand = advance_strat_322_first_live(
            bars_5m=cumulative, current_bar_ts=ts, instrument=instrument,
            persisted_state=state,
        )
        if cand:
            candidate = cand
    return state, candidate


def _cumulative(bars: list[dict]) -> list[list[dict]]:
    return [bars[: i + 1] for i in range(len(bars))]


# ── pure state machine ───────────────────────────────────────────────────────


def test_long_setup_triggers_at_first_5m_crossing_not_gap():
    bars = _long_day_bars()
    state, candidate = _drive(_cumulative(bars))
    assert state["status"] == "TRIGGERED"
    assert candidate["direction"] == "LONG"
    assert candidate["entry"] == 104.0   # trigger price, not the 105 bar high
    assert candidate["stop"] == 90.0
    assert candidate["target"] == 115.0
    assert candidate["gap_open"] is False
    assert candidate["entry_time"] == _dt(10, 10)  # close of the 10:05 bar


def test_short_setup_triggers_correctly():
    bars = _short_day_bars()
    state, candidate = _drive(_cumulative(bars))
    assert state["status"] == "TRIGGERED"
    assert candidate["direction"] == "SHORT"
    assert candidate["entry"] == 96.0
    assert candidate["stop"] == 125.0
    assert candidate["target"] == 95.0
    assert candidate["gap_open"] is False


def test_gap_open_entry_uses_bar_open_not_trigger():
    bars = _long_day_bars(include_10am=False)
    bars.append(_bar(10, 0, 105, 106, 104.5, 105.5))  # opens ABOVE trigger 104
    state, candidate = _drive(_cumulative(bars))
    assert state["status"] == "TRIGGERED"
    assert candidate["gap_open"] is True
    assert candidate["entry"] == 105.0  # the bar's OPEN, not the 104 trigger
    assert candidate["entry_time"] == _dt(10, 5)  # close of the exact 10:00 bar


def test_eight_am_not_outside_bar_rejects():
    bars = []
    for i in range(12):
        bars.append(_bar(7, i * 5, 100, 110 if i == 0 else 105, 100, 103))
    for i in range(12):  # 8AM inside 7AM's range — not an outside bar
        bars.append(_bar(8, i * 5, 103, 106, 102, 104))
    for i in range(12):
        bars.append(_bar(9, i * 5, 103, 120, 80, 92))
    bars.append(_bar(10, 0, 100, 101, 99, 100))  # reach the 10:00 evaluation boundary
    state, candidate = _drive(_cumulative(bars))
    assert state["status"] == "INVALIDATED"
    assert state["invalidation"] == "EIGHT_AM_NOT_OUTSIDE_BAR"
    assert candidate is None


def test_nine_am_not_directional_rejects():
    bars = []
    for i in range(12):
        bars.append(_bar(7, i * 5, 100, 110 if i == 0 else 105, 100, 103))
    for i in range(12):
        bars.append(
            _bar(8, i * 5, 105, 115 if i == 0 else 108, 95 if i == 1 else 100, 103)
        )
    for i in range(12):  # 9AM breaks BOTH boundaries — outside bar, not directional
        bars.append(_bar(9, i * 5, 103, 120 if i == 0 else 104, 80 if i == 1 else 100, 92))
    bars.append(_bar(10, 0, 100, 101, 99, 100))  # reach the 10:00 evaluation boundary
    state, candidate = _drive(_cumulative(bars))
    assert state["status"] == "INVALIDATED"
    assert state["invalidation"] == "NINE_AM_NOT_DIRECTIONAL"


def test_no_break_by_eleven_am_expires():
    bars = _long_day_bars(include_10am=False)
    for i in range(12):  # 10:00..10:55: never touches trigger 104
        bars.append(_bar(10, i * 5, 100, 101, 99, 100))
    bars.append(_bar(11, 0, 100, 101, 99, 100))  # 11:00 open — the expiry boundary
    state, candidate = _drive(_cumulative(bars))
    assert state["status"] == "EXPIRED"
    assert state["invalidation"] == "NO_BREAK_BY_11AM"
    assert candidate is None


def test_setup_detection_evaluates_exactly_once_at_ten_am_boundary():
    """Regression: partial 9AM data must never be evaluated before the bucket
    closes at 10:00 — evaluating incrementally would latch a spurious
    NINE_AM_NOT_DIRECTIONAL from an incomplete 9AM range."""
    bars = _long_day_bars(include_10am=False)
    bars.append(_bar(10, 0, 100, 101, 99, 100))  # reach the 10:00 evaluation boundary
    state = {}
    saw_forming_through_nine_am = True
    for cumulative in _cumulative(bars):
        ts = datetime.fromisoformat(cumulative[-1]["ts"])
        state, _ = advance_strat_322_first_live(
            bars_5m=cumulative, current_bar_ts=ts, instrument="MNQ",
            persisted_state=state,
        )
        if _dt(9, 0) <= ts < _dt(10, 0) and state["status"] != "FORMING":
            saw_forming_through_nine_am = False
    assert saw_forming_through_nine_am
    assert state["status"] == "ARMED"
    assert state["direction"] == "LONG"


def test_mnq_only_hard_guard():
    bars = _long_day_bars()
    state, candidate = _drive(_cumulative(bars), instrument="MES")
    assert state["status"] == "INVALIDATED"
    assert state["invalidation"] == "UNSUPPORTED_INSTRUMENT"
    assert candidate is None


def test_invalid_bracket_geometry_invalidates_never_trades():
    """A gap so severe the fill lands outside stop<entry<target must never
    produce a candidate — fail closed, not a distorted bracket."""
    bars = _long_day_bars(include_10am=False)
    bars.append(_bar(10, 0, 200, 205, 199, 201))  # gaps far past target (115)
    state, candidate = _drive(_cumulative(bars))
    assert state["status"] == "INVALIDATED"
    assert state["invalidation"] == "INVALID_ENTRY_BRACKET"
    assert candidate is None


def test_setup_detection_matches_research_detector():
    """Differential parity: the live state machine's 7/8/9AM classification
    must agree with research/detector_322_first_live.py on identical bars
    (entry mechanics intentionally differ — see module docstring)."""
    from research.detector_322_first_live import detect_322_first_live

    bars = _long_day_bars(include_10am=False)
    bars.append(_bar(10, 0, 105, 106, 104.5, 105.5))  # gap-open, matches gap test above
    state, candidate = _drive(_cumulative(bars))

    bars_60m = [
        {"ts": _dt(7, 0), "open": 100, "high": 110, "low": 100, "close": 103},
        {"ts": _dt(8, 0), "open": 105, "high": 115, "low": 95, "close": 103},
        {"ts": _dt(9, 0), "open": 103, "high": 104, "low": 90, "close": 92},
        {"ts": _dt(10, 0), "open": 105, "high": 106, "low": 104.5, "close": 105.5},
    ]
    reference = detect_322_first_live(bars_60m, DAY, "MNQ")
    assert reference["signal"] is True
    assert reference["direction"] == candidate["direction"]
    assert reference["entry_trigger"] == state["trigger"]
    assert reference["entry_price"] == candidate["entry"]
    assert reference["stop_reference"] == candidate["stop"]
    assert reference["target"] == candidate["target"]
    assert reference["gap_open"] == candidate["gap_open"]


# ── day-only exit contract ───────────────────────────────────────────────────


def test_registered_as_day_only_strategy():
    assert strategy_is_day_only("strat_322_first_live")
    assert not strategy_is_day_only("orb_reclaim")


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
        enabled_concepts=["strat_322_first_live"],
        disabled_concepts_per_instrument={},
        strategy_permission_gate_enabled=False,
        require_trending_condition=False,
        min_rr_ratio=0.0,
        min_target_points={"MNQ": 0},
    )
    base.update(overrides)
    return replace(config, **base)


def test_decision_engine_produces_trade_from_5m_feed(config):
    cfg = _enabled_cfg(config)
    bars = _long_day_bars(include_10am=False)
    bars.append(_bar(10, 0, 100, 101, 99, 100))
    state = _market_state(bars)
    out = DecisionEngine(cfg).evaluate(state, DailyState())
    assert out.decision != "TRADE"  # 10:00 bar alone hasn't crossed yet

    bars.append(_bar(10, 5, 100, 105, 99, 104.5))
    state = _market_state(bars)
    daily = DailyState()
    daily.strat_322_first_live_state["MNQ"] = {
        "trading_date": DAY.isoformat(), "status": "ARMED", "direction": "LONG",
        "trigger": 104.0, "stop": 90.0, "target": 115.0,
        "eight_am_high": 115.0, "eight_am_low": 95.0, "nine_am_range_points": 14.0,
        "setup_bar_ts": _dt(9, 0).isoformat(), "expires_at": _dt(11, 0).isoformat(),
        "invalidation": None,
    }
    out = DecisionEngine(cfg).evaluate(state, daily)
    assert out.decision == "TRADE"
    assert out.setup.strategy == "strat_322_first_live"
    assert out.setup.direction == "LONG"
    assert out.setup.entry == 104.0
    assert out.setup.stop == 90.0
    assert out.setup.target == 115.0


def test_generic_transforms_do_not_mutate_the_canonical_bracket(config):
    """min_target_points / advisory-bracket / reanchor must never touch this
    strategy's already-resolved formula (matches the strat_4hr_retrigger /
    STRAT_212 / STRAT_122 bypass)."""
    cfg = _enabled_cfg(config, min_target_points={"MNQ": 9999})
    bars = _long_day_bars(include_10am=False)
    bars.append(_bar(10, 5, 100, 105, 99, 104.5))
    state = _market_state(bars)
    daily = DailyState()
    daily.strat_322_first_live_state["MNQ"] = {
        "trading_date": DAY.isoformat(), "status": "ARMED", "direction": "LONG",
        "trigger": 104.0, "stop": 90.0, "target": 115.0,
        "eight_am_high": 115.0, "eight_am_low": 95.0, "nine_am_range_points": 14.0,
        "setup_bar_ts": _dt(9, 0).isoformat(), "expires_at": _dt(11, 0).isoformat(),
        "invalidation": None,
    }
    out = DecisionEngine(cfg).evaluate(state, daily)
    assert out.decision == "TRADE"
    assert out.setup.target == 115.0  # NOT expanded to a 9999-point minimum


def test_mes_never_produces_a_candidate_even_when_enabled(config):
    cfg = _enabled_cfg(config)
    bars = _long_day_bars()
    state = _market_state(bars, instrument="MES")
    daily = DailyState()
    out = DecisionEngine(cfg).evaluate(state, daily)
    assert out.decision != "TRADE" or out.setup.strategy != "strat_322_first_live"


def _armed_322_state(day: date = DAY) -> dict:
    return {
        "trading_date": day.isoformat(), "status": "ARMED", "direction": "LONG",
        "trigger": 104.0, "stop": 90.0, "target": 115.0,
        "eight_am_high": 115.0, "eight_am_low": 95.0, "nine_am_range_points": 14.0,
        "setup_bar_ts": _dt(9, 0, day).isoformat(),
        "expires_at": _dt(11, 0, day).isoformat(), "invalidation": None,
    }


def test_trending_gate_exempt_for_strat_322_alone(config):
    """Contract audit (2026-07-27, PR #359 review): the canonical rules doc,
    causal detector, and honest-fill replay that produced the evidence
    ($1,595.70/PF10.36) have ZERO market_condition/TRENDING dependency —
    verified via `grep -n "market_condition\\|TRENDING\\|trend_direction\\|
    trend_strength" research/detector_322_first_live.py research/
    replay_322_honest_fill.py research/reconcile_322_first_live.py` (zero
    matches). The system-wide TRENDING gate's own justification comment
    cites a 555-day replay that predates this strategy's existence — a
    global default, not part of this strategy's contract. This locks the
    exemption: a non-TRENDING bar whose SOLE candidate is strat_322_first_live
    must produce a TRADE, not a block."""
    cfg = _enabled_cfg(config, require_trending_condition=True)
    bars = _long_day_bars(include_10am=False)
    bars.append(_bar(10, 5, 100, 105, 99, 104.5))
    state = _market_state(bars)
    state.market_condition = "RANGE_BOUND"  # NOT trending
    daily = DailyState()
    daily.strat_322_first_live_state["MNQ"] = _armed_322_state()
    out = DecisionEngine(cfg).evaluate(state, daily)
    assert out.decision == "TRADE"
    assert out.setup.strategy == "strat_322_first_live"
    assert out.setup.entry == 104.0


def test_signal_layer_min_rr_exempt_for_strat_322_alone(config):
    """Contract audit (2026-07-27, PR review — parity-corrections branch):
    the canonical evidence for this strategy (60M_322_EXPANDED_EVIDENCE_
    2026-07-26.md, 34 candidates / 21 fills / PF10.36) does not clear a 2.0
    R:R floor -- 21/21 fills are below 2R (evidenced structurally, not by
    accident: the bracket is 8AM-high/low derived, not R:R-target derived).
    A global min_rr_ratio=2.0 would silently exclude the entire evidenced
    fill population at signal-confirmation time, before the trade ever
    reaches RiskEngine. This locks the first (signal-layer) of the two
    min_rr_ratio enforcement points: a sub-2R strat_322_first_live setup
    must still confirm as a TRADE, using the real 2026-06-15 fixture
    (entry=104, stop=90, target=115 -> rr=11/14=0.786)."""
    cfg = _enabled_cfg(config, min_rr_ratio=2.0)
    bars = _long_day_bars(include_10am=False)
    bars.append(_bar(10, 5, 100, 105, 99, 104.5))
    state = _market_state(bars)
    daily = DailyState()
    daily.strat_322_first_live_state["MNQ"] = _armed_322_state()
    out = DecisionEngine(cfg).evaluate(state, daily)
    assert out.decision == "TRADE"
    assert out.setup.strategy == "strat_322_first_live"
    assert out.setup.rr_ratio < cfg.min_rr_ratio  # exemption is genuinely exercised
    assert out.setup.entry == 104.0
    assert out.setup.stop == 90.0
    assert out.setup.target == 115.0


def test_trending_gate_exempt_helper_never_bypasses_on_collision(config):
    """Unit-level collision safety for _trending_gate_exempt_candidate: when
    BOTH 5-minute-native strategies have a candidate on the identical bar,
    the helper must return False — the exemption can never apply, so
    strat_4hr_retrigger's own gated behavior is provably untouched. Tested
    directly against the helper (rather than through the full advance()
    pipeline) so the assertion is precise about the exact inputs that flip
    the decision, independent of how each state machine happens to persist."""
    engine = DecisionEngine(config=_enabled_cfg(config))
    bars = _long_day_bars(include_10am=False)
    bars.append(_bar(10, 5, 100, 105, 99, 104.5))
    state = _market_state(bars)

    state.strat_322_first_live_candidate = None
    state.four_hr_retrigger_candidate = None
    assert engine._trending_gate_exempt_candidate(state) is False  # neither

    state.strat_322_first_live_candidate = {"direction": "LONG"}
    state.four_hr_retrigger_candidate = None
    assert engine._trending_gate_exempt_candidate(state) is True  # 322 alone

    state.four_hr_retrigger_candidate = {"direction": "LONG"}
    assert engine._trending_gate_exempt_candidate(state) is False  # both -> exempt OFF

    state.strat_322_first_live_candidate = None
    assert engine._trending_gate_exempt_candidate(state) is False  # 4HR alone

    state.canonical_4hr_only = False
    state.strat_322_first_live_candidate = {"direction": "LONG"}
    state.four_hr_retrigger_candidate = None
    assert engine._trending_gate_exempt_candidate(state) is False  # legacy path


def test_trending_gate_still_blocks_four_hr_retrigger_alone(config):
    """Explicit regression lock: strat_4hr_retrigger's own TRENDING-gated
    behavior (unrelated to this PR, already live in production) must be
    byte-identical to before this change when it is the only candidate."""
    cfg = _enabled_cfg(
        config,
        enabled_concepts=["strat_4hr_retrigger"],
        require_trending_condition=True,
    )
    bars = _long_day_bars(include_10am=False)
    bars.append(_bar(10, 5, 100, 105, 99, 104.5))
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


def test_trending_gate_exemption_does_not_leak_to_legacy_15m_path(config):
    """The exemption is scoped to canonical_4hr_only bars only — a normal
    15m-path bar (any other strategy) must be completely unaffected."""
    cfg = _enabled_cfg(
        config,
        enabled_concepts=["orb_reclaim"],
        require_trending_condition=True,
    )
    bars = _long_day_bars(include_10am=False)
    state = _market_state(bars)
    state.canonical_4hr_only = False
    state.market_condition = "RANGE_BOUND"
    state.ohlc.timeframe = "15m"
    daily = DailyState()
    out = DecisionEngine(cfg).evaluate(state, daily)
    assert out.decision == "NO_TRADE"
    assert "MARKET_CONDITION_NOT_TRENDING" in out.failed_gates


def test_blocked_candidate_audit_still_visible_when_exemption_does_not_apply(config):
    """Observability requirement: whenever the exemption legitimately does
    NOT apply (forced off here via canonical_4hr_only=False, the same
    condition the collision case produces), a genuinely-blocked
    strat_322_first_live candidate must still surface in
    blocked_candidate_audit with full strategy/direction/entry/stop/target/
    blocking_gate detail, surviving to_dict() — the pre-existing generic
    mechanism (_collect_blocked_candidate_audit, untouched by this fix)
    still does its job for this strategy exactly like every other."""
    cfg = _enabled_cfg(config, require_trending_condition=True)
    bars = _long_day_bars(include_10am=False)
    bars.append(_bar(10, 5, 100, 105, 99, 104.5))
    state = _market_state(bars)
    state.market_condition = "RANGE_BOUND"
    state.canonical_4hr_only = False  # forces _trending_gate_exempt_candidate() False
    daily = DailyState()
    daily.strat_322_first_live_state["MNQ"] = _armed_322_state()
    out = DecisionEngine(cfg).evaluate(state, daily)
    assert out.decision == "NO_TRADE"
    assert "MARKET_CONDITION_NOT_TRENDING" in out.failed_gates
    rows = out.blocked_candidate_audit["candidates"]
    row = next(r for r in rows if r["strategy"] == "strat_322_first_live")
    assert row["blocking_gate"] == "MARKET_CONDITION_NOT_TRENDING"
    assert row["direction"] == "LONG"
    assert row["entry"] == 104.0
    assert row["stop"] == 90.0
    assert row["target"] == 115.0
    assert "blocked_candidate_audit" in out.to_dict()


def test_cross_instrument_state_does_not_leak(config):
    """MNQ arming a setup must not be visible to MES's state slot (same
    DailyState instance, same cross-instrument isolation contract as
    four_hr_retrigger_state)."""
    cfg = _enabled_cfg(config)
    daily = DailyState()
    mnq_bars = _long_day_bars(include_10am=False)
    DecisionEngine(cfg).evaluate(_market_state(mnq_bars), daily)
    assert "MNQ" in daily.strat_322_first_live_state
    assert "MES" not in daily.strat_322_first_live_state


# ── risk_rules.yaml activation (the actual demo-execution wiring) ──────────


def test_risk_rules_yaml_enables_mnq_only():
    from config.settings import load_config

    cfg = load_config()
    assert "strat_322_first_live" in cfg.enabled_concepts
    assert "strat_322_first_live" not in cfg.disabled_concepts_per_instrument.get("MNQ", [])
    assert "strat_322_first_live" in cfg.disabled_concepts_per_instrument.get("MES", [])
    assert cfg.strategy_status.get("strat_322_first_live") == "PAPER_ELIGIBLE"


def test_risk_rules_yaml_enables_strat_122_mes_only_leaves_212_disabled():
    from config.settings import load_config

    cfg = load_config()
    assert "strat_122" in cfg.enabled_concepts
    assert "strat_122" in cfg.disabled_concepts_per_instrument.get("MNQ", [])
    assert "strat_122" not in cfg.disabled_concepts_per_instrument.get("MES", [])
    assert "strat_212" not in cfg.enabled_concepts
    assert cfg.strategy_status.get("strat_122") == "PAPER_ELIGIBLE"
