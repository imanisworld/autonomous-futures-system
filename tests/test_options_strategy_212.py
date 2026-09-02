"""
tests/test_options_strategy_212.py

Increment 1 — options_manager/strategies/strat_212.py tests. Proves the
mechanical Strat 2-1-2 continuation validator is advisory-only: no broker
calls, no execution, no order placement, and fails closed to INVALID
whenever entry, invalidation, target, market-context, or contract-
constraint input is missing.
"""

from __future__ import annotations

import ast
from pathlib import Path

import options_manager.strategies.base as strategies_base_module
import options_manager.strategies.strat_212 as strat_212_module
from options_manager.context import MarketContextInputs
from options_manager.contracts import ContractConstraintsInputs
from options_manager.levels import LevelFinderInputs
from options_manager.strategies.base import (
    StrategyContractConstraints,
    StrategyMarketContext,
)
from options_manager.strategies.strat_212 import Strat212Bars, evaluate_strat_212

_SCANNED_STRATEGY_MODULES = (strategies_base_module, strat_212_module)

_FORBIDDEN_IMPORT_FRAGMENTS = (
    "execution",
    "webhook",
    "alert_ranker",
    "options_companion",
    "risk_engine",
    "requests",
    "httpx",
    "urllib",
    "socket",
    "aiohttp",
    "websocket",
    "robin_stocks",
    "ib_insync",
    "ibapi",
)

_EXISTING_PIPELINE_MODULES = (
    "options_manager.risk_gate",
    "options_manager.contract_quality",
    "options_manager.dry_run_review",
    "options_manager.human_confirm",
    "options_manager.order_ticket",
    "options_manager.broker_boundary",
    "options_manager.mock_broker_preview",
    "options_manager.storage",
    "options_manager.http_api",
    "options_manager.app",
)

_FORBIDDEN_ORDER_ACTION_IDENTIFIERS = (
    "place_order",
    "submit_order",
    "cancel_order",
    "replace_order",
    "execute_order",
    "live_order",
)


def _bullish_bars(*, current_high: float = 101.0, current_low: float = 96.5) -> Strat212Bars:
    return Strat212Bars(
        two_bars_back_type="two_up",
        two_bars_back_high=100.0,
        two_bars_back_low=95.0,
        previous_high=99.0,
        previous_low=96.0,
        current_high=current_high,
        current_low=current_low,
    )


def _bearish_bars(*, current_high: float = 98.5, current_low: float = 94.0) -> Strat212Bars:
    return Strat212Bars(
        two_bars_back_type="two_down",
        two_bars_back_high=100.0,
        two_bars_back_low=95.0,
        previous_high=99.0,
        previous_low=96.0,
        current_high=current_high,
        current_low=current_low,
    )


def _confirmed_context() -> StrategyMarketContext:
    return StrategyMarketContext(confirmed=True)


def _met_constraints() -> StrategyContractConstraints:
    return StrategyContractConstraints(constraints_met=True)


def _valid_call_market_context_inputs(**overrides) -> MarketContextInputs:
    fields = dict(
        direction="CALL",
        ticker="SPY",
        underlying_price=500.0,
        spy_trend="bullish",
        qqq_trend="bullish",
        spy_above_flip=True,
        qqq_above_flip=True,
        gex_regime="positive",
        price_above_gex_flip=True,
        signa_direction="bullish",
        signa_grade="A",
        signa_score=80.0,
        higher_timeframe_alignment="aligned",
        gap_direction="none",
        distance_to_gamma_resistance=5.0,
        distance_to_gamma_support=5.0,
        event_risk="none",
    )
    fields.update(overrides)
    return MarketContextInputs(**fields)


