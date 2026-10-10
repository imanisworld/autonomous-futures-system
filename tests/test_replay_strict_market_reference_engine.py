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
from risk.risk_engine import RiskEngine
from strategy.signal_engine import DecisionEngine, DecisionOutput, SetupDetail
from strategy.strat_212_122 import STRAT_212


def test_strict_net_pnl_subtracts_round_turn_commission_per_contract() -> None:
    assert _strict_net_pnl(100.0, 1.48, 1) == pytest.approx(98.52)
    assert _strict_net_pnl(100.0, 1.48, 2) == pytest.approx(97.04)
    assert _strict_net_pnl(None, 1.48, 2) is None


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


def _force_one_orb_trade(setup: SetupDetail):
    original_evaluate = DecisionEngine.evaluate
    forced = [False]

    def _force(self, state, daily_state):
        if not forced[0]:
            forced[0] = True
            return DecisionOutput(
                timestamp=datetime.now(timezone.utc),
                instrument="MNQ",
                session="new_york",
                decision="TRADE",
                reason="test",
                setup=setup,
                failed_gates=[],
            )
        return original_evaluate(self, state, daily_state)

    return _force


def test_strict_replay_entry_uses_next_bar_open_not_signal_close(
    config, tmp_path, monkeypatch
):
    setup = SetupDetail(
        direction="LONG",
        entry=19505.0,
        stop=19495.0,
        target=19525.0,
        rr_ratio=2.0,
        strategy="orb_reclaim",
        notes="test",
    )
    monkeypatch.setattr(DecisionEngine, "evaluate", _force_one_orb_trade(setup))

    base = json.loads(Path("data/replay/sample_day_mnq.jsonl").read_text().splitlines()[0])
    signal_ts = base["timestamp"]
    bar0 = {**base, "close": 19508.0}
    entry_bar_ts = "2026-05-23T14:35:00+00:00"
    bar1 = {
        **base,
        "timestamp": entry_bar_ts,
        "open": 19505.0,
        "high": 19530.0,
        "low": 19500.0,
        "close": 19520.0,
        "market_condition": "TRENDING",
    }
    replay_path = tmp_path / "next_open_entry.jsonl"
    replay_path.write_text(json.dumps(bar0) + "\n" + json.dumps(bar1) + "\n")

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
    win = next(
        r
        for r in rows
        if r.get("type") == "OUTCOME" and r["outcome"].get("result") == "WIN"
    )
    audit = win["outcome"]["execution_audit"]
    assert audit["historical_signal_bar_ts"] == signal_ts
    assert audit["historical_entry_bar_ts"] == entry_bar_ts
    assert win["outcome"]["entry_price"] == pytest.approx(19505.0)
    assert win["outcome"]["entry_price"] != pytest.approx(bar0["close"])


def test_strict_replay_two_contract_commission_is_296(config, tmp_path, monkeypatch):
    setup = SetupDetail(
        direction="LONG",
        entry=19505.0,
        stop=19495.0,
        target=19525.0,
        rr_ratio=2.0,
        strategy="orb_reclaim",
        notes="test",
    )
    monkeypatch.setattr(DecisionEngine, "evaluate", _force_one_orb_trade(setup))
    monkeypatch.setattr(
        RiskEngine,
        "recommended_contracts",
        lambda self, instrument, balance: 2,
    )

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
    replay_path = tmp_path / "two_contract_win.jsonl"
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
    gross = float(audit["gross_pnl_dollars"])
    assert audit["commission_dollars"] == pytest.approx(2.96)
    assert win["outcome"]["pnl_dollars"] == pytest.approx(gross - 2.96)
    assert win["outcome"]["contracts"] == 2


def test_strict_replay_strat_212_journals_entry_not_filled(config, tmp_path, monkeypatch):
    setup = SetupDetail(
        direction="LONG",
        entry=19500.0,
        stop=19480.0,
        target=19540.0,
        rr_ratio=2.0,
        strategy=STRAT_212,
        notes="test",
    )
    monkeypatch.setattr(DecisionEngine, "evaluate", _force_one_orb_trade(setup))

    lines = Path("data/replay/sample_day_mnq.jsonl").read_text().splitlines()
    bar0 = json.loads(lines[0])
    bar1 = json.loads(lines[1])
    replay_path = tmp_path / "strat212_two_bar.jsonl"
    replay_path.write_text(json.dumps(bar0) + "\n" + json.dumps(bar1) + "\n")

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
    assert report.wins == 0
    rows = [json.loads(line) for line in Path(report.journal_path).read_text().splitlines()]
    cancelled = [
        r
        for r in rows
        if r.get("type") == "OUTCOME"
        and r["outcome"].get("exit_reason") == "ENTRY_NOT_FILLED"
        and r["outcome"].get("strategy") == STRAT_212
    ]
    assert cancelled


def test_strict_replay_gapped_next_bar_journals_entry_not_filled(config, tmp_path, monkeypatch):
    """Missing adjacent next bar must not crash the replay run."""
    setup = SetupDetail(
        direction="LONG",
        entry=19500.0,
        stop=19480.0,
        target=19540.0,
        rr_ratio=2.0,
        strategy="orb_reclaim",
        notes="test",
    )
    monkeypatch.setattr(DecisionEngine, "evaluate", _force_one_orb_trade(setup))

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
