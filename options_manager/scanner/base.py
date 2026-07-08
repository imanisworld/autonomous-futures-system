"""options_manager/scanner/base.py

Advisory-only 2-1-2 watchlist scanner model — Increment 9. Shared,
caller-supplied data contract for the scanner (strat_212_scanner.py).
Every row here is caller-supplied: this module never fetches a quote,
never fetches an option chain, never fetches market data, never scans a
live symbol universe, and performs no I/O of any kind.

Design note (Increment 8 audit decisions, applied here):
- WatchlistRow is a new, purpose-built model — it deliberately does not
  reuse options_manager.replay.Strat212ReplayRow, which carries
  replay-only future_high/future_low/future_close fields that have no
  meaning for a live scan (a scanner has no known future price).
- ScanResult/ScanReport are new, scanner-specific models — they do not
  reuse options_manager.replay.Strat212ReplayResult/Strat212ReplayReport,
  which carry replay-outcome fields (replay_outcome, target_1_hits, etc.)
  that likewise have no meaning here.
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
    StrategySignal,
)

ScanStatus = Literal["TRIGGERED", "WATCH", "INVALID", "NO_TRADE"]


@dataclass(frozen=True)
class WatchlistRow:
    """One caller-supplied ticker/setup to scan through evaluate_strat_212().
    Nothing here is fetched — bars, entry/invalidation, target/level
    inputs, market-context inputs, and contract-constraints inputs are
    all supplied by the caller, exactly like options_manager.replay's
    Strat212ReplayRow, minus that model's replay-only future_* fields.

    `exclude` is a caller-controlled bypass only (e.g. "I already know I
    don't want this ticker scanned this run") — it carries no session,
    market-hours, or time-based logic of its own."""

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
    notes: str = ""
    exclude: bool = False


@dataclass(kw_only=True)
class ScanResult:
    """Per-row scan output. Never a broker call, never an order, never an
    execution side effect, never an alert — a pure description of what
    evaluate_strat_212() said about this row, translated into the
    scanner's own TRIGGERED/WATCH/INVALID/NO_TRADE vocabulary."""

    ticker: str
    timestamp: str
    scan_status: ScanStatus
    strategy_status: Optional[str] = None
    reason_code: str
    reason: str = ""
    signal: Optional[StrategySignal] = None
    entry: Optional[float] = None
    invalidation: Optional[float] = None
    target_1: Optional[float] = None
    target_2: Optional[float] = None
    rr_1: Optional[float] = None
    rr_2: Optional[float] = None
    context_status: Optional[str] = None
    contract_status: Optional[str] = None
    warnings: list[str] = field(default_factory=list)


@dataclass(kw_only=True)
class ScanReport:
    """Aggregate scan report across all rows. Purely descriptive counts
    over the per-row results — no ranking, no alerting, no side effects."""

    total_rows: int
    triggered: int
    watch: int
    invalid: int
    no_trade: int
    results: list[ScanResult] = field(default_factory=list)
    counts_by_status: dict[str, int] = field(default_factory=dict)
    counts_by_reason: dict[str, int] = field(default_factory=dict)
