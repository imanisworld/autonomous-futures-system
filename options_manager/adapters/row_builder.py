"""options_manager/adapters/row_builder.py

Pure, source-neutral row builder — Increment 13. Translates caller-
supplied, already-normalized adapter data (AdapterCandle,
AdapterOptionQuote, AdapterUnderlyingSnapshot,
AdapterMarketContextSnapshot) plus caller-supplied entry/invalidation/
risk-threshold values into a options_manager.scanner.WatchlistRow.

This module does not fetch anything -- it has no network/HTTP imports,
no login material, and no adapter-specific (e.g. Polygon) code at all. It
never runs the scanner or the strategy validator itself -- it only
builds the WatchlistRow those functions would later be called on by
their own caller. It does not infer entry_trigger/underlying_invalidation
from candles --
those remain explicit, caller-supplied values, exactly like every other
increment in this buildout. When an optional adapter snapshot (levels,
market context, contract quote) is not supplied, the corresponding
WatchlistRow field is left unset/None rather than invented -- the
scanner's own fail-closed behavior is unchanged by this translation
layer.
"""

from __future__ import annotations

from typing import Literal, Optional

from options_manager.context import MarketContextInputs
from options_manager.contracts import ContractConstraintsInputs
from options_manager.levels import LevelFinderInputs
from options_manager.scanner import WatchlistRow
from options_manager.strategies import (
    Strat212Bars,
    StrategyContractConstraints,
    StrategyMarketContext,
)

from .base import (
    AdapterCandle,
    AdapterMarketContextSnapshot,
    AdapterOptionQuote,
    AdapterUnderlyingSnapshot,
)


def build_watchlist_row_from_adapter_data(
    *,
    ticker: str,
    timestamp: str,
    direction: Literal["CALL", "PUT"],
    two_bars_back_type: Literal["two_up", "two_down"],
    two_bars_back_candle: AdapterCandle,
    previous_candle: AdapterCandle,
    current_candle: AdapterCandle,
    entry_trigger: Optional[float] = None,
    underlying_invalidation: Optional[float] = None,
    target_1: Optional[float] = None,
    target_2: Optional[float] = None,
    underlying_snapshot: Optional[AdapterUnderlyingSnapshot] = None,
    min_rr_threshold: Optional[float] = None,
    min_distance_to_target: Optional[float] = None,
    market_context: StrategyMarketContext = StrategyMarketContext(),
    market_context_snapshot: Optional[AdapterMarketContextSnapshot] = None,
    min_distance_to_gamma_level: Optional[float] = None,
    contract_constraints: StrategyContractConstraints = StrategyContractConstraints(),
    option_quote: Optional[AdapterOptionQuote] = None,
    max_premium: Optional[float] = None,
    max_spread_percent: Optional[float] = None,
    min_volume: Optional[int] = None,
    min_open_interest: Optional[int] = None,
    min_dte: Optional[int] = None,
    max_theta_abs: Optional[float] = None,
    notes: str = "",
    exclude: bool = False,
) -> WatchlistRow:
    """Pure function of its explicit inputs -> WatchlistRow. Fetches
    nothing; every value here is already in the caller's hands.

    `two_bars_back_type` must be supplied explicitly, exactly like
    Strat212Bars already requires -- classifying that bar would need a
    fourth, even-earlier candle this module never asks for, and this
    module must not reimplement strategy.strat_classifier's own
    candle-classification logic.

    `entry_trigger`/`underlying_invalidation`/`target_1`/`target_2` are
    never derived from the supplied candles -- they are always the
    caller's own explicit values, or left unresolved.

    `underlying_snapshot`/`market_context_snapshot`/`option_quote` are
    each optional independently: when omitted, the corresponding
    WatchlistRow field (`level_inputs`/`market_context_inputs`/
    `contract_constraints_inputs`) is left None rather than populated
    with an invented favorable default -- the scanner's own fail-closed
    behavior decides what happens next, unchanged by this translation.
    """
    bars = Strat212Bars(
        two_bars_back_type=two_bars_back_type,
        two_bars_back_high=two_bars_back_candle.high,
        two_bars_back_low=two_bars_back_candle.low,
        previous_high=previous_candle.high,
        previous_low=previous_candle.low,
        current_high=current_candle.high,
        current_low=current_candle.low,
    )

    level_inputs = None
    if underlying_snapshot is not None:
        level_inputs = LevelFinderInputs(
            direction=direction,
            entry=entry_trigger,
            underlying_invalidation=underlying_invalidation,
            resistance_levels=underlying_snapshot.resistance_levels,
            support_levels=underlying_snapshot.support_levels,
            gamma_resistance=underlying_snapshot.gamma_resistance,
            gamma_support=underlying_snapshot.gamma_support,
            min_rr_threshold=min_rr_threshold,
            min_distance_to_target=min_distance_to_target,
        )

    market_context_inputs = None
    if market_context_snapshot is not None:
        market_context_inputs = MarketContextInputs(
            direction=direction,
            ticker=ticker,
            underlying_price=underlying_snapshot.spot_price if underlying_snapshot else None,
            spy_trend=market_context_snapshot.spy_trend,
            qqq_trend=market_context_snapshot.qqq_trend,
            spy_above_flip=market_context_snapshot.spy_above_flip,
            qqq_above_flip=market_context_snapshot.qqq_above_flip,
            gex_regime=market_context_snapshot.gex_regime,
            price_above_gex_flip=market_context_snapshot.price_above_gex_flip,
            signa_direction=market_context_snapshot.signa_direction,
            signa_grade=market_context_snapshot.signa_grade,
            signa_score=market_context_snapshot.signa_score,
            higher_timeframe_alignment=market_context_snapshot.higher_timeframe_alignment,
            gap_direction=market_context_snapshot.gap_direction,
            distance_to_gamma_resistance=market_context_snapshot.distance_to_gamma_resistance,
            distance_to_gamma_support=market_context_snapshot.distance_to_gamma_support,
            event_risk=market_context_snapshot.event_risk,
            min_distance_to_gamma_level=min_distance_to_gamma_level,
        )

    contract_constraints_inputs = None
    if option_quote is not None:
        contract_constraints_inputs = ContractConstraintsInputs(
            direction=direction,
            ticker=ticker,
            expiration=option_quote.expiration,
            dte=option_quote.dte,
            strike=option_quote.strike,
            premium=option_quote.premium,
            bid=option_quote.bid,
            ask=option_quote.ask,
            spread_percent=option_quote.spread_percent,
            volume=option_quote.volume,
            open_interest=option_quote.open_interest,
            delta=option_quote.delta,
            theta=option_quote.theta,
            iv=option_quote.iv,
            max_premium=max_premium,
            max_spread_percent=max_spread_percent,
            min_volume=min_volume,
            min_open_interest=min_open_interest,
            min_dte=min_dte,
            max_theta_abs=max_theta_abs,
            earnings_risk=option_quote.earnings_risk,
            event_risk=option_quote.event_risk,
        )

    return WatchlistRow(
        ticker=ticker,
        timestamp=timestamp,
        direction=direction,
        bars=bars,
        entry_trigger=entry_trigger,
        underlying_invalidation=underlying_invalidation,
        target_1=target_1,
        target_2=target_2,
        level_inputs=level_inputs,
        market_context=market_context,
        market_context_inputs=market_context_inputs,
        contract_constraints=contract_constraints,
        contract_constraints_inputs=contract_constraints_inputs,
        notes=notes,
        exclude=exclude,
    )
