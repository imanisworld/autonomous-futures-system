#!/usr/bin/env python3
"""Stop-rule sweep — does ANY bar-close stop rule beat the static bracket?

Replays each trade forward over the 15-MINUTE candles (the resolution the live
bot + broker bracket can actually act on — intra-bar moves are invisible to it)
and applies several candidate exit rules through ONE consistent simulator, so
the cross-rule comparison is apples-to-apples even if the absolute net differs
slightly from the production replay.

Rules:
  static        — fixed stop + fixed target (baseline)
  be_0.5R/1R/1.5R — move stop to entry once favourable excursion (prior bars)
                    reaches the threshold; keep target
  trail_1R      — once +1R reached, trail stop 1R behind the running favourable
                  high (bar close); keep target
  run_trail_1R  — like trail_1R but DROP the target (let winners run on the trail)
  run_trail_0.5R— runner with a tighter 0.5R trail

Conventions (conservative, no intra-bar look-ahead): resolution starts on the bar
AFTER entry; stop moves are decided from bars STRICTLY BEFORE the current one; a
bar that hits both target and the active stop is booked as the stop (worst case).
Exit P&L uses the instrument point value, 1 contract.

VALIDATION (read before trusting numbers):
  - Static-baseline WIN RATES match the production replay closely (MES ~52% vs
    50.7%, MNQ ~50% vs 50%), so per-contract EXITS are faithful. Absolute net is
    ~3x below the replay because the replay sized >1 contract; this tool is 1c.
  - BUT the BE@1R result here (~+$300/+$120) is more optimistic than the FAITHFUL
    paper_broker A/B (BE@1R ≈ flat, +$4/+$26). So this tool OVERSTATES stop-rule
    benefit. Treat all Δ-vs-static as DIRECTIONAL ONLY — a screen for which rules
    are worth testing in the real sim, not a magnitude. Confirm winners in
    paper_broker before believing any of it.

Usage: python3 scripts/stop_rule_sweep.py --trades trades.json --candles data/replay_polygon
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TICK_SIZE = {"MNQ": 0.25, "MES": 0.25}
TICK_VALUE = {"MNQ": 0.50, "MES": 1.25}


def _pt_value(inst):
    return TICK_VALUE.get(inst, 1.0) / TICK_SIZE.get(inst, 0.25)


def _parse(ts):
    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _load_candles(candles_dir, inst):
    out = []
    for f in sorted(Path(candles_dir, inst).glob(f"{inst}_*.jsonl")):
        for line in f.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            out.append((_parse(r["timestamp"]), r["high"], r["low"], r["close"]))
    out.sort(key=lambda x: x[0])
    return out


def _simulate(trade, candles, rule, max_hold_min=480):
    inst = trade["instrument"]
    is_long = (trade["direction"] or "").upper() == "LONG"
    entry, stop0, target = float(trade["entry"]), float(trade["stop"]), float(trade["target"])
    R = abs(entry - stop0)
    if R <= 0:
        return None
    start = _parse(trade["entry_ts"])
    # Resolution starts on the bar AFTER entry (the entry bar's pre-entry range
    # must not trigger a phantom same-bar exit), matching how the live bot resolves.
    window = [c for c in candles if start < c[0] <= start + timedelta(minutes=max_hold_min)]
    if len(window) < 2:
        return None

    use_target = not rule.startswith("run_")
    fav_high = entry            # running favourable extreme (price terms)
    active_stop = stop0
    pt = _pt_value(inst)

    def favorable(price_extreme):
        return (price_extreme - entry) if is_long else (entry - price_extreme)

    for _, hi, lo, close in window:
        fav_now = hi if is_long else lo            # this bar's favourable extreme
        # --- decide active stop from PRIOR favourable (no intra-bar look-ahead) ---
        prior_fav = favorable(fav_high)
        if rule.startswith("be_"):
            thr = float(rule[3:-1]) * R
            if prior_fav >= thr:
                active_stop = entry
        elif rule in ("trail_1R", "run_trail_1R"):
            if prior_fav >= 1.0 * R:
                active_stop = (fav_high - R) if is_long else (fav_high + R)
        elif rule == "run_trail_0.5R":
            if prior_fav >= 1.0 * R:
                active_stop = (fav_high - 0.5 * R) if is_long else (fav_high + 0.5 * R)
        # --- check exits this bar (stop-first on straddle) ---
        hit_stop = (lo <= active_stop) if is_long else (hi >= active_stop)
        hit_tgt = use_target and ((hi >= target) if is_long else (lo <= target))
        if hit_stop:
            diff = (active_stop - entry) if is_long else (entry - active_stop)
            return diff * pt
        if hit_tgt:
            diff = (target - entry) if is_long else (entry - target)
            return diff * pt
        # update running favourable extreme AFTER this bar
        fav_high = max(fav_high, fav_now) if is_long else min(fav_high, fav_now)

    # timeout → mark out at last close
    last_close = window[-1][3]
    diff = (last_close - entry) if is_long else (entry - last_close)
    return diff * pt


RULES = ["static", "be_0.5R", "be_1R", "be_1.5R", "trail_1R", "run_trail_1R", "run_trail_0.5R"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True)
    ap.add_argument("--candles", default="data/replay_polygon")
    ap.add_argument("--max-hold-min", type=int, default=480)
    args = ap.parse_args()

    trades = json.loads(Path(args.trades).read_text())
    by_inst = defaultdict(list)
    for t in trades:
        if t.get("instrument") and t.get("entry_ts") and t.get("entry") is not None:
            by_inst[t["instrument"]].append(t)

    print(f"{'inst':5} {'rule':14} | {'n':>4} {'win%':>6} {'net$':>10} {'exp/trade':>9} {'Δ vs static':>12}")
    print("-" * 72)
    for inst in sorted(by_inst):
        candles = _load_candles(args.candles, inst)
        base_net = None
        for rule in RULES:
            pnls = [p for t in by_inst[inst] if (p := _simulate(t, candles, rule, args.max_hold_min)) is not None]
            n = len(pnls)
            net = sum(pnls)
            wins = sum(1 for p in pnls if p > 0)
            wr = 100 * wins / n if n else 0
            exp = net / n if n else 0
            if rule == "static":
                base_net = net
            delta = "" if rule == "static" else f"{net - base_net:+.0f}"
            print(f"{inst:5} {rule:14} | {n:>4} {wr:>6.1f} {net:>10.0f} {exp:>9.1f} {delta:>12}")
        print("-" * 72)


if __name__ == "__main__":
    main()
