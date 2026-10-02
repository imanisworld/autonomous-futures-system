"""Reachable equal-time collisions under frozen #915 capacity arbitration.

This module reports structure only. It does not summarize P&L, profit factor,
drawdown, or win/loss, and it does not call the account-admission overlay.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from research.mnq_combined_portfolio_audit import (
    MAX_FILLS_PER_DAY,
    PortfolioEvent,
    parse_ts,
    replay_portfolio,
)

CASE_FIELDS = (
    "accepted_source_id",
    "collision_timestamp",
    "exiting_family",
    "candidate_family",
    "candidate_source_id",
)


def reachable_equal_time_cases(events: Iterable[PortfolioEvent]) -> list[dict[str, str]]:
    """Return the next capacity-reachable candidate at an accepted exit tie.

    Frozen `replay_portfolio` keeps a position open when `exit_ts` equals the
    candidate `eligible_fill_ts`. A case is reachable when that is the only
    capacity reason the next candidate would miss: the observation day is
    still under the three-fill cap, and no earlier candidate at that same
    timestamp would already have taken the freed slot.
    """
    rows = list(events)
    replay = replay_portfolio(rows)
    by_id = {event.source_id: event for event in rows}
    fills_by_day: dict[str, int] = defaultdict(int)
    current_key: tuple[str, str] | None = None
    slot_taken = False
    cases: list[dict[str, str]] = []

    for decision in replay.decisions:
        event = by_id[decision.source_id]
        if decision.disposition == "FILLED":
            fills_by_day[event.observation_day] += 1
            continue
        if decision.disposition != "SKIPPED_BUSY_PORTFOLIO":
            continue
        blocker = by_id[decision.blocker_source_id or ""]
        if blocker.exit_ts is None or parse_ts(blocker.exit_ts) != parse_ts(event.eligible_fill_ts):
            continue
        key = (blocker.source_id, event.eligible_fill_ts)
        if key != current_key:
            current_key = key
            slot_taken = False
        if slot_taken or fills_by_day[event.observation_day] >= MAX_FILLS_PER_DAY:
            continue
        slot_taken = True
        cases.append(
            {
                "accepted_source_id": blocker.source_id,
                "collision_timestamp": event.eligible_fill_ts,
                "exiting_family": blocker.family,
                "candidate_family": event.family,
                "candidate_source_id": event.source_id,
            }
        )
    return cases