def _valid_put_market_context_inputs(**overrides) -> MarketContextInputs:
    fields = dict(
        direction="PUT",
        ticker="QQQ",
        underlying_price=400.0,
        spy_trend="bearish",
        qqq_trend="bearish",
        spy_above_flip=False,
        qqq_above_flip=False,
        gex_regime="negative",
        price_above_gex_flip=False,
        signa_direction="bearish",
        signa_grade="A",
        signa_score=80.0,
        higher_timeframe_alignment="aligned",
        gap_direction="none",
        distance_to_gamma_resistance=5.0,
        distance_to_gamma_support=5.0,
        event_risk="none",
    )
    fields.update(overrides)
    return MarketContextInputs(**fields)


def _valid_call_contract_constraints_inputs(**overrides) -> ContractConstraintsInputs:
    fields = dict(
        direction="CALL",
        ticker="SPY",
        expiration="2026-08-01",
        dte=30,
        strike=505.0,
        premium=2.50,
        bid=2.45,
        ask=2.55,
        spread_percent=0.04,
        volume=500,
        open_interest=1000,
        delta=0.35,
        theta=-0.05,
        iv=0.25,
        max_premium=5.0,
        max_spread_percent=0.10,
        min_volume=100,
        min_open_interest=200,
        min_dte=7,
        max_theta_abs=0.10,
        earnings_risk="NONE",
        event_risk="NONE",
    )
    fields.update(overrides)
    return ContractConstraintsInputs(**fields)


def _valid_put_contract_constraints_inputs(**overrides) -> ContractConstraintsInputs:
    fields = dict(
        direction="PUT",
        ticker="QQQ",
        expiration="2026-08-01",
        dte=30,
        strike=395.0,
        premium=2.50,
        bid=2.45,
        ask=2.55,
        spread_percent=0.04,
        volume=500,
        open_interest=1000,
        delta=-0.35,
        theta=-0.05,
        iv=0.25,
        max_premium=5.0,
        max_spread_percent=0.10,
        min_volume=100,
        min_open_interest=200,
        min_dte=7,
        max_theta_abs=0.10,
        earnings_risk="NONE",
        event_risk="NONE",
    )
    fields.update(overrides)
    return ContractConstraintsInputs(**fields)


# --- 1. valid bullish 2-1-2 ----------------------------------------------------------


def test_valid_bullish_212_returns_valid():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "VALID"
    assert result.reason_code == "valid_212_continuation"
    assert result.direction == "CALL"
    assert result.candle_sequence == "strat_212"
    assert result.entry_trigger == 99.0
    assert result.underlying_invalidation == 95.5
    assert result.target_1 == 103.0
    assert result.target_2 == 106.0


# --- 2. valid bearish 2-1-2 ----------------------------------------------------------


