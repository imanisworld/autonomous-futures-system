"""options_manager/replay/fixtures.py

Deterministic replay proof fixtures — Increment 7. Static, in-code sample
data that exercises the whole advisory-only 2-1-2 replay path (strategy
evaluation, target/level derivation, market-context derivation, contract-
constraints derivation, and outcome resolution) without ever fetching
anything. Every fixture builder returns already-known, fixed values —
tickers, timestamps, candles, levels, market context, and contract
constraints are all hardcoded here, never loaded from a file, network
call, or live data source.

This module performs no I/O of any kind: no candle fetch, no option-chain
fetch, no market-data fetch, no broker call, no order placement, no
execution, no symbol scanning, no alert sending, no file reads/writes at
runtime. It does not import replay/replay_engine.py or
replay/candle_loader.py, does not import the live context.market_context
loader, and does not import alert_ranker, options_companion, execution,
webhook, broker systems, or risk/risk_engine.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from options_manager.context import MarketContextInputs
from options_manager.contracts import ContractConstraintsInputs
from options_manager.levels import LevelFinderInputs
from options_manager.review import (
    OutcomeReviewEntry,
    RejectionReviewEntry,
    ReplaySummary,
    WarningAggregation,
    aggregate_warnings,
    outcome_review,
    rejection_review,
    render_summary_text,
    summarize_replay,
)
from options_manager.strategies import (
    Strat212Bars,
    StrategyContractConstraints,
    StrategyMarketContext,
)

from .base import Strat212ReplayReport, Strat212ReplayRow
from .strat_212_replay import replay_strat_212


def _bullish_bars() -> Strat212Bars:
    """A caller-supplied bar triple that classifies as a strat_212 LONG
    continuation (matches the fixture used across Increments 5/6's own
    tests)."""
    return Strat212Bars(
        two_bars_back_type="two_up",
        two_bars_back_high=100.0,
        two_bars_back_low=95.0,
        previous_high=99.0,
        previous_low=96.0,
        current_high=101.0,
        current_low=96.5,
    )


def _bearish_bars() -> Strat212Bars:
    """A caller-supplied bar triple that classifies as a strat_212 SHORT
    continuation."""
    return Strat212Bars(
        two_bars_back_type="two_down",
        two_bars_back_high=100.0,
        two_bars_back_low=95.0,
        previous_high=99.0,
        previous_low=96.0,
        current_high=98.5,
        current_low=94.0,
    )


def _forming_bars() -> Strat212Bars:
    """A caller-supplied bar triple still forming (both previous and
    current are inside bars) — the requested direction's setup has not
    broken out yet, so the strategy reports WATCH, not INVALID."""
    return Strat212Bars(
        two_bars_back_type="two_up",
        two_bars_back_high=100.0,
        two_bars_back_low=95.0,
        previous_high=99.0,
        previous_low=96.0,
        current_high=98.5,
        current_low=96.5,
    )


def _bad_sequence_bars() -> Strat212Bars:
    """A caller-supplied bar triple that is neither a strat_212 sequence
    nor a forming watch state (previous bar is directional, not inside),
    so the strategy fails closed to INVALID sequence_not_212."""
    return Strat212Bars(
        two_bars_back_type="two_up",
        two_bars_back_high=100.0,
        two_bars_back_low=95.0,
        previous_high=104.0,
        previous_low=99.0,
        current_high=108.0,
        current_low=103.0,
    )


def valid_call_target_1_hit_row() -> Strat212ReplayRow:
    return Strat212ReplayRow(
        ticker="FX_CALL_T1",
        timestamp="2026-01-02T10:00:00Z",
        direction="CALL",
        bars=_bullish_bars(),
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=StrategyMarketContext(confirmed=True),
        contract_constraints=StrategyContractConstraints(constraints_met=True),
        future_high=104.0,
        future_low=98.0,
    )


def valid_call_target_2_hit_row() -> Strat212ReplayRow:
    return Strat212ReplayRow(
        ticker="FX_CALL_T2",
        timestamp="2026-01-02T10:01:00Z",
        direction="CALL",
        bars=_bullish_bars(),
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=StrategyMarketContext(confirmed=True),
        contract_constraints=StrategyContractConstraints(constraints_met=True),
        future_high=107.0,
        future_low=98.0,
    )


def valid_call_stop_hit_row() -> Strat212ReplayRow:
    return Strat212ReplayRow(
        ticker="FX_CALL_STOP",
        timestamp="2026-01-02T10:02:00Z",
        direction="CALL",
        bars=_bullish_bars(),
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=StrategyMarketContext(confirmed=True),
        contract_constraints=StrategyContractConstraints(constraints_met=True),
        future_high=100.0,
        future_low=94.0,
    )


def valid_put_target_1_hit_row() -> Strat212ReplayRow:
    return Strat212ReplayRow(
        ticker="FX_PUT_T1",
        timestamp="2026-01-02T10:03:00Z",
        direction="PUT",
        bars=_bearish_bars(),
        entry_trigger=96.0,
        underlying_invalidation=99.5,
        target_1=92.0,
        target_2=89.0,
        market_context=StrategyMarketContext(confirmed=True),
        contract_constraints=StrategyContractConstraints(constraints_met=True),
        future_high=97.0,
        future_low=91.0,
    )


def valid_put_target_2_hit_row() -> Strat212ReplayRow:
    return Strat212ReplayRow(
        ticker="FX_PUT_T2",
        timestamp="2026-01-02T10:04:00Z",
        direction="PUT",
        bars=_bearish_bars(),
        entry_trigger=96.0,
        underlying_invalidation=99.5,
        target_1=92.0,
        target_2=89.0,
        market_context=StrategyMarketContext(confirmed=True),
        contract_constraints=StrategyContractConstraints(constraints_met=True),
        future_high=97.0,
        future_low=88.0,
    )


def valid_put_stop_hit_row() -> Strat212ReplayRow:
    return Strat212ReplayRow(
        ticker="FX_PUT_STOP",
        timestamp="2026-01-02T10:05:00Z",
        direction="PUT",
        bars=_bearish_bars(),
        entry_trigger=96.0,
        underlying_invalidation=99.5,
        target_1=92.0,
        target_2=89.0,
        market_context=StrategyMarketContext(confirmed=True),
        contract_constraints=StrategyContractConstraints(constraints_met=True),
        future_high=100.0,
        future_low=95.0,
    )


def watch_not_triggered_row() -> Strat212ReplayRow:
    return Strat212ReplayRow(
        ticker="FX_WATCH",
        timestamp="2026-01-02T10:06:00Z",
        direction="CALL",
        bars=_forming_bars(),
        entry_trigger=None,
        underlying_invalidation=None,
        target_1=None,
        target_2=None,
        market_context=StrategyMarketContext(),
        contract_constraints=StrategyContractConstraints(),
    )


def invalid_bad_sequence_row() -> Strat212ReplayRow:
    return Strat212ReplayRow(
        ticker="FX_BAD_SEQ",
        timestamp="2026-01-02T10:07:00Z",
        direction="CALL",
        bars=_bad_sequence_bars(),
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=StrategyMarketContext(confirmed=True),
        contract_constraints=StrategyContractConstraints(constraints_met=True),
    )


def invalid_bad_market_context_row() -> Strat212ReplayRow:
    return Strat212ReplayRow(
        ticker="FX_BAD_CONTEXT",
        timestamp="2026-01-02T10:08:00Z",
        direction="CALL",
        bars=_bullish_bars(),
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=StrategyMarketContext(),
        market_context_inputs=MarketContextInputs(
            direction="CALL",
            ticker="FX_BAD_CONTEXT",
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
            event_risk="high",
        ),
        contract_constraints=StrategyContractConstraints(constraints_met=True),
    )


def invalid_bad_contract_constraints_row() -> Strat212ReplayRow:
    return Strat212ReplayRow(
        ticker="FX_BAD_CONTRACT",
        timestamp="2026-01-02T10:09:00Z",
        direction="CALL",
        bars=_bullish_bars(),
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=StrategyMarketContext(confirmed=True),
        contract_constraints=StrategyContractConstraints(),
        contract_constraints_inputs=ContractConstraintsInputs(
            direction="CALL",
            ticker="FX_BAD_CONTRACT",
            expiration="2026-08-01",
            dte=30,
            strike=505.0,
            premium=2.50,
            bid=2.45,
            ask=2.55,
            spread_percent=0.50,
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
        ),
    )


def invalid_target_too_close_or_poor_rr_row() -> Strat212ReplayRow:
    return Strat212ReplayRow(
        ticker="FX_POOR_RR",
        timestamp="2026-01-02T10:10:00Z",
        direction="CALL",
        bars=_bullish_bars(),
        entry_trigger=100.0,
        underlying_invalidation=97.0,
        level_inputs=LevelFinderInputs(
            direction="CALL",
            entry=100.0,
            underlying_invalidation=97.0,
            resistance_levels=(103.0, 108.0),
            min_rr_threshold=2.0,
        ),
        market_context=StrategyMarketContext(confirmed=True),
        contract_constraints=StrategyContractConstraints(constraints_met=True),
    )


def no_outcome_data_valid_setup_row() -> Strat212ReplayRow:
    return Strat212ReplayRow(
        ticker="FX_NO_OUTCOME",
        timestamp="2026-01-02T10:11:00Z",
        direction="CALL",
        bars=_bullish_bars(),
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=StrategyMarketContext(confirmed=True),
        contract_constraints=StrategyContractConstraints(constraints_met=True),
    )


_FIXTURE_BUILDERS = (
    valid_call_target_1_hit_row,
    valid_call_target_2_hit_row,
    valid_call_stop_hit_row,
    valid_put_target_1_hit_row,
    valid_put_target_2_hit_row,
    valid_put_stop_hit_row,
    watch_not_triggered_row,
    invalid_bad_sequence_row,
    invalid_bad_market_context_row,
    invalid_bad_contract_constraints_row,
    invalid_target_too_close_or_poor_rr_row,
    no_outcome_data_valid_setup_row,
)


def build_replay_proof_dataset() -> list[Strat212ReplayRow]:
    """Returns a fresh list of the 12 fixed replay proof rows, in a fixed
    order, covering every required outcome type. Each call rebuilds the
    rows from the individual builder functions above rather than
    returning a shared/cached list, so nothing here can accumulate
    mutated state across calls (the rows themselves are also frozen
    dataclasses)."""
    return [builder() for builder in _FIXTURE_BUILDERS]


def run_replay_proof_dataset(
    rows: list[Strat212ReplayRow] | None = None,
) -> Strat212ReplayReport:
    """Replays `rows` (defaulting to build_replay_proof_dataset()) through
    replay_strat_212() and returns the resulting Strat212ReplayReport."""
    if rows is None:
        rows = build_replay_proof_dataset()
    return replay_strat_212(rows)


@dataclass(kw_only=True)
class ReplayProofReview:
    """Bundled review of a replay proof report, using the Increment 6
    reporting utilities unchanged."""

    summary: ReplaySummary
    rejections: list[RejectionReviewEntry] = field(default_factory=list)
    outcomes: list[OutcomeReviewEntry] = field(default_factory=list)
    warnings: WarningAggregation
    summary_text: str


def review_replay_proof_dataset(
    rows: list[Strat212ReplayRow] | None = None,
) -> ReplayProofReview:
    """Runs the replay proof dataset (or `rows`, if supplied) and applies
    every options_manager.review reporting utility against the resulting
    report."""
    report = run_replay_proof_dataset(rows)
    return ReplayProofReview(
        summary=summarize_replay(report),
        rejections=rejection_review(report),
        outcomes=outcome_review(report),
        warnings=aggregate_warnings(report),
        summary_text=render_summary_text(report),
    )
