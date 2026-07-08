"""options_manager/review/base.py

Advisory-only replay reporting model — Increment 6. Shared, pure data
contract for the reporting/rejection-review/outcome-review utilities in
replay_report.py. Every function in this package consumes a
caller-supplied Strat212ReplayReport (options_manager/replay, Increment
5) — it never runs a replay itself, never fetches data, never writes
files, never sends alerts, and performs no I/O of any kind.

Design note on warning aggregation: options_manager.replay.Strat212ReplayResult
already merges a row's strategy/context/contract warnings into a single
`warnings` list (this merge happens inside strat_212_replay.py's
_evaluate_row, added in Increment 5). This reporting layer consumes only
Strat212ReplayReport/Strat212ReplayResult (per requirement #1 — it does
not re-run evaluate_strat_212() or re-derive a StrategySignal), so it
cannot structurally recover which of those three sources a given warning
came from without modifying the Increment-5 replay wrapper, which is out
of scope here ("prefer additive-only"). aggregate_warnings() therefore
aggregates the single merged `warnings` field per row; this is flagged
explicitly in the PR report rather than faked as three independently
sourced categories.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(kw_only=True)
class ReplaySummary:
    """Deterministic, descriptive rollup of a Strat212ReplayReport. Purely
    computed from the report's own fields and per-row results — no option
    P&L, no premium simulation, no side effects."""

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
    top_rejection_reasons: list[tuple[str, int]]
    context_status_counts: dict[str, int]
    contract_status_counts: dict[str, int]
    average_rr_1: Optional[float]
    average_rr_2: Optional[float]


@dataclass(kw_only=True)
class RejectionReviewEntry:
    """One INVALID reason_code's share of the replayed rows, with a
    deterministic, capped sample of tickers/timestamps for human review."""

    reason_code: str
    count: int
    percent_of_total: float
    sample_tickers: list[str] = field(default_factory=list)
    sample_timestamps: list[str] = field(default_factory=list)


@dataclass(kw_only=True)
class OutcomeReviewEntry:
    """One replay_outcome's share of the VALID-status rows, with a
    deterministic, capped sample of tickers/timestamps for human review."""

    outcome: str
    count: int
    percent_of_valid_setups: float
    sample_tickers: list[str] = field(default_factory=list)
    sample_timestamps: list[str] = field(default_factory=list)


@dataclass(kw_only=True)
class WarningAggregation:
    """Deterministic counts of each distinct warning message across all
    replayed rows. See the module docstring above for why this is a
    single merged category rather than separate context/contract/result
    buckets."""

    warning_counts: dict[str, int]
    total_warnings: int