def test_valid_bearish_212_returns_valid():
    result = evaluate_strat_212(
        _bearish_bars(),
        direction="PUT",
        entry_trigger=96.0,
        underlying_invalidation=99.5,
        target_1=92.0,
        target_2=89.0,
        market_context=_confirmed_context(),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "VALID"
    assert result.reason_code == "valid_212_continuation"
    assert result.direction == "PUT"
    assert result.candle_sequence == "strat_212"


# --- 3. watch state before trigger ----------------------------------------------------


def test_bullish_setup_forming_but_not_triggered_returns_watch():
    bars = Strat212Bars(
        two_bars_back_type="two_up",
        two_bars_back_high=100.0,
        two_bars_back_low=95.0,
        previous_high=99.0,
        previous_low=96.0,
        current_high=98.5,  # still inside the previous (inside) bar — no breakout yet
        current_low=96.5,
    )
    result = evaluate_strat_212(
        bars,
        direction="CALL",
        entry_trigger=None,
        underlying_invalidation=None,
        target_1=None,
        target_2=None,
        market_context=StrategyMarketContext(),
        contract_constraints=StrategyContractConstraints(),
    )
    assert result.status == "WATCH"
    assert result.reason_code == "setup_forming_not_triggered"


# --- 4. invalid sequence ---------------------------------------------------------------


def test_outside_bar_current_is_invalid_sequence():
    bars = Strat212Bars(
        two_bars_back_type="two_up",
        two_bars_back_high=100.0,
        two_bars_back_low=95.0,
        previous_high=99.0,
        previous_low=96.0,
        current_high=102.0,  # breaks both sides of the inside bar -> outside bar
        current_low=93.0,
    )
    result = evaluate_strat_212(
        bars,
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "sequence_not_212"


# --- 5. missing invalidation -------------------------------------------------------------


def test_missing_invalidation_fails_closed_to_invalid():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=None,
        target_1=103.0,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "missing_invalidation"


# --- 6. missing targets --------------------------------------------------------------------


def test_missing_target_1_fails_closed_to_invalid():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=None,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "missing_target_1"


def test_missing_target_2_fails_closed_to_invalid():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=None,
        market_context=_confirmed_context(),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "missing_target_2"


# --- 6b. Increment 2B: level/target-finder wiring ------------------------------------------


def test_no_targets_and_no_level_inputs_fails_closed_to_invalid():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=None,
        target_2=None,
        market_context=_confirmed_context(),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "missing_target_1"


def test_bullish_212_derives_targets_from_resistance_and_gamma_levels():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=100.0,
        underlying_invalidation=97.0,
        level_inputs=LevelFinderInputs(
            direction="CALL",
            entry=100.0,
            underlying_invalidation=97.0,
            resistance_levels=(103.0, 108.0),
            gamma_resistance=105.0,
        ),
        market_context=_confirmed_context(),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "VALID"
    assert result.reason_code == "valid_212_continuation"
    assert result.target_1 == 103.0
    assert result.target_2 == 105.0
    assert result.risk_amount == 3.0
    assert result.reward_1 == 3.0
    assert result.reward_2 == 5.0
    assert result.rr_1 == 1.0
    assert result.distance_to_target_1 == 3.0
    assert result.distance_to_target_2 == 5.0


def test_bearish_212_derives_targets_from_support_and_gamma_levels():
    result = evaluate_strat_212(
        _bearish_bars(),
        direction="PUT",
        entry_trigger=100.0,
        underlying_invalidation=103.0,
        level_inputs=LevelFinderInputs(
            direction="PUT",
            entry=100.0,
            underlying_invalidation=103.0,
            support_levels=(97.0, 94.0),
            gamma_support=95.0,
        ),
        market_context=_confirmed_context(),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "VALID"
    assert result.reason_code == "valid_212_continuation"
    assert result.target_1 == 97.0
    assert result.target_2 == 95.0
    assert result.risk_amount == 3.0
    assert result.reward_1 == 3.0
    assert result.rr_1 == 1.0


def test_target_finder_invalid_propagates_as_strategy_invalid():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=100.0,
        underlying_invalidation=97.0,
        level_inputs=LevelFinderInputs(
            direction="CALL",
            entry=100.0,
            underlying_invalidation=97.0,
            resistance_levels=(95.0,),  # below entry -- no valid candidate
        ),
        market_context=_confirmed_context(),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "target_finder_no_target_1"


def test_target_finder_too_close_propagates_as_strategy_invalid():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=100.0,
        underlying_invalidation=97.0,
        level_inputs=LevelFinderInputs(
            direction="CALL",
            entry=100.0,
            underlying_invalidation=97.0,
            resistance_levels=(103.0, 108.0),
            min_distance_to_target=5.0,  # target_1 distance is only 3.0
        ),
        market_context=_confirmed_context(),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "target_finder_target_too_close"


def test_target_finder_rr_below_threshold_propagates_as_strategy_invalid():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=100.0,
        underlying_invalidation=97.0,  # risk = 3
        level_inputs=LevelFinderInputs(
            direction="CALL",
            entry=100.0,
            underlying_invalidation=97.0,
            resistance_levels=(102.0, 108.0),  # reward_1 = 2 -> rr_1 = 0.667
            min_rr_threshold=1.0,
        ),
        market_context=_confirmed_context(),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "target_finder_rr_below_threshold"


def test_derived_targets_still_require_market_context():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=100.0,
        underlying_invalidation=97.0,
        level_inputs=LevelFinderInputs(
            direction="CALL",
            entry=100.0,
            underlying_invalidation=97.0,
            resistance_levels=(103.0, 108.0),
            gamma_resistance=105.0,
        ),
        market_context=StrategyMarketContext(),  # confirmed=None
        contract_constraints=_met_constraints(),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "missing_market_context"


def test_derived_targets_still_require_contract_constraints():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=100.0,
        underlying_invalidation=97.0,
        level_inputs=LevelFinderInputs(
            direction="CALL",
            entry=100.0,
            underlying_invalidation=97.0,
            resistance_levels=(103.0, 108.0),
            gamma_resistance=105.0,
        ),
        market_context=_confirmed_context(),
        contract_constraints=StrategyContractConstraints(),  # constraints_met=None
    )
    assert result.status == "INVALID"
    assert result.reason_code == "missing_contract_constraints"


def test_explicit_targets_do_not_populate_derived_only_fields():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "VALID"
    assert result.risk_amount is None
    assert result.reward_1 is None
    assert result.rr_1 is None
    assert result.distance_to_target_1 is None


# --- 6c. Increment 3B: market-context-validator wiring -------------------------------------


def test_explicit_strategy_market_context_still_works_unchanged():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "VALID"
    assert result.context_status is None
    assert result.context_score is None
    assert result.context_warnings == []
    assert result.market_context_reason_code is None


def test_bullish_212_derives_clean_valid_context_from_market_context_inputs():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=StrategyMarketContext(),  # confirmed=None -> derive instead
        market_context_inputs=_valid_call_market_context_inputs(),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "VALID"
    assert result.context_status == "VALID"
    assert result.market_context_reason_code == "context_confirmed"
    assert result.context_warnings == []
    assert result.context_score == 4.0


def test_bearish_212_derives_clean_valid_context_from_market_context_inputs():
    result = evaluate_strat_212(
        _bearish_bars(),
        direction="PUT",
        entry_trigger=96.0,
        underlying_invalidation=99.5,
        target_1=92.0,
        target_2=89.0,
        market_context=StrategyMarketContext(),
        market_context_inputs=_valid_put_market_context_inputs(),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "VALID"
    assert result.context_status == "VALID"
    assert result.market_context_reason_code == "context_confirmed"


def test_caution_context_preserves_warnings_and_does_not_fake_a_clean_valid_context():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=StrategyMarketContext(),
        market_context_inputs=_valid_call_market_context_inputs(
            qqq_trend="neutral", qqq_above_flip=False
        ),
        contract_constraints=_met_constraints(),
    )
    # The strategy is still tradeable (status VALID -- context does not
    # itself invalidate the setup), but the CAUTION context must be
    # surfaced honestly rather than reported as a clean VALID context.
    assert result.status == "VALID"
    assert result.context_status == "CAUTION"
    assert result.market_context_reason_code == "context_mixed"
    assert result.context_warnings


def test_invalid_market_context_propagates_as_strategy_invalid():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=StrategyMarketContext(),
        market_context_inputs=_valid_call_market_context_inputs(
            spy_trend="bearish", qqq_trend="bearish", signa_direction="neutral"
        ),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "market_context_market_conflict"


def test_no_strategy_market_context_and_no_market_context_inputs_fails_closed():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=StrategyMarketContext(),  # confirmed=None
        contract_constraints=_met_constraints(),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "missing_market_context"


def test_derived_context_high_event_risk_fails_through_the_strategy():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=StrategyMarketContext(),
        market_context_inputs=_valid_call_market_context_inputs(event_risk="high"),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "market_context_event_risk_high"


def test_derived_context_htf_opposite_fails_through_the_strategy():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=StrategyMarketContext(),
        market_context_inputs=_valid_call_market_context_inputs(
            higher_timeframe_alignment="opposite"
        ),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "market_context_htf_opposite"


def test_derived_context_both_indexes_against_direction_fails_through_the_strategy():
    result = evaluate_strat_212(
        _bearish_bars(),
        direction="PUT",
        entry_trigger=96.0,
        underlying_invalidation=99.5,
        target_1=92.0,
        target_2=89.0,
        market_context=StrategyMarketContext(),
        market_context_inputs=_valid_put_market_context_inputs(
            spy_trend="bullish", qqq_trend="bullish", signa_direction="neutral"
        ),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "market_context_market_conflict"


def test_derived_context_does_not_break_target_finder_derivation():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=100.0,
        underlying_invalidation=97.0,
        level_inputs=LevelFinderInputs(
            direction="CALL",
            entry=100.0,
            underlying_invalidation=97.0,
            resistance_levels=(103.0, 108.0),
            gamma_resistance=105.0,
        ),
        market_context=StrategyMarketContext(),
        market_context_inputs=_valid_call_market_context_inputs(),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "VALID"
    assert result.target_1 == 103.0
    assert result.target_2 == 105.0
    assert result.context_status == "VALID"


def test_derived_context_still_requires_contract_constraints():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=StrategyMarketContext(),
        market_context_inputs=_valid_call_market_context_inputs(),
        contract_constraints=StrategyContractConstraints(),  # constraints_met=None
    )
    assert result.status == "INVALID"
    assert result.reason_code == "missing_contract_constraints"


# --- 6d. Increment 4B: contract-constraints-validator wiring --------------------------------


def test_explicit_strategy_contract_constraints_still_works_unchanged():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "VALID"
    assert result.contract_status is None
    assert result.contract_score is None
    assert result.contract_warnings == []
    assert result.contract_reason_code is None


def test_bullish_212_derives_clean_valid_contract_from_contract_constraints_inputs():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=StrategyContractConstraints(),  # constraints_met=None -> derive
        contract_constraints_inputs=_valid_call_contract_constraints_inputs(),
    )
    assert result.status == "VALID"
    assert result.contract_status == "VALID"
    assert result.contract_reason_code == "contract_confirmed"
    assert result.contract_warnings == []
    assert result.contract_score == 6.0


def test_bearish_212_derives_clean_valid_contract_from_contract_constraints_inputs():
    result = evaluate_strat_212(
        _bearish_bars(),
        direction="PUT",
        entry_trigger=96.0,
        underlying_invalidation=99.5,
        target_1=92.0,
        target_2=89.0,
        market_context=_confirmed_context(),
        contract_constraints=StrategyContractConstraints(),
        contract_constraints_inputs=_valid_put_contract_constraints_inputs(),
    )
    assert result.status == "VALID"
    assert result.contract_status == "VALID"
    assert result.contract_reason_code == "contract_confirmed"


def test_caution_contract_preserves_warnings_and_does_not_fake_a_clean_valid_contract():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=StrategyContractConstraints(),
        contract_constraints_inputs=_valid_call_contract_constraints_inputs(iv=0.85),
    )
    # The strategy is still tradeable (status VALID -- contract quality
    # does not itself invalidate the setup), but the CAUTION contract
    # must be surfaced honestly rather than reported as a clean VALID
    # contract.
    assert result.status == "VALID"
    assert result.contract_status == "CAUTION"
    assert result.contract_reason_code == "contract_mixed"
    assert result.contract_warnings


