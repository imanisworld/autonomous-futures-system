"""ReplayEngine integration for strict market_at_reference fills."""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import execution.research_reference_paper as research_reference_paper
from execution.research_reference_paper import ResearchReferencePaperBroker
from replay import ReplayEngine
from replay.replay_engine import _strict_net_pnl
from strategy.signal_engine import DecisionEngine, DecisionOutput, SetupDetail


def test_strict_net_pnl_subtracts_round_turn_commission() -> None:
    assert _strict_net_pnl(100.0, 1.48) == pytest.approx(98.52)
    assert _strict_net_pnl(None, 1.48) is None


def test_strict_replay_runs_sample_day(config, tmp_path):
    """End-to-end ReplayEngine.run with ResearchReferencePaperBroker (strict mode)."""
    used_research_broker = False
    original_init = ResearchReferencePaperBroker.__init__

    def _track_init(self, *args, **kwargs):
        nonlocal used_research_broker
        used_research_broker = True
        return original_init(self, *args, **kwargs)

    research_reference_paper.ResearchReferencePaperBroker.__init__ = _track_init  # type: ignore[method-assign]
    try:
        strict = dataclasses.replace(
            config,
            entry_fill_model="market_at_reference",
            research_commission_round_trip=1.48,
        )
        report = ReplayEngine(config=strict, log_dir=str(tmp_path / "logs")).run(
            "data/replay/sample_day_mnq.jsonl",
            review_date="2026-05-23",
        )
    finally:
        research_reference_paper.ResearchReferencePaperBroker.__init__ = original_init  # type: ignore[method-assign]

    assert used_research_broker
    assert report.candles_processed == 3


def test_strict_replay_journals_net_pnl_after_commission(config, tmp_path, monkeypatch):
    """Resolved strict-reference trades must journal net = gross - round-turn commission."""
    original_evaluate = DecisionEngine.evaluate
    forced = [False]

    def _force_one_trade(self, state, daily_state):
        if not forced[0]:
            forced[0] = True
            return DecisionOutput(
                timestamp=datetime.now(timezone.utc),
                instrument="MNQ",
                session="new_york",
                decision="TRADE",
                reason="test",
                setup=SetupDetail(
                    direction="LONG",
                    entry=19505.0,
                    stop=19495.0,
                    target=19525.0,
                    rr_ratio=2.0,
                    strategy="orb_reclaim",
                    notes="test",
                ),
                failed_gates=[],
            )
        return original_evaluate(self, state, daily_state)

    monkeypatch.setattr(DecisionEngine, "evaluate", _force_one_trade)

    base = json.loads(Path("data/replay/sample_day_mnq.jsonl").read_text().splitlines()[0])
    bar0 = {**base, "close": 19505.0}
    bar1 = {
        **base,
        "timestamp": "2026-05-23T14:35:00+00:00",
        "open": 19505.0,
        "high": 19530.0,
        "low": 19500.0,
        "close": 19520.0,
        "market_condition": "TRENDING",
    }
    replay_path = tmp_path / "two_bar_win.jsonl"
    replay_path.write_text(json.dumps(bar0) + "\n" + json.dumps(bar1) + "\n")

    commission = 1.48
    strict = dataclasses.replace(
        config,
        entry_fill_model="market_at_reference",
        research_commission_round_trip=commission,
        enabled_concepts=["orb_reclaim"],
        max_trades_per_day=10,
    )
    report = ReplayEngine(config=strict, log_dir=str(tmp_path / "logs")).run(
        replay_path,
        review_date="2026-05-23",
    )
    assert report.wins == 1
    rows = [json.loads(line) for line in Path(report.journal_path).read_text().splitlines()]
    win = next(
        r
        for r in rows
        if r.get("type") == "OUTCOME" and r["outcome"].get("result") == "WIN"
    )
    audit = win["outcome"]["execution_audit"]
    gross = audit["gross_pnl_dollars"]
    assert win["outcome"]["pnl_dollars"] == pytest.approx(float(gross) - commission)
    assert report.realized_pnl_dollars == pytest.approx(float(gross) - commission)


def test_strict_replay_gapped_next_bar_journals_entry_not_filled(config, tmp_path, monkeypatch):
    """Missing adjacent next bar must not crash the replay run."""
    original_evaluate = DecisionEngine.evaluate
    forced = [False]

    def _force_one_trade(self, state, daily_state):
        if not forced[0]:
            forced[0] = True
            return DecisionOutput(
                timestamp=datetime.now(timezone.utc),
                instrument="MNQ",
                session="new_york",
                decision="TRADE",
                reason="test",
                setup=SetupDetail(
                    direction="LONG",
                    entry=19500.0,
                    stop=19480.0,
                    target=19540.0,
                    rr_ratio=2.0,
                    strategy="orb_reclaim",
                    notes="test",
                ),
                failed_gates=[],
            )
        return original_evaluate(self, state, daily_state)

    monkeypatch.setattr(DecisionEngine, "evaluate", _force_one_trade)

    candle = json.loads(Path("data/replay/sample_day_mnq.jsonl").read_text().splitlines()[0])
    replay_path = tmp_path / "one_bar.jsonl"
    replay_path.write_text(json.dumps(candle) + "\n")

    strict = dataclasses.replace(
        config,
        entry_fill_model="market_at_reference",
        enabled_concepts=["orb_reclaim"],
        max_trades_per_day=10,
    )
    report = ReplayEngine(config=strict, log_dir=str(tmp_path / "logs")).run(
        replay_path,
        review_date="2026-05-23",
    )
    rows = [json.loads(line) for line in Path(report.journal_path).read_text().splitlines()]
    cancelled = [
        r
        for r in rows
        if r.get("type") == "OUTCOME"
        and r["outcome"].get("exit_reason") == "ENTRY_NOT_FILLED"
    ]
    assert cancelled, "expected ENTRY_NOT_FILLED when next bar is absent"
