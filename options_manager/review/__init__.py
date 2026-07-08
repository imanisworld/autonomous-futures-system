"""options_manager/review — advisory-only replay reporting layer.

Increment 6. Pure reporting, rejection-review, outcome-review, and
warning-aggregation utilities that consume a caller-supplied
Strat212ReplayReport (options_manager/replay, Increment 5). Runs no
replay itself, fetches no data, writes no files, sends no alerts, and
performs no I/O of any kind.
"""

from __future__ import annotations

from .base import (
    OutcomeReviewEntry,
    RejectionReviewEntry,
    ReplaySummary,
    WarningAggregation,
)
from .replay_report import (
    aggregate_warnings,
    outcome_review,
    rejection_review,
    render_summary_text,
    summarize_replay,
)

__all__ = [
    "OutcomeReviewEntry",
    "RejectionReviewEntry",
    "ReplaySummary",
    "WarningAggregation",
    "aggregate_warnings",
    "outcome_review",
    "rejection_review",
    "render_summary_text",
    "summarize_replay",
]
