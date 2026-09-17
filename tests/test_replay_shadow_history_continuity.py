"""Regression coverage for replay/live shadow-history continuity."""

from __future__ import annotations

import json
from pathlib import Path

import replay.replay_engine as replay_module
from replay.replay_engine import ReplayEngine


def _write_single_bar(path: Path, base: dict, timestamp: str) -> None:
    candle = dict(base)
    candle["timestamp"] = timestamp
    candle["market_condition"] = "CHOPPY"
    candle["orb_status"] = "inside"
    candle["timeframe"] = "15"
    path.write_text(json.dumps(candle) + "\n")


def test_shadow_history_spans_day_files_but_respects_live_lookback(
    config, tmp_path, monkeypatch
):
    """Replay must feed shadow observers the same rolling history live sees.

    Live uses BarHistory.recent(instrument, 8), whose default lookback spans the
    current calendar-day file plus the two prior files. Replay day files are an
    implementation detail and must not erase the immediately preceding bars,
    but bars three calendar days old must not leak forward either.
    """
    config.enabled_concepts = []
    base = json.loads(
        Path("data/replay/sample_day_mnq.jsonl").read_text().splitlines()[0]
    )

    day1 = tmp_path / "2026-05-20.jsonl"
    day2 = tmp_path / "2026-05-21.jsonl"
    day5 = tmp_path / "2026-05-24.jsonl"
    ts1 = "2026-05-20T23:45:00+00:00"
    ts2 = "2026-05-21T00:00:00+00:00"
    ts5 = "2026-05-24T00:00:00+00:00"
    _write_single_bar(day1, base, ts1)
    _write_single_bar(day2, base, ts2)
    _write_single_bar(day5, base, ts5)

    seen: list[list[str]] = []

    def capture_shadow_history(state, recent_bars=None, config=None):
        seen.append([bar["ts"] for bar in (recent_bars or [])])
        return []

    monkeypatch.setattr(
        replay_module, "evaluate_shadow_setups", capture_shadow_history
    )

    engine = ReplayEngine(config=config, log_dir=str(tmp_path / "logs"))
    engine.run(day1, review_date="2026-05-20")
    engine.run(day2, review_date="2026-05-21")
    engine.run(day5, review_date="2026-05-24")

    assert seen[0] == [ts1]
    # The 00:00 UTC file boundary must not erase the immediately prior bar.
    assert seen[1] == [ts1, ts2]
    # BarHistory.recent(..., lookback_days=3) would inspect May 24/23/22 only;
    # May 20/21 must therefore be excluded rather than leaking from the deque.
    assert seen[2] == [ts5]
