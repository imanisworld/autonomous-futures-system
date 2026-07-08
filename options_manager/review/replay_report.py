"""options_manager/review/replay_report.py

Advisory-only replay reporting and rejection/outcome review — Increment
6. Every function here is a pure function of a caller-supplied
Strat212ReplayReport (options_manager/replay, Increment 5): none of them
run a replay, fetch data, write files, or send alerts. This module does
not import replay/replay_engine.py or replay/candle_loader.py, does not
import the live context.market_context loader, and does not import
alert_ranker, options_companion, execution, webhook, broker systems, or
risk/risk_engine.py.
"""

from __future__ import annotations

from collections import Counter
from typing import Optional

from options_manager.replay import Strat212ReplayReport, Strat212ReplayResult

from .base import (
    OutcomeReviewEntry,
    RejectionReviewEntry,
    ReplaySummary,
    WarningAggregation,
)

_DEFAULT_SAMPLE_LIMIT = 5
_DEFAULT_TOP_N = 5
_UNRESOLVED_STATUS = "NONE"


def _percent(count: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return (count / denominator) * 100.0


def _status_counts(results: list[Strat212ReplayResult], attr: str) -> dict[str, int]:
    counter: Counter[str] = Counter(
        getattr(r, attr) if getattr(r, attr) is not None else _UNRESOLVED_STATUS
        for r in results
    )
    return dict(sorted(counter.items()))


def _average(values: list[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def summarize_replay(
    report: Strat212ReplayReport, *, top_n: int = _DEFAULT_TOP_N
) -> ReplaySummary:
    """Pure rollup of `report`. Deterministic: ties in rejection ranking
    break alphabetically by reason_code."""
    results = report.results

    ranked_rejections = sorted(
        report.rejection_counts_by_reason.items(), key=lambda item: (-item[1], item[0])
    )

    valid_rows = [r for r in results if r.status == "VALID"]
    rr_1_values = [r.rr_1 for r in valid_rows if r.rr_1 is not None]
    rr_2_values = [r.rr_2 for r in valid_rows if r.rr_2 is not None]

    return ReplaySummary(
        total_rows=report.total_rows,
        valid_setups=report.valid_setups,
        invalid_setups=report.invalid_setups,
        watch_setups=report.watch_setups,
        target_1_hits=report.target_1_hits,
        target_2_hits=report.target_2_hits,
        stop_hits=report.stop_hits,
        no_outcome_data=report.no_outcome_data,
        win_rate_target_1=report.win_rate_target_1,
        rejection_counts_by_reason=dict(report.rejection_counts_by_reason),
        top_rejection_reasons=ranked_rejections[:top_n],
        context_status_counts=_status_counts(results, "context_status"),
        contract_status_counts=_status_counts(results, "contract_status"),
        average_rr_1=_average(rr_1_values),
        average_rr_2=_average(rr_2_values),
    )


def rejection_review(
    report: Strat212ReplayReport, *, sample_limit: int = _DEFAULT_SAMPLE_LIMIT
) -> list[RejectionReviewEntry]:
    """One entry per distinct INVALID reason_code, ranked by count
    descending (ties broken alphabetically), each with a capped,
    deterministic sample of tickers/timestamps drawn in replay order."""
    invalid_rows = [r for r in report.results if r.status == "INVALID"]
    total = report.total_rows

    by_reason: dict[str, list[Strat212ReplayResult]] = {}
    for row in invalid_rows:
        by_reason.setdefault(row.reason_code, []).append(row)

    ranked = sorted(by_reason.items(), key=lambda item: (-len(item[1]), item[0]))

    return [
        RejectionReviewEntry(
            reason_code=reason_code,
            count=len(rows),
            percent_of_total=_percent(len(rows), total),
            sample_tickers=[r.ticker for r in rows[:sample_limit]],
            sample_timestamps=[r.timestamp for r in rows[:sample_limit]],
        )
        for reason_code, rows in ranked
    ]


def outcome_review(
    report: Strat212ReplayReport, *, sample_limit: int = _DEFAULT_SAMPLE_LIMIT
) -> list[OutcomeReviewEntry]:
    """One entry per distinct replay_outcome among VALID-status rows
    (TARGET_1_HIT, TARGET_2_HIT, STOP_HIT, OPEN, NO_OUTCOME_DATA), ranked
    by count descending (ties broken alphabetically), each with a capped,
    deterministic sample of tickers/timestamps."""
    valid_rows = [r for r in report.results if r.status == "VALID"]
    total_valid = report.valid_setups

    by_outcome: dict[str, list[Strat212ReplayResult]] = {}
    for row in valid_rows:
        by_outcome.setdefault(row.replay_outcome, []).append(row)

    ranked = sorted(by_outcome.items(), key=lambda item: (-len(item[1]), item[0]))

    return [
        OutcomeReviewEntry(
            outcome=outcome,
            count=len(rows),
            percent_of_valid_setups=_percent(len(rows), total_valid),
            sample_tickers=[r.ticker for r in rows[:sample_limit]],
            sample_timestamps=[r.timestamp for r in rows[:sample_limit]],
        )
        for outcome, rows in ranked
    ]


def aggregate_warnings(report: Strat212ReplayReport) -> WarningAggregation:
    """Deterministic counts of each distinct warning message across all
    replayed rows' already-merged `warnings` field (see base.py's module
    docstring for why this is one merged category, not three)."""
    counter: Counter[str] = Counter()
    for row in report.results:
        counter.update(row.warnings)
    return WarningAggregation(
        warning_counts=dict(sorted(counter.items())),
        total_warnings=sum(counter.values()),
    )


def render_summary_text(report: Strat212ReplayReport) -> str:
    """Deterministic, plain-text (not markdown) human-readable summary.
    Line order and formatting are fixed so repeated calls on the same
    report always produce identical output."""
    summary = summarize_replay(report)

    def _pct(value: Optional[float]) -> str:
        return "n/a" if value is None else f"{value * 100:.1f}%"

    def _avg(value: Optional[float]) -> str:
        return "n/a" if value is None else f"{value:.2f}"

    lines = [
        "Replay Summary",
        f"Total rows: {summary.total_rows}",
        f"Valid setups: {summary.valid_setups}",
        f"Invalid setups: {summary.invalid_setups}",
        f"Watch setups: {summary.watch_setups}",
        f"Target 1 hits: {summary.target_1_hits}",
        f"Target 2 hits: {summary.target_2_hits}",
        f"Stop hits: {summary.stop_hits}",
        f"No outcome data: {summary.no_outcome_data}",
        f"Win rate (target 1): {_pct(summary.win_rate_target_1)}",
        f"Average RR1: {_avg(summary.average_rr_1)}",
        f"Average RR2: {_avg(summary.average_rr_2)}",
        "Top rejection reasons:",
    ]
    if summary.top_rejection_reasons:
        for reason_code, count in summary.top_rejection_reasons:
            lines.append(f"  {reason_code}: {count}")
    else:
        lines.append("  none")

    lines.append("Context status counts:")
    if summary.context_status_counts:
        for status, count in summary.context_status_counts.items():
            lines.append(f"  {status}: {count}")
    else:
        lines.append("  none")

    lines.append("Contract status counts:")
    if summary.contract_status_counts:
        for status, count in summary.contract_status_counts.items():
            lines.append(f"  {status}: {count}")
    else:
        lines.append("  none")

    return "\n".join(lines)
