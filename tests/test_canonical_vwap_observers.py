from __future__ import annotations

import dataclasses
from copy import deepcopy

from strategy.canonical_observers import (
    VWAP_HOLD_OBSERVED,
    evaluate_canonical_observers,
    reset_engine_cache,
)


def _hold_state(fresh_market_state):
    from context.market_context import TrendData
    from strategy.strat_classifier import StratContext

    state = deepcopy(fresh_market_state)
    state.instrument = "MNQ"
    state.session = "new_york"
    state.vwap.holding = True
    state.vwap.price_vs_vwap = "below"
    state.vwap.value = 19500.0
    state.trend = TrendData(direction="DOWN", strength="STRONG")
    state.strat = StratContext(current_bar_type="two_down")
    state.raw = {"bos_direction": "bearish"}
    state.ohlc.close = 19500.0
    return state


def test_hold_observer_exactly_matches_canonical_builder(fresh_market_state):
    from strategy.signal_engine import DecisionEngine

    state = _hold_state(fresh_market_state)
    canonical = DecisionEngine()._try_vwap_hold(state)
    observed = [c for c in evaluate_canonical_observers(state) if c.strategy == VWAP_HOLD_OBSERVED]
    assert len(observed) == 1
    assert (observed[0].direction, observed[0].entry, observed[0].stop, observed[0].target, observed[0].rr_ratio) == (
        canonical.direction, canonical.entry, canonical.stop, canonical.target, canonical.rr_ratio,
    )


def test_hold_control_is_mnq_new_york_only(fresh_market_state):
    state = _hold_state(fresh_market_state)
    state.session = "london"
    assert VWAP_HOLD_OBSERVED not in {c.strategy for c in evaluate_canonical_observers(state)}
    state.session = "new_york"
    state.instrument = "MES"
    assert evaluate_canonical_observers(state) == []


def test_observer_does_not_mutate_daily_state_or_call_risk(fresh_market_state, clean_daily_state, monkeypatch):
    from risk.risk_engine import RiskEngine

    before = dataclasses.asdict(clean_daily_state)
    monkeypatch.setattr(RiskEngine, "validate", lambda *a, **k: (_ for _ in ()).throw(AssertionError("risk called")))
    assert evaluate_canonical_observers(_hold_state(fresh_market_state))
    assert dataclasses.asdict(clean_daily_state) == before


def test_observers_are_outside_permission_and_ranking(fresh_market_state):
    from config.settings import load_config

    cfg = load_config("risk_rules.yaml")
    assert cfg.enabled_concepts == ["orb_breakout"]
    before = deepcopy(_hold_state(fresh_market_state))
    evaluate_canonical_observers(before, cfg)
    assert cfg.enabled_concepts == ["orb_breakout"]


def test_shadow_api_remains_backwards_compatible(fresh_market_state):
    from strategy.shadow_setups import evaluate_shadow_setups

    reset_engine_cache()
    state = _hold_state(fresh_market_state)
    assert isinstance(evaluate_shadow_setups(state), list)
    assert isinstance(evaluate_shadow_setups(state, []), list)
