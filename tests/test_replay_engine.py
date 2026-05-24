"""
tests/test_replay_engine.py

Offline replay coverage.
"""

from __future__ import annotations

import json
from pathlib import Path

from replay import ReplayCandleLoader, ReplayEngine


def test_candle_loader_reads_sample_day():
    candles = ReplayCandleLoader().load_jsonl("data/replay/sample_day_mnq.jsonl")

    assert len(candles) == 3
    assert candles[0].instrument == "MNQ"
    assert candles[0].session == "new_york"
    assert candles[0].price_vs_vwap == "above"


def test_replay_engine_runs_sample_day(config, tmp_path):
    config.log_dir = str(tmp_path)
    report = ReplayEngine(config=config, log_dir=str(tmp_path)).run(
        "data/replay/sample_day_mnq.jsonl",
        review_date="2026-05-23",
    )

    assert report.candles_processed == 3
    assert report.approved_trades == 1
    assert report.wins == 1
    assert report.losses == 0
    assert report.open_trades == 0
    assert report.realized_pnl_dollars > 0
    assert Path(report.journal_path).exists()
    assert Path(report.review_path).exists()
    assert (tmp_path / "replay_report_2026-05-23.md").exists()


def test_replay_stops_after_max_trades(config, tmp_path):
    source = Path("data/replay/sample_day_mnq.jsonl")
    first_two = source.read_text().splitlines()[:2]
    replay_path = tmp_path / "many_trades.jsonl"
    replay_path.write_text("\n".join(first_two * 4) + "\n")

    report = ReplayEngine(config=config, log_dir=str(tmp_path / "logs")).run(
        replay_path,
        review_date="2026-05-23",
    )

    assert report.approved_trades == 3
    assert report.stopped_reason == "max_trades_per_day"


def test_replay_generates_no_trade_for_choppy_day(config, tmp_path):
    candle = json.loads(Path("data/replay/sample_day_mnq.jsonl").read_text().splitlines()[0])
    candle["market_condition"] = "CHOPPY"
    replay_path = tmp_path / "choppy.jsonl"
    replay_path.write_text(json.dumps(candle) + "\n")

    report = ReplayEngine(config=config, log_dir=str(tmp_path / "logs")).run(
        replay_path,
        review_date="2026-05-23",
    )

    assert report.approved_trades == 0
    assert report.no_trades == 1
    assert report.wins == 0