def test_invalid_contract_constraints_propagates_as_strategy_invalid():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=StrategyContractConstraints(),
        contract_constraints_inputs=_valid_call_contract_constraints_inputs(
            spread_percent=0.25
        ),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "contract_constraints_spread_too_wide"


def test_no_strategy_contract_constraints_and_no_contract_constraints_inputs_fails_closed():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=StrategyContractConstraints(),  # constraints_met=None
    )
    assert result.status == "INVALID"
    assert result.reason_code == "missing_contract_constraints"


def test_derived_contract_premium_over_max_fails_through_the_strategy():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=StrategyContractConstraints(),
        contract_constraints_inputs=_valid_call_contract_constraints_inputs(premium=6.0),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "contract_constraints_premium_over_max"


def test_derived_contract_low_volume_open_interest_fails_through_the_strategy():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=StrategyContractConstraints(),
        contract_constraints_inputs=_valid_call_contract_constraints_inputs(volume=50),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "contract_constraints_volume_too_low"

    oi_result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=StrategyContractConstraints(),
        contract_constraints_inputs=_valid_call_contract_constraints_inputs(
            open_interest=50
        ),
    )
    assert oi_result.status == "INVALID"
    assert oi_result.reason_code == "contract_constraints_open_interest_too_low"


def test_derived_contract_dte_too_short_fails_through_the_strategy():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=StrategyContractConstraints(),
        contract_constraints_inputs=_valid_call_contract_constraints_inputs(dte=3),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "contract_constraints_dte_too_short"


