"""
tests/test_review_agents.py

Tests for read-only trade review and daily risk grading.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.daily_summary import DailySummaryAgent
from agent.risk_reviewer import RiskReviewer
from agent.trade_grader import TradeGrader


def approved_trade(
    *,
    instrument: str = "MNQ",
    session: str = "new_york",
    strategy: str = "orb_reclaim",
    rr_ratio: float = 2.0,
    ts: str = "2026-05-23T14:30:00+00:00",
) -> dict:
    return {
        "ts": ts,
        "instrument": instrument,
        "session": session,
        "decision": "TRADE",
        "reason": "test approved trade",
        "market_condition": "TRENDING",
        "setup": {
            "direction": "LONG",
            "entry": 19500.0,
            "stop": 19480.0,
            "target": 19540.0,
            "rr_ratio": rr_ratio,
            "strategy": strategy,
            "notes": None,
        },
        "risk_check": {"result": "APPROVED", "failed_rule": None, "reason": None},
        "outcome": None,
    }


def outcome(result: str = "WIN", ts: str = "2026-05-23T14:35:00+00:00") -> dict:
    return {
        "ts": ts,
        "type": "OUTCOME",
        "instrument": "MNQ",
        "session": "new_york",
        "outcome": {
            "result": result,
            "entry_price": 19500.0,
            "exit_price": 19540.0 if result == "WIN" else 19480.0,
            "exit_reason": "TARGET_HIT" if result == "WIN" else "STOP_HIT",
            "pnl_ticks": 160.0 if result == "WIN" else -80.0,
            "pnl_dollars": 80.0 if result == "WIN" else -40.0,
        },
    }


def write_journal(log_dir: Path, review_date: str, entries: list[dict]) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"journal_{review_date}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")


def test_empty_journal_produces_safe_reports(config, tmp_path):
    config.log_dir = str(tmp_path)
    agent = DailySummaryAgent(config)

    morning = agent.morning("2026-05-23")
    eod = agent.eod("2026-05-23")

    assert morning["recommended_state"] == "OK_TO_PAPER_TRADE"
    assert eod["trade_grades"] == []
    assert (tmp_path / "review_2026-05-23.json").exists()
    assert (tmp_path / "daily_review_2026-05-23.md").exists()


def test_compliant_trade_grades_above_failing_trade(config):
    grader = TradeGrader(config)
    good = approved_trade()
    bad = approved_trade(session="asian", rr_ratio=1.0)
    grades = grader.grade_entries([good, outcome("WIN"), bad])

    assert grades[0].grade in ("A", "B")
    assert grades[1].grade == "F"
    assert grades[0].score > grades[1].score


def test_trade_outside_allowed_session_is_flagged(config):
    review = RiskReviewer(config).review_entries([approved_trade(session="asian")], "2026-05-23")
    assert "session_not_allowed" in review.violations
    assert review.recommended_state == "NO_TRADE"


def test_more_than_three_approved_trades_is_flagged(config):
    entries = [approved_trade(ts=f"2026-05-23T14:3{i}:00+00:00") for i in range(4)]
    review = RiskReviewer(config).review_entries(entries, "2026-05-23")
    assert "max_trades_per_day_exceeded" in review.violations


def test_two_consecutive_losses_creates_lockout_warning(config):
    entries = [approved_trade(), outcome("LOSS"), approved_trade(), outcome("LOSS")]
    review = RiskReviewer(config).review_entries(entries, "2026-05-23")
    assert "loss_lockout_active" in review.warnings
    assert review.recommended_state == "NO_TRADE"


def test_open_trade_without_outcome_is_flagged(config):
    review = RiskReviewer(config).review_entries([approved_trade()], "2026-05-23")
    assert "open_trade_without_outcome" in review.warnings
    assert review.open_trades == 1
    assert review.recommended_state == "NO_TRADE"


def test_daily_summary_writes_eod_artifacts(config, tmp_path):
    config.log_dir = str(tmp_path)
    write_journal(tmp_path, "2026-05-23", [approved_trade(), outcome("WIN")])

    report = DailySummaryAgent(config).eod("2026-05-23")

    assert report["trade_grades"][0]["grade"] in ("A", "B")
    assert (tmp_path / "review_2026-05-23.json").exists()
    assert (tmp_path / "trade_grades_2026-05-23.csv").exists()
    assert (tmp_path / "daily_review_2026-05-23.md").exists()


def test_review_agents_do_not_import_broker_execution():
    import agent.daily_summary as daily_summary
    import agent.risk_reviewer as risk_reviewer
    import agent.trade_grader as trade_grader

    combined = "\n".join(
        [
            Path(daily_summary.__file__).read_text(),
            Path(risk_reviewer.__file__).read_text(),
            Path(trade_grader.__file__).read_text(),
        ]
    )
    assert "from execution" not in combined
    assert "import execution" not in combined
    assert "PaperBroker" not in combined
    assert "Tradovate" not in combined
