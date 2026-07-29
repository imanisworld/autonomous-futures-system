"""Isolation + parity proof for the observe-only MNQ canonical VWAP watchers.

The observer exists to keep collecting vwap_hold / vwap_rejection evidence
while `orb_breakout` is the ONLY executable concept. These tests pin both
halves of that contract:

  1. PARITY  — the observed bracket is byte-for-byte the canonical executable
     bracket (it reuses DecisionEngine's own builders, so a formula drift
     between observer and executable is impossible by construction).
  2. ISOLATION — an observer candidate cannot alter the active decision,
     cannot suppress orb_breakout, cannot reach risk approval or a broker, and
     cannot move trade/position/account state.
"""
from __future__ import annotations

import dataclasses
from copy import deepcopy
from datetime import date

import pytest

from strategy.canonical_observers import (
    OBSERVED_STRATEGIES,
    VWAP_HOLD_OBSERVED,
    VWAP_REJECTION_OBSERVED,
    evaluate_canonical_observers,
    is_observed_instrument,
    reset_engine_cache,
)


@pytest.fixture(autouse=True)
def _clear_engine_cache():
    reset_engine_cache()
    yield
    reset_engine_cache()


def _vwap_hold_state(fresh_market_state):
    """A MarketState the canonical _try_vwap_hold accepts."""
    from context.market_context import TrendData

    state = deepcopy(fresh_market_state)
    state.instrument = "MNQ"
    state.vwap.holding = True
    state.vwap.price_vs_vwap = "below"
    state.vwap.value = 19500.0
    state.trend = TrendData(direction="DOWN", strength="STRONG")
    if state.strat is not None:
        state.strat.current_bar_type = "two_down"
    state.raw = {"bos_direction": "bearish"}
    state.ohlc.close = 19500.0
    return state


# ─── 1. Canonical parity ──────────────────────────────────────────────────────

def test_observer_bracket_exactly_matches_canonical_vwap_hold_builder(fresh_market_state):
    """Same MarketState -> identical direction/entry/stop/target/rr as the
    executable builder. Not 'close enough': exactly equal."""
    from strategy.signal_engine import DecisionEngine

    state = _vwap_hold_state(fresh_market_state)

    canonical = DecisionEngine()._try_vwap_hold(state)
    assert canonical is not None, "fixture must produce a canonical vwap_hold setup"

    observed = [
        c for c in evaluate_canonical_observers(state)
        if c.strategy == VWAP_HOLD_OBSERVED
    ]
    assert len(observed) == 1
    obs = observed[0]

    assert obs.direction == canonical.direction
    assert obs.entry == canonical.entry
    assert obs.stop == canonical.stop
    assert obs.target == canonical.target
    assert obs.rr_ratio == canonical.rr_ratio


def test_observer_matches_canonical_vwap_rejection_builder_when_it_fires(fresh_market_state):
    """Whatever the canonical rejection builder returns for a state, the
    observer returns the same bracket — including returning nothing when the
    canonical builder declines."""
    from strategy.signal_engine import DecisionEngine

    state = deepcopy(fresh_market_state)
    state.instrument = "MNQ"

    canonical = DecisionEngine()._try_vwap_rejection(state)
    observed = [
        c for c in evaluate_canonical_observers(state)
        if c.strategy == VWAP_REJECTION_OBSERVED
    ]

    if canonical is None:
        assert observed == []
    else:
        assert len(observed) == 1
        obs = observed[0]
        assert (obs.direction, obs.entry, obs.stop, obs.target, obs.rr_ratio) == (
            canonical.direction, canonical.entry, canonical.stop,
            canonical.target, canonical.rr_ratio,
        )


def test_observed_names_are_distinct_from_executable_names(fresh_market_state):
    """An observed candidate must never be mistakable for an executable one."""
    state = _vwap_hold_state(fresh_market_state)
    for c in evaluate_canonical_observers(state):
        assert c.strategy in OBSERVED_STRATEGIES
        assert c.strategy not in ("vwap_hold", "vwap_rejection")
        assert c.strategy.endswith("_observed")


def test_observer_is_mnq_only(fresh_market_state):
    assert is_observed_instrument("MNQ") is True
    assert is_observed_instrument("MNQ1!") is True
    assert is_observed_instrument("MES") is False
    assert is_observed_instrument(None) is False

    state = _vwap_hold_state(fresh_market_state)
    state.instrument = "MES"
    assert evaluate_canonical_observers(state) == []


# ─── 2. Isolation ─────────────────────────────────────────────────────────────

def test_observer_candidate_cannot_change_the_active_decision(fresh_market_state, clean_daily_state):
    """The active DecisionOutput is identical whether or not the observer runs."""
    from strategy.signal_engine import DecisionEngine

    state = _vwap_hold_state(fresh_market_state)
    engine = DecisionEngine()

    before = engine.evaluate(deepcopy(state), clean_daily_state)
    _ = evaluate_canonical_observers(state)
    after = engine.evaluate(deepcopy(state), clean_daily_state)

    assert before.decision == after.decision
    assert (before.setup is None) == (after.setup is None)
    if before.setup is not None:
        assert before.setup.strategy == after.setup.strategy
        assert before.setup.entry == after.setup.entry


def test_observer_cannot_suppress_orb_breakout():
    """Under the shipped isolated config, orb_breakout is the only ranking
    participant. The observer runs entirely outside ranking, so it cannot
    preempt it the way an ENABLED shadow-only strategy could (PR #373)."""
    from config.settings import load_config

    cfg = load_config("risk_rules.yaml")
    assert cfg.enabled_concepts == ["orb_breakout"]
    for name in OBSERVED_STRATEGIES:
        assert name not in cfg.enabled_concepts
        assert name not in cfg.strategy_status