def test_derived_contract_high_event_and_earnings_risk_fails_through_the_strategy():
    earnings_result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=StrategyContractConstraints(),
        contract_constraints_inputs=_valid_call_contract_constraints_inputs(
            earnings_risk="HIGH"
        ),
    )
    assert earnings_result.status == "INVALID"
    assert earnings_result.reason_code == "contract_constraints_earnings_risk_high"

    event_result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=StrategyContractConstraints(),
        contract_constraints_inputs=_valid_call_contract_constraints_inputs(
            event_risk="HIGH"
        ),
    )
    assert event_result.status == "INVALID"
    assert event_result.reason_code == "contract_constraints_event_risk_high"


def test_derived_contract_bad_bid_ask_fails_through_the_strategy():
    bid_result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=StrategyContractConstraints(),
        contract_constraints_inputs=_valid_call_contract_constraints_inputs(bid=0.0),
    )
    assert bid_result.status == "INVALID"
    assert bid_result.reason_code == "contract_constraints_bid_invalid"

    ask_result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=StrategyContractConstraints(),
        contract_constraints_inputs=_valid_call_contract_constraints_inputs(
            bid=2.50, ask=2.50
        ),
    )
    assert ask_result.status == "INVALID"
    assert ask_result.reason_code == "contract_constraints_ask_invalid"


