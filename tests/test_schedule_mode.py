"""
tests/test_schedule_mode.py

Phase 3: schedule-mode feature flag + always-on counterfactual generator.

Locks: config defaults & validation, the live-rejects-always_on_paper invariant,
that "current" enforces session gates while always-on bypasses them, and that the
read-only shadow generator emits a SETUP_BLOCKED candidate only when the sole
difference is the schedule (not quality/risk, and not when it actually traded).
"""
from __future__ import annotations

import dataclasses

import pytest

from config.settings import SystemConfig, _validate_config, ConfigError
from strategy.signal_engine import DecisionEngine
from risk.risk_engine import DailyState
from adaptive.shadow_runner import evaluate_with_shadow
from adaptive.opportunity_tracker import SETUP_BLOCKED


# ── Config defaults & validation ─────────────────────────────────────────────

def test_schedule_mode_default_is_current():
    assert SystemConfig.__dataclass_fields__["schedule_mode"].default == "current"


def test_validate_rejects_unknown_schedule_mode(config):
    bad = dataclasses.replace(config, schedule_mode="turbo")
    with pytest.raises(ConfigError):
        _validate_config(bad)


def test_validate_allows_known_modes(config):
    # The unit-test config disables staleness (0); set a valid value so we're
    # exercising only the schedule_mode branch of _validate_config.
    base = dataclasses.replace(config, max_staleness_seconds=300)
    for mode in ("current", "always_on_shadow", "always_on_paper"):
        _validate_config(dataclasses.replace(base, schedule_mode=mode))  # no raise


def test_live_execution_rejects_always_on_paper(config):
    bad = dataclasses.replace(
        config, schedule_mode="always_on_paper", live_trading_enabled=True
    )
    with pytest.raises(ConfigError):
        _validate_config(bad)


# ── Engine schedule bypass ───────────────────────────────────────────────────

def test_current_enforces_session_gate(config, fresh_market_state):
    # new_york state, but config only allows london → current blocks on session.
    cfg = dataclasses.replace(config, allowed_sessions=["london"])
    out = DecisionEngine(cfg, schedule_mode="current").evaluate(fresh_market_state, DailyState())
    assert out.decision == "NO_TRADE"
    assert "not allowed" in out.reason


def test_always_on_shadow_bypasses_session_gate(config, fresh_market_state):
    cfg = dataclasses.replace(config, allowed_sessions=["london"])
    out = DecisionEngine(cfg, schedule_mode="always_on_shadow").evaluate(
        fresh_market_state, DailyState()
    )
    # Session no longer the blocker — it gets past the session gate (the base
    # fixture is a valid opening setup, so it should reach a TRADE).
    assert out.decision == "TRADE"
    assert out.setup is not None


# ── Shadow generator ─────────────────────────────────────────────────────────

def test_generator_emits_setup_blocked_when_only_schedule_differs(config, fresh_market_state):
    cfg = dataclasses.replace(config, allowed_sessions=["london"])  # new_york disallowed
    cand = evaluate_with_shadow(fresh_market_state, DailyState(), cfg)
    assert cand is not None
    assert cand.block_type == SETUP_BLOCKED
    assert cand.has_valid_bracket()
    assert cand.instrument == fresh_market_state.instrument


def test_generator_records_to_store(config, fresh_market_state, tmp_path):
    from adaptive.opportunity_tracker import OpportunityStore
    cfg = dataclasses.replace(config, allowed_sessions=["london"])
    store = OpportunityStore(log_dir=str(tmp_path))
    cand = evaluate_with_shadow(fresh_market_state, DailyState(), cfg, store=store)
    assert cand is not None
    rows = store.read_day()
    assert len(rows) == 1 and rows[0]["block_type"] == SETUP_BLOCKED


def test_generator_no_candidate_when_actually_trades(config, fresh_market_state):
    # new_york allowed → current trades → not a missed opportunity.
    cfg = dataclasses.replace(config, allowed_sessions=["london", "new_york"])
    assert evaluate_with_shadow(fresh_market_state, DailyState(), cfg) is None


def test_generator_no_candidate_when_shadow_also_blocks(config, fresh_market_state):
    # Break the setup so NO strategy fires → shadow also NO_TRADE → no candidate,
    # even though the session is disallowed.
    state = dataclasses.replace(fresh_market_state, market_condition="CHOPPY")
    state.trend = dataclasses.replace(fresh_market_state.trend, direction="FLAT", strength="WEAK")
    cfg = dataclasses.replace(config, allowed_sessions=["london"])
    assert evaluate_with_shadow(state, DailyState(), cfg) is None