def test_observer_does_not_call_risk_validate(fresh_market_state, monkeypatch):
    from risk.risk_engine import RiskEngine

    def _trip(*a, **k):
        raise AssertionError("observer must never call RiskEngine.validate")

    monkeypatch.setattr(RiskEngine, "validate", _trip, raising=False)

    state = _vwap_hold_state(fresh_market_state)
    candidates = evaluate_canonical_observers(state)
    assert candidates, "fixture should produce at least one observed candidate"


def test_observer_does_not_instantiate_or_call_any_broker(fresh_market_state, monkeypatch):
    import execution.paper_broker as pb

    def _trip(*a, **k):
        raise AssertionError("observer must never construct a broker")

    monkeypatch.setattr(pb.PaperBroker, "__init__", _trip, raising=False)
    monkeypatch.setattr(pb.PaperBroker, "execute_bracket", _trip, raising=False)

    state = _vwap_hold_state(fresh_market_state)
    assert evaluate_canonical_observers(state)


def test_observer_does_not_touch_trade_count_or_position_state(fresh_market_state, clean_daily_state):
    before = dataclasses.asdict(clean_daily_state)
    state = _vwap_hold_state(fresh_market_state)
    evaluate_canonical_observers(state)
    assert dataclasses.asdict(clean_daily_state) == before


def test_observer_exceptions_are_fail_soft(fresh_market_state, monkeypatch):
    """A raising canonical builder yields no candidates and no exception."""
    from strategy.signal_engine import DecisionEngine

    def _boom(self, state):
        raise RuntimeError("simulated observer defect")

    monkeypatch.setattr(DecisionEngine, "_try_vwap_hold", _boom, raising=False)
    monkeypatch.setattr(DecisionEngine, "_try_vwap_rejection", _boom, raising=False)

    state = _vwap_hold_state(fresh_market_state)
    assert evaluate_canonical_observers(state) == []


def test_one_failing_builder_does_not_suppress_the_other(fresh_market_state, monkeypatch):
    from strategy.signal_engine import DecisionEngine

    def _boom(self, state):
        raise RuntimeError("simulated rejection-builder defect")

    monkeypatch.setattr(DecisionEngine, "_try_vwap_rejection", _boom, raising=False)

    state = _vwap_hold_state(fresh_market_state)
    names = {c.strategy for c in evaluate_canonical_observers(state)}
    assert VWAP_HOLD_OBSERVED in names


def test_observer_exception_does_not_change_the_active_result(fresh_market_state, tmp_path, monkeypatch):
    """End-to-end: a raising observer must not alter process_alert's decision."""
    import sys

    from webhook.runner import process_alert

    sys.path.insert(0, "tests")
    from test_e2e_scenarios import _base_payload
    from tests.conftest import load_permissive_config

    payload = _base_payload(timestamp="2026-05-23T14:30:00+00:00")
    fd = date(2026, 5, 23)
    cfg = load_permissive_config(max_staleness_seconds=10**9)

    healthy = process_alert(
        payload, config=cfg, log_dir=str(tmp_path / "a"), for_date=fd
    )

    import strategy.canonical_observers as co

    def _boom(state, config=None):
        raise RuntimeError("simulated observer defect")

    monkeypatch.setattr(co, "evaluate_canonical_observers", _boom, raising=False)
    monkeypatch.setattr(
        "strategy.shadow_setups.evaluate_canonical_observers", _boom, raising=False
    )

    broken = process_alert(
        payload, config=cfg, log_dir=str(tmp_path / "b"), for_date=fd
    )

    assert healthy["decision"] == broken["decision"]


# ─── 3. Resolution path ───────────────────────────────────────────────────────

def test_observed_candidate_resolves_through_existing_shadow_resolver(fresh_market_state):
    """Reuses resolve_shadow_candidate, which already models entry-fill realism
    and pessimistic same-bar stop/target handling."""
    from strategy.shadow_setups import resolve_shadow_candidate

    state = _vwap_hold_state(fresh_market_state)
    observed = [
        c for c in evaluate_canonical_observers(state)
        if c.strategy == VWAP_HOLD_OBSERVED
    ]
    assert observed
    candidate = observed[0]

    # A bar straddling BOTH stop and target must resolve pessimistically (STOP).
    straddle = [(candidate.stop + 1.0, candidate.target - 1.0)]
    outcome = resolve_shadow_candidate(
        candidate,
        [(candidate.entry + 0.25, candidate.entry - 0.25)] + straddle,
        instrument="MNQ",
        pessimistic_both_hit=True,
    )
    assert outcome.result == "LOSS"
    assert outcome.exit_reason == "STOP_HIT"


def test_observed_candidates_flow_through_evaluate_shadow_setups(fresh_market_state):
    from strategy.shadow_setups import evaluate_shadow_setups

    state = _vwap_hold_state(fresh_market_state)
    names = {c.strategy for c in evaluate_shadow_setups(state, [], None)}
    assert VWAP_HOLD_OBSERVED in names


def test_evaluate_shadow_setups_config_arg_is_optional(fresh_market_state):
    """Every pre-existing caller passes no config — that must keep working."""
    from strategy.shadow_setups import evaluate_shadow_setups

    state = _vwap_hold_state(fresh_market_state)
    assert isinstance(evaluate_shadow_setups(state), list)
    assert isinstance(evaluate_shadow_setups(state, []), list)