def test_derived_contract_does_not_break_target_finder_or_market_context():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=100.0,
        underlying_invalidation=97.0,
        level_inputs=LevelFinderInputs(
            direction="CALL",
            entry=100.0,
            underlying_invalidation=97.0,
            resistance_levels=(103.0, 108.0),
            gamma_resistance=105.0,
        ),
        market_context=StrategyMarketContext(),
        market_context_inputs=_valid_call_market_context_inputs(),
        contract_constraints=StrategyContractConstraints(),
        contract_constraints_inputs=_valid_call_contract_constraints_inputs(),
    )
    assert result.status == "VALID"
    assert result.target_1 == 103.0
    assert result.target_2 == 105.0
    assert result.context_status == "VALID"
    assert result.contract_status == "VALID"


def test_explicit_strategy_market_context_wins_over_market_context_inputs():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        # Explicit confirmed=True should win even though market_context_inputs
        # is also supplied and would otherwise derive a context.
        market_context=_confirmed_context(),
        market_context_inputs=_valid_call_market_context_inputs(
            spy_trend="bearish", qqq_trend="bearish", signa_direction="neutral"
        ),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "VALID"
    assert result.context_status is None
    assert result.context_score is None
    assert result.market_context_reason_code is None


# --- 7. missing market context -----------------------------------------------------------


def test_missing_market_context_fails_closed_to_invalid():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=StrategyMarketContext(),  # confirmed=None by default
        contract_constraints=_met_constraints(),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "missing_market_context"


