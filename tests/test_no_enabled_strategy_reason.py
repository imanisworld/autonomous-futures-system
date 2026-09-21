"""
tests/test_no_enabled_strategy_reason.py

When enabled_concepts minus disabled_concepts_per_instrument[instrument] is
empty, evaluate() must say NO_ENABLED_STRATEGY instead of blaming whatever
tradability gate happens to run first. The gate must still journal the scored
market_condition and must be invisible whenever at least one concept can fire.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from risk.risk_engine import DailyState
from strategy.signal_engine import DecisionEngine


def _engine(config, **overrides):
    return DecisionEngine(config=replace(config, **overrides))


def test_empty_executable_set_reports_no_enabled_strategy(config, fresh_market_state):
    engine = _engine(
        config,
        enabled_concepts=["orb_breakout"],
        disabled_concepts_per_instrument={"MNQ": ["orb_breakout"]},
    )
    d = engine.evaluate(fresh_market_state, DailyState())
    assert d.decision == "NO_TRADE"
    assert d.failed_gates == ["NO_ENABLED_STRATEGY"]
    assert "MNQ" in d.reason
    assert d.market_condition, "label must still be scored and journaled"


def test_no_enabled_strategy_wins_over_market_condition_gate(config, fresh_market_state):
    """Before this gate a DEAD/CHOPPY bar hid the empty set behind
    MARKET_CONDITION_NOT_TRADABLE. Force a non-tradable label and confirm the
    empty set is reported first."""
    engine = _engine(
        config,
        enabled_concepts=["orb_breakout"],
        disabled_concepts_per_instrument={"MNQ": ["orb_breakout"]},
    )
    state = replace(fresh_market_state, market_condition="DEAD")
    d = engine.evaluate(state, DailyState())
    assert d.failed_gates == ["NO_ENABLED_STRATEGY"]
    assert "MARKET_CONDITION_NOT_TRADABLE" not in d.failed_gates


def test_gate_is_per_instrument(config, fresh_market_state):
    engine = _engine(
        config,
        enabled_concepts=["orb_breakout"],
        disabled_concepts_per_instrument={"MES": ["orb_breakout"]},
    )
    d = engine.evaluate(fresh_market_state, DailyState())  # MNQ still has orb_breakout
    assert "NO_ENABLED_STRATEGY" not in (d.failed_gates or [])

    mes = replace(fresh_market_state, instrument="MES")
    d = engine.evaluate(mes, DailyState())
    assert d.failed_gates == ["NO_ENABLED_STRATEGY"]


def test_empty_enabled_concepts_reports_no_enabled_strategy(config, fresh_market_state):
    engine = _engine(config, enabled_concepts=[], disabled_concepts_per_instrument={})
    d = engine.evaluate(fresh_market_state, DailyState())
    assert d.failed_gates == ["NO_ENABLED_STRATEGY"]


def test_canonical_4hr_lane_narrows_to_five_minute_native(config, fresh_market_state):
    """On the canonical 4HR lane only 5m-native strategies count. A 15m-only
    enabled set is therefore empty there, but not on the 15m lane."""
    engine = _engine(config, enabled_concepts=["orb_breakout"], disabled_concepts_per_instrument={})
    assert engine._executable_concepts(fresh_market_state) == ["orb_breakout"]
    four_hr = replace(fresh_market_state, canonical_4hr_only=True)
    assert engine._executable_concepts(four_hr) == []

    engine = _engine(config, enabled_concepts=["orb_breakout", "strat_4hr_retrigger"], disabled_concepts_per_instrument={})
    assert engine._executable_concepts(four_hr) == ["strat_4hr_retrigger"]


def test_gate_silent_when_a_concept_can_fire(config, fresh_market_state):
    """Regression guard: with a non-empty executable set the decision path is
    byte-for-byte what it was before the gate existed."""
    engine = _engine(
        config,
        enabled_concepts=["orb_breakout", "orb_reclaim"],
        disabled_concepts_per_instrument={"MNQ": ["orb_breakout"]},
    )
    d = engine.evaluate(fresh_market_state, DailyState())
    assert "NO_ENABLED_STRATEGY" not in (d.failed_gates or [])
    assert "No enabled strategy" not in (d.reason or "")
