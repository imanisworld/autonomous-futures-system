from __future__ import annotations

from copy import deepcopy

from context.market_context import GEXContext, ICCContext, SignaContext, TrendData
from risk.risk_engine import DailyState
from strategy.gex_gate import evaluate_gex
from strategy.regime_classifier import classify_regime
from strategy.signa_gate import evaluate_signa
from strategy.signal_engine import DecisionEngine
from strategy.strat_classifier import StratContext


def _base_long_state(fresh_market_state):
    state = deepcopy(fresh_market_state)
    state.orb.status = "reclaimed_high"
    state.vwap.price_vs_vwap = "above"
    state.vwap.reclaimed = True
    state.vwap.holding = True
    state.trend = TrendData(direction="UP", strength="STRONG")
    state.strat = StratContext(current_bar_type="2U")
    state.gex = GEXContext(gex_flip=state.price.last - 0.5, call_wall=state.price.last + 40, put_wall=state.price.last - 80)
    state.signa = SignaContext(grade="A", score=92, daily_direction="UP", weekly_direction="UP")
    state.icc = ICCContext(indication_type="demand")
    return state


def test_daily_2u_aligned_gex_support_signa_a_approved_long(config, fresh_market_state):
    state = _base_long_state(fresh_market_state)

    decision = DecisionEngine(config=config).evaluate(state, DailyState())

    assert decision.decision == "TRADE"
    assert decision.regime == "FULL_LONG"
    assert decision.gex_status == "GREEN_LIGHT_LONG"
    assert decision.signa_status == "PASS"
    assert decision.failed_gates == []


def test_daily_2u_buying_into_call_wall_gex_red_light_rejection(config, fresh_market_state):
    state = _base_long_state(fresh_market_state)
    state.gex.call_wall = state.price.last - 1

    decision = DecisionEngine(config=config).evaluate(state, DailyState())

    assert decision.decision == "NO_TRADE"
    assert decision.gex_status == "RED_LIGHT"
    assert "GEX_UNDER_CALL_WALL" in decision.failed_gates


def test_daily_1_clean_context_is_restricted_watch_approval(config, fresh_market_state):
    state = _base_long_state(fresh_market_state)
    state.strat = StratContext(current_bar_type="1")

    decision = DecisionEngine(config=config).evaluate(state, DailyState())

    assert decision.decision == "TRADE"
    assert decision.regime == "RESTRICTED"
    assert "REGIME_RESTRICTED" in decision.failed_gates


def test_daily_3_weekly_bullish_failed_low_reclaim_full_long_allowed(config, fresh_market_state):
    state = _base_long_state(fresh_market_state)
    state.strat = StratContext(current_bar_type="3")
    state.icc.phase = "failed_low_reclaim"

    decision = DecisionEngine(config=config).evaluate(state, DailyState())

    assert decision.decision == "TRADE"
    assert decision.regime == "FULL_LONG"


def test_daily_3_no_htf_agreement_is_restricted(fresh_market_state):
    state = _base_long_state(fresh_market_state)
    state.strat = StratContext(current_bar_type="3")
    state.trend = TrendData(direction="SIDEWAYS", strength="WEAK")

    regime = classify_regime(state)

    assert regime.regime in {"RESTRICTED", "NO_TRADE"}


def test_signa_c_rejected(config, fresh_market_state):
    config.signa_gate_enforced = True  # this test covers ENFORCED behavior
    state = _base_long_state(fresh_market_state)
    state.signa.grade = "C"

    decision = DecisionEngine(config=config).evaluate(state, DailyState())

    assert decision.decision == "NO_TRADE"
    assert decision.signa_status == "FAIL"
    assert "SIGNA_GRADE_FAIL" in decision.failed_gates


def test_opposing_weekly_signa_rejected(config, fresh_market_state):
    config.signa_gate_enforced = True  # this test covers ENFORCED behavior
    state = _base_long_state(fresh_market_state)
    state.signa.grade = "A"
    state.signa.weekly_direction = "DOWN"

    decision = DecisionEngine(config=config).evaluate(state, DailyState())

    assert decision.decision == "NO_TRADE"
    assert decision.signa_status == "FAIL"
    assert "SIGNA_WEEKLY_OPPOSES" in decision.failed_gates


def test_signa_fail_is_shadow_by_default_does_not_block(config, fresh_market_state):
    """Default (signa_gate_enforced=False): a Signa FAIL is still COMPUTED and
    recorded for journaling/measurement, but it must NOT block the trade."""
    assert config.signa_gate_enforced is False  # production default
    state = _base_long_state(fresh_market_state)
    state.signa.grade = "C"  # would FAIL the gate

    decision = DecisionEngine(config=config).evaluate(state, DailyState())

    # Trade is NOT blocked by Signa in shadow mode...
    assert decision.decision == "TRADE"
    assert "SIGNA_GRADE_FAIL" not in decision.failed_gates
    # ...but the FAIL status is still surfaced so we can measure it later.
    assert decision.signa_status == "FAIL"


def test_mid_range_gex_rejected_watch_only(config, fresh_market_state):
    state = _base_long_state(fresh_market_state)
    state.gex.gex_flip = None
    state.gex.call_wall = state.price.last + 100
    state.gex.put_wall = state.price.last - 100
    state.gex.mid_lower = state.price.last - 5
    state.gex.mid_upper = state.price.last + 5

    decision = DecisionEngine(config=config).evaluate(state, DailyState())

    assert decision.decision == "NO_TRADE"
    assert decision.gex_status == "RED_LIGHT"
    assert "GEX_MID_RANGE" in decision.failed_gates


def test_chase_over_70_extension_warning_not_rejection(config, fresh_market_state):
    state = _base_long_state(fresh_market_state)
    state.icc.phase = "chase_70_extension"

    decision = DecisionEngine(config=config).evaluate(state, DailyState())

    assert decision.decision == "TRADE"
    assert decision.regime == "RESTRICTED"
    assert "REGIME_CHASE_EXTENSION" in decision.failed_gates


def test_gex_data_absent_neutral_no_block(fresh_market_state):
    state = deepcopy(fresh_market_state)
    state.gex = None

    result = evaluate_gex(state, "LONG")

    assert result.status == "NEUTRAL"
    assert result.failed_gate is None


def test_signa_data_absent_neutral_no_block(fresh_market_state):
    state = deepcopy(fresh_market_state)
    state.signa = None

    result = evaluate_signa(state, "LONG")

    assert result.status == "NEUTRAL"
    assert result.failed_gate is None