def test_rejected_market_context_fails_closed_to_invalid():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=StrategyMarketContext(confirmed=False, notes="SPY/QQQ conflict"),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "market_context_rejected"


# --- 8. missing contract constraints ------------------------------------------------------


def test_missing_contract_constraints_fails_closed_to_invalid():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=StrategyContractConstraints(),  # constraints_met=None
    )
    assert result.status == "INVALID"
    assert result.reason_code == "missing_contract_constraints"


def test_rejected_contract_constraints_fails_closed_to_invalid():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=StrategyContractConstraints(
            constraints_met=False, notes="spread too wide"
        ),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "contract_constraints_rejected"


# --- invalidation/target side-sanity (defensive, not in the required list but cheap) -------


def test_invalidation_on_wrong_side_for_call_is_invalid():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=100.0,  # above entry — wrong side for CALL
        target_1=103.0,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "invalidation_wrong_side"


def test_direction_mismatch_against_classified_sequence_is_invalid():
    # Bars form a bullish strat_212, but the caller asks for PUT.
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="PUT",
        entry_trigger=99.0,
        underlying_invalidation=100.5,
        target_1=95.0,
        target_2=92.0,
        market_context=_confirmed_context(),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "direction_mismatch"


# --- 9. advisory output cannot bypass existing risk gates -----------------------------------
#
# Increment 1 wires nothing into options_manager's existing pipeline — a
# StrategySignal has no code path into risk_gate/contract_quality/
# dry_run_review/human_confirm/order_ticket/broker_boundary/
# mock_broker_preview/storage/http_api/app because the strategy layer
# never imports any of them. Proven structurally (AST import scan) rather
# than by exercising the pipeline, since no wiring exists yet to exercise.


def _imported_modules(module) -> list[str]:
    """Absolute module names only. `ast.ImportFrom.level > 0` marks a
    relative import (e.g. `from .base import X` inside strat_212.py) —
    those resolve within the same package, not to a top-level module
    named "base", so they are excluded rather than misreported as a
    cross-boundary import."""
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def test_strategy_modules_do_not_import_existing_pipeline_modules():
    for module in _SCANNED_STRATEGY_MODULES:
        imported = _imported_modules(module)
        overlap = set(imported) & set(_EXISTING_PIPELINE_MODULES)
        assert not overlap, (
            f"{module.__name__} must not import existing pipeline modules "
            f"(would create a bypass path): {overlap}"
        )


def test_strategy_modules_only_cross_boundary_import_is_strategy_strat_classifier():
    for module in _SCANNED_STRATEGY_MODULES:
        imported = _imported_modules(module)
        outside_options_manager = [
            name
            for name in imported
            if not name.startswith("options_manager")
            and name not in ("__future__", "dataclasses", "typing")
        ]
        assert set(outside_options_manager) <= {"strategy.strat_classifier", "strategy"}, (
            f"{module.__name__} has an unexpected cross-boundary import: "
            f"{outside_options_manager}"
        )


def test_strategy_modules_have_no_forbidden_imports():
    for module in _SCANNED_STRATEGY_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in name, f"{module.__name__} must not import {name!r}"


def test_strategy_modules_do_not_import_live_context_loader():
    for module in _SCANNED_STRATEGY_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            assert name != "context" and not name.startswith("context."), (
                f"{module.__name__} must not import the live context.* loader "
                f"(pulls config, not caller-supplied): {name}"
            )


def test_strategy_modules_have_no_order_action_verbs():
    for module in _SCANNED_STRATEGY_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in _FORBIDDEN_ORDER_ACTION_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_strategy_modules_do_not_mutate_live_options_flag():
    for module in _SCANNED_STRATEGY_MODULES:
        source = Path(module.__file__).read_text()
        assert "LIVE_OPTIONS_TRADING_ENABLED\"] =" not in source
        assert "LIVE_OPTIONS_TRADING_ENABLED'] =" not in source
        assert "LIVE_OPTIONS_TRADING_ENABLED" not in source
