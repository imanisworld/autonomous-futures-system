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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from strategy.strat_classifier import INSIDE_BAR, TWO_DOWN, TWO_UP, classify_from_ohlc

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
    target_1: Optional[float],
    target_2: Optional[float],
    market_context: StrategyMarketContext,
    contract_constraints: StrategyContractConstraints,
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
    if target_1 is None:
        return _invalid(STRATEGY_NAME, direction, "missing_target_1", "target_1 is required")
    if target_2 is None:
        return _invalid(STRATEGY_NAME, direction, "missing_target_2", "target_2 is required")

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

    if market_context.confirmed is None:
        return _invalid(
            STRATEGY_NAME,
            direction,
            "missing_market_context",
            "market_context.confirmed is not resolved",
        )
    if market_context.confirmed is False:
        return _invalid(
            STRATEGY_NAME,
            direction,
            "market_context_rejected",
            market_context.notes or "market context rejected this setup",
        )

    if contract_constraints.constraints_met is None:
        return _invalid(
            STRATEGY_NAME,
            direction,
            "missing_contract_constraints",
            "contract_constraints.constraints_met is not resolved",
        )
    if contract_constraints.constraints_met is False:
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
    )
