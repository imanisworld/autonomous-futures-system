"""options_manager/strategies/strat_212.py

Mechanical Strat 2-1-2 continuation validator — Increment 1's first
concrete strategy. Reuses strategy.strat_classifier (pure, read-only,
already tested, no I/O) for candle/sequence classification; adds no
candle-classification logic of its own, so options_manager and the
futures system never disagree about what a 2-1-2 is.

Advisory only. This module never calls a broker, never places or
previews an order, and performs no I/O of any kind — it only reads three
already-known OHLC bars plus the caller-supplied entry/invalidation/
target/market-context/contract-constraint inputs, and returns a
StrategySignal.

Increment 2B: targets may either be supplied explicitly (target_1/
target_2, the original Increment 1 behavior) or derived from caller-
supplied levels via options_manager.levels.target_finder.find_targets()
when level_inputs is provided instead. find_targets() is a pure, local,
already-tested module with no I/O of its own; wiring it in adds no
execution, broker, scanner, replay, or market-data-fetching path.

Increment 3B: market context may either be supplied explicitly via
StrategyMarketContext (the original Increment 1 behavior — confirmed=
True/False) or derived from caller-supplied SPY/QQQ/GEX/Signa/HTF/
event-risk inputs via
options_manager.context.market_validator.evaluate_market_context() when
market_context_inputs is provided and market_context.confirmed is still
unresolved (None). evaluate_market_context() is a pure, local, already-
tested module with no I/O of its own; wiring it in adds no execution,
broker, scanner, replay, contract-selection, or market-data-fetching
path, and does not import the live context/market_context.py loader.

Increment 4B: contract constraints may either be supplied explicitly via
StrategyContractConstraints (the original Increment 1 behavior —
constraints_met=True/False) or derived from caller-supplied contract
data (liquidity, spread, greeks, DTE, earnings/event risk) via
options_manager.contracts.contract_validator.evaluate_contract_constraints()
when contract_constraints_inputs is provided and
contract_constraints.constraints_met is still unresolved (None).
evaluate_contract_constraints() is a pure, local, already-tested module
with no I/O of its own; wiring it in adds no execution, broker, scanner,
replay, contract-selection, or option-chain-fetching path, and does not
import options_companion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from strategy.strat_classifier import INSIDE_BAR, TWO_DOWN, TWO_UP, classify_from_ohlc

from options_manager.context import MarketContextInputs, evaluate_market_context
from options_manager.contracts import ContractConstraintsInputs, evaluate_contract_constraints
from options_manager.levels import LevelFinderInputs, find_targets

from .base import (
    StrategyContractConstraints,
    StrategyMarketContext,
    StrategySignal,
    _invalid,
    _watch,
)

STRATEGY_NAME = "strat_212_continuation"


@dataclass(kw_only=True)
class Strat212Bars:
    """The three bars of a 2-1-2 setup, oldest first.

    `two_bars_back_type` is the already-known classification of the first
    (directional) bar — deriving it would require a fourth, even earlier
    bar, which strategy.strat_classifier's own classify_from_ohlc does not
    ask for either; it is supplied directly, exactly like that module
    expects.
    """

    two_bars_back_type: Literal["two_up", "two_down"]
    two_bars_back_high: float
    two_bars_back_low: float
    previous_high: float
    previous_low: float
    current_high: float
    current_low: float


def evaluate_strat_212(
    bars: Strat212Bars,
    *,
    direction: Literal["CALL", "PUT"],
    entry_trigger: Optional[float],
    underlying_invalidation: Optional[float],
    target_1: Optional[float] = None,
    target_2: Optional[float] = None,
    level_inputs: Optional[LevelFinderInputs] = None,
    market_context: StrategyMarketContext,
    market_context_inputs: Optional[MarketContextInputs] = None,
    contract_constraints: StrategyContractConstraints,
    contract_constraints_inputs: Optional[ContractConstraintsInputs] = None,
) -> StrategySignal:
    """Pure function of its explicit inputs -> StrategySignal.

    Classifies the supplied bars via strategy.strat_classifier only —
    never reimplements candle classification. A CALL direction requires a
    bullish strat_212 sequence; a PUT direction requires the bearish
    mirror. A forming-but-not-yet-broken-out inside bar (still inside,
    matching the requested direction's directional bar) returns WATCH,
    not INVALID. Missing entry, invalidation, target, market context, or
    contract constraints all fail closed to INVALID — a strategy must
    never assume a favorable default for data it wasn't given.

    Targets: if target_1 and target_2 are both supplied explicitly, they
    are used as-is (original Increment 1 behavior, unchanged). If either
    is missing and level_inputs is supplied instead, targets are derived
    by calling options_manager.levels.target_finder.find_targets() with
    the already-known entry_trigger/underlying_invalidation plus
    level_inputs' resistance/support/gamma levels and thresholds. If the
    target finder returns INVALID, this validator returns INVALID with a
    "target_finder_<reason_code>" reason code that exposes the target
    finder's own reason code and message. If neither explicit targets nor
    level_inputs are supplied, this fails closed exactly as before
    (missing_target_1 / missing_target_2).

    Market context: if market_context.confirmed is explicitly True or
    False, that is used as-is (original Increment 1 behavior, unchanged).
    If market_context.confirmed is still None (unresolved) and
    market_context_inputs is supplied instead, market context is derived
    by calling evaluate_market_context(). If the market validator returns
    INVALID, this validator returns INVALID with a
    "market_context_<reason_code>" reason code that exposes the market
    validator's own reason code and message (e.g.
    "market_context_market_conflict"). If it returns VALID or CAUTION,
    the setup is allowed to proceed and context_status/context_score/
    context_warnings/market_context_reason_code are populated on the
    final StrategySignal — a CAUTION context is never reported as a
    clean VALID one. If neither an explicit confirmation nor
    market_context_inputs is supplied, this fails closed exactly as
    before (missing_market_context).

    Contract constraints: if contract_constraints.constraints_met is
    explicitly True or False, that is used as-is (original Increment 1
    behavior, unchanged). If contract_constraints.constraints_met is
    still None (unresolved) and contract_constraints_inputs is supplied
    instead, contract constraints are derived by calling
    evaluate_contract_constraints(). If the contract validator returns
    INVALID, this validator returns INVALID with a
    "contract_constraints_<reason_code>" reason code that exposes the
    contract validator's own reason code and message (e.g.
    "contract_constraints_spread_too_wide"). If it returns VALID or
    CAUTION, the setup is allowed to proceed and contract_status/
    contract_score/contract_warnings/contract_reason_code are populated
    on the final StrategySignal — a CAUTION contract is never reported
    as a clean VALID one. If neither an explicit confirmation nor
    contract_constraints_inputs is supplied, this fails closed exactly as
    before (missing_contract_constraints).
    """
    ctx = classify_from_ohlc(
        current_high=bars.current_high,
        current_low=bars.current_low,
        previous_high=bars.previous_high,
        previous_low=bars.previous_low,
        two_bars_back_high=bars.two_bars_back_high,
        two_bars_back_low=bars.two_bars_back_low,
        two_bars_back_type=bars.two_bars_back_type,
    )

    if ctx.strat_sequence == "strat_212":
        expected_strat_direction = "LONG" if direction == "CALL" else "SHORT"
        if ctx.strat_direction != expected_strat_direction:
            return _invalid(
                STRATEGY_NAME,
                direction,
                "direction_mismatch",
                f"strat_212 direction {ctx.strat_direction!r} does not match "
                f"requested {direction!r}",
            )
    else:
        expected_directional_bar = TWO_UP if direction == "CALL" else TWO_DOWN
        if (
            ctx.two_bars_back_type == expected_directional_bar
            and ctx.previous_bar_type == INSIDE_BAR
            and ctx.current_bar_type == INSIDE_BAR
        ):
            return _watch(
                STRATEGY_NAME,
                direction,
                "setup_forming_not_triggered",
                "inside bar has not yet broken in the requested direction",
            )
        return _invalid(
            STRATEGY_NAME,
            direction,
            "sequence_not_212",
            "classified bars do not form a strat_212 setup or a forming "
            f"watch state (bar_types={ctx.two_bars_back_type!r}/"
            f"{ctx.previous_bar_type!r}/{ctx.current_bar_type!r})",
        )

    if entry_trigger is None:
        return _invalid(
            STRATEGY_NAME, direction, "missing_entry_trigger", "entry_trigger is required"
        )
    if underlying_invalidation is None:
        return _invalid(
            STRATEGY_NAME,
            direction,
            "missing_invalidation",
            "underlying_invalidation is required",
        )
    derived_targets = None
    if target_1 is None or target_2 is None:
        if level_inputs is not None:
            derived_targets = find_targets(
                LevelFinderInputs(
                    direction=direction,
                    entry=entry_trigger,
                    underlying_invalidation=underlying_invalidation,
                    resistance_levels=level_inputs.resistance_levels,
                    support_levels=level_inputs.support_levels,
                    gamma_resistance=level_inputs.gamma_resistance,
                    gamma_support=level_inputs.gamma_support,
                    min_rr_threshold=level_inputs.min_rr_threshold,
                    min_distance_to_target=level_inputs.min_distance_to_target,
                )
            )
            if derived_targets.status == "INVALID":
                return _invalid(
                    STRATEGY_NAME,
                    direction,
                    f"target_finder_{derived_targets.reason_code}",
                    derived_targets.reason,
                )
            target_1 = derived_targets.target_1
            target_2 = derived_targets.target_2
        elif target_1 is None:
            return _invalid(
                STRATEGY_NAME, direction, "missing_target_1", "target_1 is required"
            )
        else:
            return _invalid(
                STRATEGY_NAME, direction, "missing_target_2", "target_2 is required"
            )

    if direction == "CALL" and underlying_invalidation >= entry_trigger:
        return _invalid(
            STRATEGY_NAME,
            direction,
            "invalidation_wrong_side",
            "underlying_invalidation must be below entry_trigger for CALL",
        )
    if direction == "PUT" and underlying_invalidation <= entry_trigger:
        return _invalid(
            STRATEGY_NAME,
            direction,
            "invalidation_wrong_side",
            "underlying_invalidation must be above entry_trigger for PUT",
        )

    if direction == "CALL" and (target_1 <= entry_trigger or target_2 <= entry_trigger):
        return _invalid(
            STRATEGY_NAME,
            direction,
            "target_wrong_side",
            "targets must be above entry_trigger for CALL",
        )
    if direction == "PUT" and (target_1 >= entry_trigger or target_2 >= entry_trigger):
        return _invalid(
            STRATEGY_NAME,
            direction,
            "target_wrong_side",
            "targets must be below entry_trigger for PUT",
        )

    context_status = None
    context_score = None
    context_warnings: list[str] = []
    market_context_reason_code = None

    if market_context.confirmed is None:
        if market_context_inputs is None:
            return _invalid(
                STRATEGY_NAME,
                direction,
                "missing_market_context",
                "market_context.confirmed is not resolved",
            )
        context_result = evaluate_market_context(market_context_inputs)
        if context_result.status == "INVALID":
            return _invalid(
                STRATEGY_NAME,
                direction,
                f"market_context_{context_result.reason_code}",
                context_result.reason,
            )
        context_status = context_result.status
        context_score = context_result.context_score
        context_warnings = context_result.warnings
        market_context_reason_code = context_result.reason_code
    elif market_context.confirmed is False:
        return _invalid(
            STRATEGY_NAME,
            direction,
            "market_context_rejected",
            market_context.notes or "market context rejected this setup",
        )

    contract_status = None
    contract_score = None
    contract_warnings: list[str] = []
    contract_reason_code = None

    if contract_constraints.constraints_met is None:
        if contract_constraints_inputs is None:
            return _invalid(
                STRATEGY_NAME,
                direction,
                "missing_contract_constraints",
                "contract_constraints.constraints_met is not resolved",
            )
        contract_result = evaluate_contract_constraints(contract_constraints_inputs)
        if contract_result.status == "INVALID":
            return _invalid(
                STRATEGY_NAME,
                direction,
                f"contract_constraints_{contract_result.reason_code}",
                contract_result.reason,
            )
        contract_status = contract_result.status
        contract_score = contract_result.contract_score
        contract_warnings = contract_result.warnings
        contract_reason_code = contract_result.reason_code
    elif contract_constraints.constraints_met is False:
        return _invalid(
            STRATEGY_NAME,
            direction,
            "contract_constraints_rejected",
            contract_constraints.notes or "contract constraints rejected this setup",
        )

    return StrategySignal(
        strategy_name=STRATEGY_NAME,
        direction=direction,
        status="VALID",
        reason_code="valid_212_continuation",
        reason=f"strat_212 {ctx.strat_direction} continuation confirmed",
        candle_sequence=ctx.strat_sequence,
        entry_trigger=entry_trigger,
        underlying_invalidation=underlying_invalidation,
        target_1=target_1,
        target_2=target_2,
        distance_to_target_1=derived_targets.distance_to_target_1 if derived_targets else None,
        distance_to_target_2=derived_targets.distance_to_target_2 if derived_targets else None,
        risk_amount=derived_targets.risk_amount if derived_targets else None,
        reward_1=derived_targets.reward_1 if derived_targets else None,
        reward_2=derived_targets.reward_2 if derived_targets else None,
        rr_1=derived_targets.rr_1 if derived_targets else None,
        rr_2=derived_targets.rr_2 if derived_targets else None,
        context_status=context_status,
        context_score=context_score,
        context_warnings=context_warnings,
        market_context_reason_code=market_context_reason_code,
        contract_status=contract_status,
        contract_score=contract_score,
        contract_warnings=contract_warnings,
        contract_reason_code=contract_reason_code,
    )
