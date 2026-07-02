#!/usr/bin/env python3
"""Observe-only walk-forward scorecard for causal continuation candidates."""
from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import load_config
from replay.candle_loader import ReplayCandleLoader
from replay.replay_engine import ReplayEngine
from strategy.shadow_setups import evaluate_shadow_setups, resolve_shadow_candidate


NAMES = {
    "impulse_first_pullback_observed",
    "trend_consolidation_break_observed",
}
TICK_VALUE = {"MES": 1.25, "MNQ": 0.50}


def summarize(rows):
    resolved = [r for r in rows if r["result"] in {"WIN", "LOSS"}]
    wins = [r for r in resolved if r["result"] == "WIN"]
    pnls = [r["pnl_dollars"] for r in resolved]
    equity = peak = drawdown = 0.0
    streak = max_streak = 0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        streak = streak + 1 if pnl < 0 else 0
        max_streak = max(max_streak, streak)
    return {
        "candidates": len(rows),
        "resolved": len(resolved),
        "wins": len(wins),
        "losses": len(resolved) - len(wins),
        "win_rate": len(wins) / len(resolved) if resolved else 0,
        "net_pnl": round(sum(pnls), 2),
        "expectancy": round(sum(pnls) / len(resolved), 2) if resolved else 0,
        "max_drawdown": round(drawdown, 2),
        "max_consecutive_losses": max_streak,
    }


def run(candle_dir: Path):
    engine = ReplayEngine(config=load_config(), log_dir="/tmp/continuation-scorecard")
    rows = []
    for path in sorted(candle_dir.glob("*.jsonl")):
        candles = ReplayCandleLoader().load_jsonl(path)
        history = deque(maxlen=8)
        previous = None
        emitted: set[tuple[str, str]] = set()
        for idx, candle in enumerate(candles):
            history.append(
                {
                    "ts": candle.timestamp,
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": candle.volume,
                }
            )
            state = engine._market_state_from_candle(candle, previous)
            previous = candle
            for candidate in evaluate_shadow_setups(state, list(history)):
                if candidate.strategy not in NAMES:
                    continue
                identity = (candidate.strategy, candidate.direction)
                # Exactly the first occurrence per direction/day.
                if identity in emitted:
                    continue
                emitted.add(identity)
                outcome = resolve_shadow_candidate(
                    candidate,
                    [(c.high, c.low) for c in candles[idx + 1:]],
                    instrument=state.instrument,
                    pessimistic_both_hit=True,
                )
                pnl_ticks = outcome.pnl_ticks or 0.0
                rows.append(
                    {
                        "date": path.stem[-10:],
                        "instrument": state.instrument,
                        "strategy": candidate.strategy,
                        "direction": candidate.direction,
                        "result": outcome.result,
                        "pnl_dollars": pnl_ticks * TICK_VALUE[state.instrument],
                    }
                )
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candles", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    rows = run(args.candles)
    dates = sorted({r["date"] for r in rows})
    midpoint_date = dates[len(dates) // 2] if dates else ""
    output = {}
    for name in sorted(NAMES):
        selected = [r for r in rows if r["strategy"] == name]
        output[name] = {
            "all": summarize(selected),
            "first_half": summarize([r for r in selected if r["date"] < midpoint_date]),
            "second_half": summarize([r for r in selected if r["date"] >= midpoint_date]),
        }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True))
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
