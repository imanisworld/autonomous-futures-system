"""Provenance of the 47 reachable #915 equal-time collisions.

No account overlay and no P&L.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from research.mnq_combined_portfolio_audit import PortfolioEvent
from research.mnq_reachable_order_audit import (
    CLASSIFICATIONS,
    DISTINCT_REQUEST_ORDER_UNKNOWN,
    EXIT_BEFORE_CANDIDATE_PROVEN,
    ROW_FIELDS,
    order_rows,
)

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ROOT / "research/artifacts/pr915-six-family-fillable-events-5a9f14b.jsonl"
CASES = ROOT / "research/artifacts/pr915-reachable-equal-time-collisions-5a9f14b.json"
AUDIT = ROOT / "research/artifacts/pr915-reachable-order-audit-5a9f14b.json"
FORBIDDEN = {"net_pnl", "result", "entry", "stop", "target", "profit_factor", "drawdown"}


def test_order_audit_matches_the_reachable_cases() -> None:
    events = [
        PortfolioEvent(**json.loads(line))
        for line in EVENTS.read_text(encoding="utf-8").splitlines()
        if line
    ]
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    rows = order_rows(events, cases)
    saved = json.loads(AUDIT.read_text(encoding="utf-8"))
    assert rows == saved
    assert len(rows) == 47
    assert all(tuple(row) == ROW_FIELDS for row in rows)
    assert FORBIDDEN.isdisjoint(ROW_FIELDS)
    assert all(row["classification"] in CLASSIFICATIONS for row in rows)
    assert Counter(row["classification"] for row in rows) == Counter({
        EXIT_BEFORE_CANDIDATE_PROVEN: 45,
        DISTINCT_REQUEST_ORDER_UNKNOWN: 2,
    })
    unknown = [row for row in rows if row["classification"] == DISTINCT_REQUEST_ORDER_UNKNOWN]
    assert {(row["exiting_source"], row["candidate_source"]) for row in unknown} == {
        (
            "daily22:2025-12-01:2025-11-30T23:00:00+00:00",
            "asia:2025-12-01T01:15:00+00:00:strat_22_continuation_observed:SHORT",
        ),
        (
            "asia:2026-02-05T02:30:00+00:00:strat_22_continuation_observed:SHORT",
            "daily22:2026-02-05:2026-02-05T12:40:00+00:00",
        ),
    }
    assert all(row["same_request"] is True for row in rows if row["classification"] == EXIT_BEFORE_CANDIDATE_PROVEN)
    assert all(row["same_request"] is False for row in unknown)
