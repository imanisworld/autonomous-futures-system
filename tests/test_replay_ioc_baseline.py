"""Replay-level regression for the IOC-faithful baseline (Workstream A Phase 0).

The sample day produces exactly one approved trade that WINS under the legacy
market fill model. Under ioc_limit with a zero tolerance the same decision is
still made and journaled (signal formation is untouched) but the entry books
CANCELLED/ENTRY_NOT_FILLED like the live box: no win, no loss, no P&L, no
trade counted. With a generous tolerance the day is byte-equivalent to legacy
except the fill price sits at the (marketable) close.
"""
from __future__ import annotations

import json
from pathlib import Path

from replay import ReplayEngine

SAMPLE = "data/replay/sample_day_mnq.jsonl"


def _outcomes(journal_path: str) -> list[dict]:
    rows = [json.loads(l) for l in Path(journal_path).read_text().splitlines() if l.strip()]
    return [r["outcome"] for r in rows if isinstance(r.get("outcome"), dict)]


def test_ioc_no_fill_books_cancelled_and_counts_nothing(config, tmp_path):
    config.log_dir = str(tmp_path)
    config.entry_fill_model = "ioc_limit"
    config.entry_tolerance_ticks_by_root = {"MNQ": 0.0}
    report = ReplayEngine(config=config, log_dir=str(tmp_path)).run(
        SAMPLE, review_date="2026-05-23"
    )
    # The decision pipeline is untouched: the trade is still approved+journaled.
    assert report.approved_trades == 1
    # ...but the entry never fills, so nothing is won, lost, left open, or earned.
    assert report.wins == 0
    assert report.losses == 0
    assert report.open_trades == 0
    assert report.realized_pnl_dollars == 0.0
    outcomes = _outcomes(report.journal_path)
    assert len(outcomes) == 1
    assert outcomes[0]["result"] == "CANCELLED"
    assert outcomes[0]["exit_reason"] == "ENTRY_NOT_FILLED"
    assert outcomes[0]["pnl_dollars"] == 0.0


def test_ioc_marketable_day_matches_legacy_result(config, tmp_path):
    config.log_dir = str(tmp_path)
    config.entry_fill_model = "ioc_limit"
    # Generous tolerance: the decision-bar close is always marketable.
    config.entry_tolerance_ticks_by_root = {"MNQ": 10_000.0}
    report = ReplayEngine(config=config, log_dir=str(tmp_path)).run(
        SAMPLE, review_date="2026-05-23"
    )
    assert report.approved_trades == 1
    assert report.wins == 1
    assert report.losses == 0
    assert report.open_trades == 0


def test_default_market_model_unchanged(config, tmp_path):
    config.log_dir = str(tmp_path)
    report = ReplayEngine(config=config, log_dir=str(tmp_path)).run(
        SAMPLE, review_date="2026-05-23"
    )
    assert report.approved_trades == 1
    assert report.wins == 1
    assert report.realized_pnl_dollars > 0


def test_stop_market_keeps_decision_but_uses_next_bar_entry(config, tmp_path):
    config.log_dir = str(tmp_path)
    config.entry_fill_model = "stop_market"
    report = ReplayEngine(config=config, log_dir=str(tmp_path)).run(
        SAMPLE, review_date="2026-05-23"
    )
    # Signal formation/risk approval is unchanged from the legacy market model.
    assert report.approved_trades == 1
    outcomes = _outcomes(report.journal_path)
    assert len(outcomes) == 1
    assert outcomes[0]["result"] in {"WIN", "LOSS", "BREAKEVEN", "CANCELLED"}
    assert outcomes[0]["exit_reason"] != "ENTRY_OPEN_UNAVAILABLE"
