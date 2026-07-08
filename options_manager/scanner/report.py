"""options_manager/scanner/report.py

Advisory-only scan reporting and rejection/no-trade review — Increment
10. Every function here is a pure function of a caller-supplied ScanReport
(options_manager.scanner, Increment 9): none of them scan a watchlist,
fetch data, write files, or send alerts. This module does not import
replay/replay_engine.py or replay/candle_loader.py, does not import the
live context.market_context loader, and does not import alert_ranker,
options_companion, execution, webhook, broker systems, or
risk/risk_engine.py.

Design note: this module keeps the Increment 9 NO_TRADE-vs-INVALID
distinction intact rather than merging them into one "rejection" bucket
-- rejection_review() covers only INVALID rows (incomplete/unsafe/
rejected setups) and no_trade_review() covers only NO_TRADE rows (no
actionable setup exists), since conflating the two would erase the exact
distinction Increment 9 was built to preserve.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from .base import ScanReport, ScanResult

_DEFAULT_SAMPLE_LIMIT = 5
_DEFAULT_TOP_N = 5
_UNRESOLVED_STATUS = "NONE"


@dataclass(kw_only=True)
class ScanSummary:
    """Deterministic, descriptive rollup of a ScanReport. Purely computed
    from the report's own fields and per-row results -- no ranking, no
    side effects."""

    total_rows: int
    triggered: int
    watch: int
    invalid: int
    no_trade: int
    counts_by_status: dict[str, int]
    counts_by_reason: dict[str, int]
    top_invalid_reasons: list[tuple[str, int]]
    top_no_trade_reasons: list[tuple[str, int]]
    context_status_counts: dict[str, int]
    contract_status_counts: dict[str, int]


@dataclass(kw_only=True)
class ScanReasonReviewEntry:
    """One reason_code's share of a particular scan_status bucket (either
    INVALID or NO_TRADE, never mixed), with a deterministic, capped
    sample of tickers/timestamps for human review."""

    reason_code: str
    count: int
    percent_of_total: float
    sample_tickers: list[str] = field(default_factory=list)
    sample_timestamps: list[str] = field(default_factory=list)


@dataclass(kw_only=True)
class ScanWarningAggregation:
    """Deterministic counts of each distinct warning message across all
    scanned rows' already-merged `warnings` field."""

    warning_counts: dict[str, int]
    total_warnings: int


def _percent(count: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return (count / denominator) * 100.0


def _status_counts(results: list[ScanResult], attr: str) -> dict[str, int]:
    counter: Counter[str] = Counter(
        getattr(r, attr) if getattr(r, attr) is not None else _UNRESOLVED_STATUS
        for r in results
    )
    return dict(sorted(counter.items()))


def _ranked_reason_counts(results: list[ScanResult]) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter(r.reason_code for r in results)
    return sorted(counter.items(), key=lambda item: (-item[1], item[0]))


def summarize_scan(report: ScanReport, *, top_n: int = _DEFAULT_TOP_N) -> ScanSummary:
    """Pure rollup of `report`. Deterministic: ties in reason ranking
    break alphabetically by reason_code."""
    results = report.results
    invalid_rows = [r for r in results if r.scan_status == "INVALID"]
    no_trade_rows = [r for r in results if r.scan_status == "NO_TRADE"]

    return ScanSummary(
        total_rows=report.total_rows,
        triggered=report.triggered,
        watch=report.watch,
        invalid=report.invalid,
        no_trade=report.no_trade,
        counts_by_status=dict(report.counts_by_status),
        counts_by_reason=dict(report.counts_by_reason),
        top_invalid_reasons=_ranked_reason_counts(invalid_rows)[:top_n],
        top_no_trade_reasons=_ranked_reason_counts(no_trade_rows)[:top_n],
        context_status_counts=_status_counts(results, "context_status"),
        contract_status_counts=_status_counts(results, "contract_status"),
    )


def _reason_review(
    results: list[ScanResult], *, total: int, sample_limit: int
) -> list[ScanReasonReviewEntry]:
    by_reason: dict[str, list[ScanResult]] = {}
    for row in results:
        by_reason.setdefault(row.reason_code, []).append(row)

    ranked = sorted(by_reason.items(), key=lambda item: (-len(item[1]), item[0]))

    return [
        ScanReasonReviewEntry(
            reason_code=reason_code,
            count=len(rows),
            percent_of_total=_percent(len(rows), total),
            sample_tickers=[r.ticker for r in rows[:sample_limit]],
            sample_timestamps=[r.timestamp for r in rows[:sample_limit]],
        )
        for reason_code, rows in ranked
    ]


def rejection_review(
    report: ScanReport, *, sample_limit: int = _DEFAULT_SAMPLE_LIMIT
) -> list[ScanReasonReviewEntry]:
    """One entry per distinct reason_code among INVALID rows only
    (incomplete/unsafe/rejected setups), ranked by count descending
    (ties alphabetical), each with a capped, deterministic sample of
    tickers/timestamps. percent_of_total is relative to total_rows."""
    invalid_rows = [r for r in report.results if r.scan_status == "INVALID"]
    return _reason_review(invalid_rows, total=report.total_rows, sample_limit=sample_limit)


def no_trade_review(
    report: ScanReport, *, sample_limit: int = _DEFAULT_SAMPLE_LIMIT
) -> list[ScanReasonReviewEntry]:
    """One entry per distinct reason_code among NO_TRADE rows only (no
    actionable setup exists), ranked by count descending (ties
    alphabetical), each with a capped, deterministic sample of
    tickers/timestamps. percent_of_total is relative to total_rows."""
    no_trade_rows = [r for r in report.results if r.scan_status == "NO_TRADE"]
    return _reason_review(no_trade_rows, total=report.total_rows, sample_limit=sample_limit)


def aggregate_scan_warnings(report: ScanReport) -> ScanWarningAggregation:
    """Deterministic counts of each distinct warning message across all
    scanned rows' already-merged `warnings` field."""
    counter: Counter[str] = Counter()
    for row in report.results:
        counter.update(row.warnings)
    return ScanWarningAggregation(
        warning_counts=dict(sorted(counter.items())),
        total_warnings=sum(counter.values()),
    )


def render_scan_summary_text(report: ScanReport) -> str:
    """Deterministic, plain-text (not markdown) human-readable summary.
    Line order and formatting are fixed so repeated calls on the same
    report always produce identical output."""
    summary = summarize_scan(report)

    lines = [
        "Scan Summary",
        f"Total rows: {summary.total_rows}",
        f"Triggered: {summary.triggered}",
        f"Watch: {summary.watch}",
        f"Invalid: {summary.invalid}",
        f"No trade: {summary.no_trade}",
        "Top invalid reasons:",
    ]
    if summary.top_invalid_reasons:
        for reason_code, count in summary.top_invalid_reasons:
            lines.append(f"  {reason_code}: {count}")
    else:
        lines.append("  none")

    lines.append("Top no-trade reasons:")
    if summary.top_no_trade_reasons:
        for reason_code, count in summary.top_no_trade_reasons:
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
