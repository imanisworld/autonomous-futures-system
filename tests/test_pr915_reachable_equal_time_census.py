"""Structural census of reachable #915 equal-time collisions.

The test checks capacity reachability only. It does not score P&L.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from research.mnq_combined_portfolio_audit import PortfolioEvent, replay_portfolio
from research.mnq_reachable_equal_time_census import CASE_FIELDS, reachable_equal_time_cases

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ROOT / "research/artifacts/pr915-six-family-fillable-events-5a9f14b.jsonl"
CENSUS = ROOT / "research/artifacts/pr915-reachable-equal-time-collisions-5a9f14b.json"
FORBIDDEN = {"net_pnl", "result", "entry", "stop", "target", "profit_factor", "drawdown"}


def _events() -> list[PortfolioEvent]:
    return [
        PortfolioEvent(**json.loads(line))
        for line in EVENTS.read_text(encoding="utf-8").splitlines()
        if line
    ]


def test_reachable_census_matches_the_frozen_capacity_replay() -> None:
    events = _events()
    replay = replay_portfolio(events)
    cases = reachable_equal_time_cases(events)
    saved = json.loads(CENSUS.read_text(encoding="utf-8"))
    assert len(replay.fills) == 488
    assert cases == saved
    assert len(cases) == 47
    assert all(tuple(case) == CASE_FIELDS for case in cases)
    assert FORBIDDEN.isdisjoint(CASE_FIELDS)
    assert Counter(
        f"{case['exiting_family']} -> {case['candidate_family']}" for case in cases
    ) == Counter({
        "ASIA_D_EMA -> ASIA_D_EMA": 45,
        "DAILY_22_COMPLETED_CLOSE -> ASIA_D_EMA": 1,
        "ASIA_D_EMA -> DAILY_22_COMPLETED_CLOSE": 1,
    })
