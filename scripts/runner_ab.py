#!/usr/bin/env python3
"""Faithful runner A/B — drives the REAL paper_broker over fixed trades.

Unlike scripts/stop_rule_sweep.py (a standalone approximation that overstates),
this replays each trade through the production PaperBroker exit engine bar-by-bar
over 15-min candles, toggling ONLY runner_mode. Same trades, same candles, same
broker → the Δ is the honest effect of the runner exit. Static baseline should
reproduce the stop-sweep's static column (both 1-contract, pessimistic straddle).

Usage: python3 scripts/runner_ab.py --trades trades.json --candles data/replay_polygon
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from execution.broker_interface import BracketOrder
from execution.paper_broker import NextBarOHLC, PaperBroker, TICK_SIZE, TICK_VALUE


def _parse(ts):
    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _load_candles(candles_dir, inst):
    out = []
    for f in sorted(Path(candles_dir, inst).glob(f"{inst}_*.jsonl")):
        for line in f.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                out.append((_parse(r["timestamp"]), r["high"], r["low"], r["close"]))
    out.sort(key=lambda x: x[0])
    return out


def _run_trade(trade, candles, *, runner, activation_r, trail_r, max_hold_min=480):
    inst = trade["instrument"]
    entry, stop, target = float(trade["entry"]), float(trade["stop"]), float(trade["target"])
    if abs(entry - stop) <= 0:
        return None
    start = _parse(trade["entry_ts"])
    window = [c for c in candles if start < c[0] <= start + timedelta(minutes=max_hold_min)]
    if len(window) < 2:
        return None

    broker = PaperBroker(starting_balance=100_000.0, pessimistic_both_hit=True,
                         breakeven_at_1r=False, runner_mode=runner,
                         runner_activation_r=activation_r, runner_trail_r=trail_r)
    broker.execute_bracket(BracketOrder(
        instrument=inst, direction=trade["direction"], entry=entry, stop=stop,
        target=target, rr_ratio=0.0, strategy=trade.get("strategy", ""), notes="",
        contracts=1))
    for _, hi, lo, close in window:
        fill = broker.resolve_position(NextBarOHLC(high=hi, low=lo))
        if fill is not None:
            return fill.pnl_dollars
    # timeout → mark out at last close, 1 contract
    pt = TICK_VALUE.get(inst, 1.0) / TICK_SIZE.get(inst, 0.25)
    last_close = window[-1][3]
    diff = (last_close - entry) if trade["direction"] == "LONG" else (entry - last_close)
    return diff * pt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True)
    ap.add_argument("--candles", default="data/replay_polygon")
    ap.add_argument("--activation-r", type=float, default=1.0)
    ap.add_argument("--trail-r", type=float, default=0.5)
    args = ap.parse_args()

    trades = json.loads(Path(args.trades).read_text())
    by_inst = defaultdict(list)
    for t in trades:
        if t.get("instrument") and t.get("entry_ts") and t.get("entry") is not None:
            by_inst[t["instrument"]].append(t)

    print(f"runner: activation={args.activation_r}R trail={args.trail_r}R · 1 contract\n")
    print(f"{'inst':5} {'mode':8} | {'n':>4} {'win%':>6} {'net$':>9} {'exp/trade':>9} {'Δ vs static':>12}")
    print("-" * 64)
    for inst in sorted(by_inst):
        candles = _load_candles(args.candles, inst)
        base = None
        for mode, runner in (("static", False), ("runner", True)):
            pnls = [p for t in by_inst[inst]
                    if (p := _run_trade(t, candles, runner=runner,
                                        activation_r=args.activation_r, trail_r=args.trail_r)) is not None]
            n = len(pnls); net = sum(pnls); wins = sum(1 for p in pnls if p > 0)
            wr = 100 * wins / n if n else 0; exp = net / n if n else 0
            if mode == "static":
                base = net
            delta = "" if mode == "static" else f"{net - base:+.0f}"
            print(f"{inst:5} {mode:8} | {n:>4} {wr:>6.1f} {net:>9.0f} {exp:>9.1f} {delta:>12}")
        print("-" * 64)


if __name__ == "__main__":
    main()
