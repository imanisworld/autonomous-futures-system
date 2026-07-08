"""options_manager/replay/base.py

Advisory-only 2-1-2 replay model — Increment 5. Shared contract for the
replay wrapper (strat_212_replay.py). Every row here is caller-supplied
historical data; this module never fetches candles, never fetches an
option chain, never fetches market data, never scans symbols, never
simulates option premium, and performs no I/O of any kind.

This module does not import replay/replay_engine.py (which has
execution/broker/journal imports per the earlier audit) or
replay/candle_loader.py (not needed — every row already carries its own
already-known Strat212Bars). It is a pure consumer of
options_manager.strategies.evaluate_strat_212(): it does not modify
strat_212.py, and it does not import options_manager's own risk_gate,
contract_quality, dry_run_review, human_confirm, order_ticket,
broker_boundary, mock_broker_preview, storage, http_api, app,
alert_ranker, options_companion, execution, webhook, or
risk/risk_engine.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from options_manager.context import MarketContextInputs
from options_manager.contracts import ContractConstraintsInputs
from options_manager.levels import LevelFinderInputs
from options_manager.strategies import (
    Strat212Bars,
    StrategyContractConstraints,
    StrategyMarketContext,
)

ReplayOutcomeStatus = Literal[
    "NOT_TRIGGERED",
    "INVALID",
    "TARGET_1_HIT",
    "TARGET_2_HIT",
    "STOP_HIT",
    "OPEN",
    "NO_OUTCOME_DATA",
]


@dataclass(frozen=True)
class Strat212ReplayRow:
    """One caller-supplied historical row to replay through
    evaluate_strat_212(). Nothing here is fetched — bars, entry/
    invalidation, target/level inputs, market-context inputs, contract-
    constraints inputs, and the future high/low/close snapshot used for
    outcome evaluation are all supplied by the caller. `future_high` /
    `future_low` represent the highest/lowest price reached in whatever
    forward window the caller chose to replay (e.g. the rest of the
    trading day); `future_close` is carried through for reference but is
    not used in target/stop hit logic."""

    ticker: str
    timestamp: str
    direction: Literal["CALL", "PUT"]
    bars: Strat212Bars
    entry_trigger: Optional[float] = None
    underlying_invalidation: Optional[float] = None
    target_1: Optional[float] = None
    target_2: Optional[float] = None
    level_inputs: Optional[LevelFinderInputs] = None
    market_context: StrategyMarketContext = field(default_factory=StrategyMarketContext)
    market_context_inputs: Optional[MarketContextInputs] = None
    contract_constraints: StrategyContractConstraints = field(
        default_factory=StrategyContractConstraints
    )
    contract_constraints_inputs: Optional[ContractConstraintsInputs] = None
    future_high: Optional[float] = None
    future_low: Optional[float] = None
    future_close: Optional[float] = None


@dataclass(kw_only=True)
class Strat212ReplayResult:
    """Per-row replay output. Never a broker call, never an order, never
    an execution side effect — a pure description of what
    evaluate_strat_212() said about this row and, if future price data
    was supplied, whether the resulting setup would have hit its target
    or its stop."""

    ticker: str
    timestamp: str
    status: str
    reason_code: str
    entry: Optional[float] = None
    invalidation: Optional[float] = None
    target_1: Optional[float] = None
    target_2: Optional[float] = None
    rr_1: Optional[float] = None
    rr_2: Optional[float] = None
    context_status: Optional[str] = None
    contract_status: Optional[str] = None
    replay_outcome: ReplayOutcomeStatus
    outcome_reason: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass(kw_only=True)
class Strat212ReplayReport:
    """Aggregate replay report across all rows. Purely descriptive
    counts/ratios over the per-row results — no option P&L, no premium
    simulation, no paper trades."""

    total_rows: int
    valid_setups: int
    invalid_setups: int
    watch_setups: int
    target_1_hits: int
    target_2_hits: int
    stop_hits: int
    no_outcome_data: int
    win_rate_target_1: Optional[float]
    rejection_counts_by_reason: dict[str, int]
    results: list[Strat212ReplayResult] = field(default_factory=list)
