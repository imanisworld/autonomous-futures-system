"""Ordering provenance for the 47 reachable #915 equal-time collisions.

Structure only. This module does not score P&L and does not call the
account-admission overlay.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Iterable

from research.mnq_combined_portfolio_audit import PortfolioEvent, parse_ts

EXIT_BEFORE_CANDIDATE_PROVEN = "EXIT_BEFORE_CANDIDATE_PROVEN"
CANDIDATE_BEFORE_EXIT_PROVEN = "CANDIDATE_BEFORE_EXIT_PROVEN"
DISTINCT_REQUEST_ORDER_UNKNOWN = "DISTINCT_REQUEST_ORDER_UNKNOWN"
INSUFFICIENT_PROVENANCE = "INSUFFICIENT_PROVENANCE"

_FAMILY_MINUTES = {
    "ASIA_D_EMA": 15,
    "DAILY_22_COMPLETED_CLOSE": 5,
}

CLASSIFICATIONS = (
    EXIT_BEFORE_CANDIDATE_PROVEN,
    CANDIDATE_BEFORE_EXIT_PROVEN,
    DISTINCT_REQUEST_ORDER_UNKNOWN,
    INSUFFICIENT_PROVENANCE,
)

ROW_FIELDS = (
    "timestamp",
    "exiting_source",
    "candidate_source",
    "exit_source_bar_timeframe",
    "candidate_source_bar_timeframe",
    "same_request",
    "proven_ordering",
    "evidence",
    "classification",
)


def _bar_start(close_ts: str, minutes: int):
    return parse_ts(close_ts) - timedelta(minutes=minutes)


def _label(close_ts: str, minutes: int) -> str:
    start = _bar_start(close_ts, minutes)
    return f"{minutes}m bar {start.isoformat()} close {parse_ts(close_ts).isoformat()}"


def order_rows(
    events: Iterable[PortfolioEvent],
    cases: Iterable[dict[str, str]],
) -> list[dict[str, str | bool]]:
    by_id = {event.source_id: event for event in events}
    rows: list[dict[str, str | bool]] = []
    for case in cases:
        exiting = by_id[case["accepted_source_id"]]
        candidate = by_id[case["candidate_source_id"]]
        timestamp = case["collision_timestamp"]
        if parse_ts(exiting.exit_ts or "") != parse_ts(timestamp):
            raise ValueError(f"{exiting.source_id} exit is not the collision timestamp")
        if parse_ts(candidate.eligible_fill_ts) != parse_ts(timestamp):
            raise ValueError(f"{candidate.source_id} fill is not the collision timestamp")
        exit_minutes = _FAMILY_MINUTES.get(exiting.family)
        candidate_minutes = _FAMILY_MINUTES.get(candidate.family)
        if exit_minutes is None or candidate_minutes is None:
            rows.append(_row(
                timestamp, exiting.source_id, candidate.source_id,
                "unknown", "unknown", False, "unknown",
                "Family timeframe is not in the archived #915 adapter map.",
                INSUFFICIENT_PROVENANCE,
            ))
            continue
        if exiting.family == "ASIA_D_EMA" and candidate.family == "ASIA_D_EMA":
            if _bar_start(timestamp, 15) == _bar_start(exiting.eligible_fill_ts, 15):
                rows.append(_row(
                    timestamp, exiting.source_id, candidate.source_id,
                    _label(timestamp, 15), _label(timestamp, 15), False, "unknown",
                    "The Asia exit close equals that position's own entry close, so process_bar would not advance it.",
                    INSUFFICIENT_PROVENANCE,
                ))
                continue
            rows.append(_row(
                timestamp, exiting.source_id, candidate.source_id,
                _label(timestamp, 15), _label(timestamp, 15), True,
                "exit before candidate",
                "Same 15m bar close. process_alert is one bar. "
                "asia_d_ema_paper_cohort.process_bar clears an open cohort position on that bar "
                "before it considers that bar's candidates. This cohort call does not reach RiskEngine.validate.",
                EXIT_BEFORE_CANDIDATE_PROVEN,
            ))
            continue
        rows.append(_row(
            timestamp, exiting.source_id, candidate.source_id,
            _label(timestamp, exit_minutes), _label(timestamp, candidate_minutes),
            False, "unknown",
            "The exit and the candidate are closes of different bar timeframes, so they are separate "
            "process_alert payloads. No arrival order between those payloads is recorded.",
            DISTINCT_REQUEST_ORDER_UNKNOWN,
        ))
    return rows


def _row(
    timestamp: str,
    exiting_source: str,
    candidate_source: str,
    exit_bar: str,
    candidate_bar: str,
    same_request: bool,
    proven_ordering: str,
    evidence: str,
    classification: str,
) -> dict[str, str | bool]:
    return {
        "timestamp": timestamp,
        "exiting_source": exiting_source,
        "candidate_source": candidate_source,
        "exit_source_bar_timeframe": exit_bar,
        "candidate_source_bar_timeframe": candidate_bar,
        "same_request": same_request,
        "proven_ordering": proven_ordering,
        "evidence": evidence,
        "classification": classification,
    }
