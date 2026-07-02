#!/usr/bin/env python3
"""Execution-realistic scorecard for authorized ORB -> 5m retest entries.

Consumes completed 15m replay journals plus synchronized 5m Polygon replay
files. It never runs strategy discovery on 5m data: only exact ORB brackets
already approved by the 15m engine are eligible.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from context.bar_history import _parse_dt
from execution.broker_interface import BracketOrder
from execution.paper_broker import NextBarOHLC, PaperBroker
from replay.retest_lane import FineBar, RetestArm, simulate_arm


ORB = {"orb_breakout", "orb_reclaim"}


def load_arms(journal_dir: Path) -> list[RetestArm]:
    arms: list[RetestArm] = []
    for path in sorted(journal_dir.glob("journal_*.jsonl")):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            setup = row.get("setup") or {}
            if (
                row.get("decision") != "TRADE"
                or (row.get("risk_check") or {}).get("result") != "APPROVED"
                or setup.get("strategy") not in ORB
            ):
                continue
            source_ts = _parse_dt(str(row.get("bar_ts") or ""))
            if source_ts is None:
                continue
            instrument = str(row.get("instrument") or "").upper()
            arms.append(
                RetestArm(
                    instrument=instrument,
                    # A 15m setup becomes knowable only at bar close.
                    armed_at=source_ts + timedelta(minutes=15),
                    direction=str(setup["direction"]),
                    entry=float(setup["entry"]),
                    stop=float(setup["stop"]),
                    target=float(setup["target"]),
                    strategy=str(setup["strategy"]),
                    arm_id=f"{path.stem}:{row.get('bar_ts')}",
                )
            )
    return arms


def load_fine_bars(root: Path, instrument: str, day: str) -> list[FineBar]:
    path = root / instrument / f"{instrument}_{day}.jsonl"
    if not path.exists():
        return []
    bars: list[FineBar] = []
    for line in path.read_text().splitlines():
        row = json.loads(line)
        ts = _parse_dt(str(row.get("timestamp") or row.get("ts") or ""))
        if ts is not None:
            bars.append(
                FineBar(
                    timestamp=ts,
                    instrument=instrument,
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                )
            )
    return bars


def resolve_runner(
    arm: RetestArm,
    trigger,
    bars: list[FineBar],
    *,
    slippage_ticks: float,
) -> tuple[str, float]:
    broker = PaperBroker(
        starting_balance=1500.0,
        slippage_ticks=slippage_ticks,
        pessimistic_both_hit=True,
        runner_mode=True,
        runner_activation_r=1.0,
        runner_trail_r=0.5,
    )
    broker.execute_bracket(
        BracketOrder(
            instrument=arm.instrument,
            direction=arm.direction,
            entry=float(trigger.trigger_close),
            stop=arm.stop,
            target=arm.target,
            rr_ratio=2.0,
            strategy=arm.strategy,
            contracts=1,
        )
    )
    for bar in bars:
        if bar.timestamp <= trigger.triggered_at:
            continue
        fill = broker.resolve_position(NextBarOHLC(high=bar.high, low=bar.low))
        if fill is not None:
            return fill.result, float(fill.pnl_dollars or 0.0)
    return "OPEN", 0.0


def summarize(rows: list[dict]) -> dict:
    resolved = [r for r in rows if r["outcome"] in {"WIN", "LOSS", "BREAKEVEN"}]
    pnls = [r["pnl"] for r in resolved]
    equity = peak = drawdown = 0.0
    streak = max_streak = 0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        streak = streak + 1 if pnl < 0 else 0
        max_streak = max(max_streak, streak)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    return {
        "arms": len(rows),
        "triggered": sum(r["triggered"] for r in rows),
        "fill_rate": sum(r["triggered"] for r in rows) / len(rows) if rows else 0,
        "resolved": len(resolved),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(resolved) if resolved else 0,
        "net_pnl": round(sum(pnls), 2),
        "expectancy": round(statistics.fmean(pnls), 2) if pnls else 0,
        "profit_factor": (
            round(sum(wins) / abs(sum(losses)), 3) if losses else None
        ),
        "max_drawdown": round(drawdown, 2),
        "max_consecutive_losses": max_streak,
    }


def run(journal_dir: Path, fine_root: Path, ttl: int, distance: int, slippage: float):
    rows = []
    cache: dict[tuple[str, str], list[FineBar]] = {}
    for arm in load_arms(journal_dir):
        day = arm.armed_at.date().isoformat()
        bars = cache.setdefault(
            (arm.instrument, day),
            load_fine_bars(fine_root, arm.instrument, day),
        )
        trigger = simulate_arm(
            arm,
            bars,
            ttl_minutes=ttl,
            max_distance_ticks=distance,
            tick_size=0.25,
        )
        outcome, pnl = (
            resolve_runner(arm, trigger, bars, slippage_ticks=slippage)
            if trigger.status == "TRIGGERED"
            else ("NO_FILL", 0.0)
        )
        rows.append(
            {
                "instrument": arm.instrument,
                "armed_at": arm.armed_at.isoformat(),
                "triggered": trigger.status == "TRIGGERED",
                "minutes_to_fill": trigger.minutes_to_fill,
                "outcome": outcome,
                "pnl": pnl,
            }
        )
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journals", type=Path, required=True)
    parser.add_argument("--five-minute-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--slippage-ticks", type=float, default=1.0)
    args = parser.parse_args(argv)

    all_results = {}
    for ttl in (15, 20, 30):
        for distance in (1, 2, 4):
            key = f"ttl{ttl}_distance{distance}"
            rows = run(
                args.journals,
                args.five_minute_root,
                ttl,
                distance,
                args.slippage_ticks,
            )
            midpoint = len(rows) // 2
            all_results[key] = {
                "all": summarize(rows),
                "first_half": summarize(rows[:midpoint]),
                "second_half": summarize(rows[midpoint:]),
            }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(all_results, indent=2, sort_keys=True))
    print(json.dumps(all_results, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
