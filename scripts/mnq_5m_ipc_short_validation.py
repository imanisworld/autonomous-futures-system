#!/usr/bin/env python3
"""Narrow validation pass: short-only impulse-pullback-continuation @ R=1.5.

RESULT: REJECTED -- see docs/mnq-5m-ipc-short-validation-2026-07-13.md.
Removing the best 10 trades (0.6% of the 1,615-trade sample) flips the
result from +$0.815/trade to -$2.157/trade; median trade expectancy is
-$29.98. This script produces the subgroup/outlier-sensitivity breakdown
that disqualifies the short-only, R=1.5 sub-result from
research/mnq_5m_impulse_pullback_continuation.py under the operator's
explicit build gate.

Reproduce: python3 scripts/mnq_5m_ipc_short_validation.py
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from research.mnq_5m_impulse_pullback_continuation import detect_candidates  # noqa: E402
from execution.broker_interface import BracketOrder  # noqa: E402
from execution.paper_broker import NextBarOHLC, PaperBroker  # noqa: E402

FINE_ROOT = REPO / "data/replay_polygon_5m/MNQ"
TICK = 0.25
COMMISSION_RT = 1.48
LOOKBACK = 20
R_MULTIPLE = 1.5


def load_day(p):
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def resolve(cand, bars_after, slip_ticks):
    slip = TICK * slip_ticks
    entry = cand["entry"] - slip  # SHORT: adverse slip is downward fill (worse price)
    broker = PaperBroker(starting_balance=1500.0, slippage_ticks=0.0, pessimistic_both_hit=True, runner_mode=False)
    broker.execute_bracket(BracketOrder(
        instrument="MNQ", direction="SHORT", entry=entry, stop=cand["stop"],
        target=cand["target"], rr_ratio=cand["rr"], strategy="ipc_short", contracts=1,
    ))
    for b in bars_after:
        out = broker.resolve_position(NextBarOHLC(high=float(b["high"]), low=float(b["low"])))
        if out is not None:
            return out.result, float(out.pnl_dollars or 0.0) - COMMISSION_RT, b.get("timestamp")
    return "OPEN", 0.0, None


def regime_at(bar):
    td = (bar.get("trend_direction") or "").upper()
    ts = (bar.get("trend_strength") or "").upper()
    mc = (bar.get("market_condition") or "").upper()
    return f"{mc}/{td}/{ts}"


def run():
    files = sorted(FINE_ROOT.glob("MNQ_*.jsonl"))
    trades = []
    prior_tail = []

    for path in files:
        day_bars = load_day(path)
        if not day_bars:
            continue
        day_bars.sort(key=lambda r: r["timestamp"])
        bars = (prior_tail + day_bars) if prior_tail else day_bars
        offset = len(prior_tail)
        since = {"long": None, "short": None}

        for i in range(offset, len(bars)):
            cur = bars[i]
            window = bars[max(0, i - LOOKBACK):i]
            for d in ("long", "short"):
                if since[d] is not None:
                    since[d] += 1
            cands = detect_candidates(
                window=window, current_bar=cur, session=cur.get("session"),
                r_multiple=R_MULTIPLE, bars_since_last_trigger=since,
            )
            for c in cands:
                if c.get("decision") != "ACCEPTED" or c["direction"] != "short":
                    continue
                since["short"] = 0
                bars_after = bars[i + 1:i + 1 + 500]
                results = {}
                for slip in (2.0, 3.0, 4.0):
                    result, pnl, exit_ts = resolve(c, bars_after, slip)
                    results[slip] = (result, pnl, exit_ts)
                if results[2.0][0] == "OPEN":
                    continue
                ts = str(cur["timestamp"])
                trades.append({
                    "entry_ts": ts, "month": ts[:7], "year": ts[:4],
                    "session": c["session"], "regime": regime_at(cur),
                    "risk_points": c["risk_points"], "pullback_bars": c["pullback_bars"],
                    "pnl_2t": results[2.0][1], "outcome_2t": results[2.0][0],
                    "pnl_3t": results[3.0][1], "pnl_4t": results[4.0][1],
                    "exit_ts": results[2.0][2],
                    "entry": c["entry"], "stop": c["stop"], "target": c["target"],
                    "file": path.name,
                })
        prior_tail = day_bars[-LOOKBACK:]
    return trades


def stats(pnls):
    if not pnls:
        return {"n": 0}
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    running = peak = max_dd = 0.0
    streak = max_streak = 0
    for p in pnls:
        running += p
        peak = max(peak, running)
        max_dd = min(max_dd, running - peak)
        if p < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    return {
        "n": len(pnls), "win_rate": round(len(wins) / len(pnls), 3),
        "net_pnl": round(sum(pnls), 2), "mean_exp": round(statistics.fmean(pnls), 3),
        "median_exp": round(statistics.median(pnls), 3),
        "pf": round(sum(wins) / abs(sum(losses)), 3) if wins and losses else None,
        "max_dd": round(max_dd, 2), "longest_losing_streak": max_streak,
    }


if __name__ == "__main__":
    trades = run()
    print(f"TOTAL TRADES: {len(trades)}\n")

    print("=== OVERALL (2-tick) ===")
    print(stats([t["pnl_2t"] for t in trades]))
    print("=== OVERALL (3-tick) ===")
    print(stats([t["pnl_3t"] for t in trades]))
    print("=== OVERALL (4-tick) ===")
    print(stats([t["pnl_4t"] for t in trades]))

    print("\n=== BY SESSION (2-tick) ===")
    by_session = defaultdict(list)
    for t in trades:
        by_session[t["session"]].append(t["pnl_2t"])
    for k, v in sorted(by_session.items()):
        print(k, stats(v))

    print("\n=== BY MONTH (2-tick) ===")
    by_month = defaultdict(list)
    for t in trades:
        by_month[t["month"]].append(t["pnl_2t"])
    for k, v in sorted(by_month.items()):
        print(k, stats(v))

    print("\n=== BY YEAR (2-tick) ===")
    by_year = defaultdict(list)
    for t in trades:
        by_year[t["year"]].append(t["pnl_2t"])
    for k, v in sorted(by_year.items()):
        print(k, stats(v))

    print("\n=== BY REGIME (2-tick) ===")
    by_regime = defaultdict(list)
    for t in trades:
        by_regime[t["regime"]].append(t["pnl_2t"])
    for k, v in sorted(by_regime.items(), key=lambda kv: -len(kv[1])):
        print(k, stats(v))

    print("\n=== QUARTILES BY TIME (2-tick) ===")
    n = len(trades)
    for qi in range(4):
        lo, hi = qi * n // 4, (qi + 1) * n // 4
        print(f"Q{qi+1}", stats([t["pnl_2t"] for t in trades[lo:hi]]))

    print("\n=== OUTLIER SENSITIVITY (2-tick) ===")
    sorted_by_pnl = sorted(trades, key=lambda t: -t["pnl_2t"])
    all_pnls = [t["pnl_2t"] for t in trades]
    best5_ids = {id(t) for t in sorted_by_pnl[:5]}
    best10_ids = {id(t) for t in sorted_by_pnl[:10]}
    without5 = [t["pnl_2t"] for t in trades if id(t) not in best5_ids]
    without10 = [t["pnl_2t"] for t in trades if id(t) not in best10_ids]
    print("all:", stats(all_pnls))
    print("without best 5:", stats(without5))
    print("without best 10:", stats(without10))
    print("top 10 trades:", [round(t["pnl_2t"], 2) for t in sorted_by_pnl[:10]])

    print("\n=== RISK/STOP-DISTANCE DISTRIBUTION ===")
    risks = [t["risk_points"] for t in trades]
    print("risk_points: min", min(risks), "median", statistics.median(risks), "max", max(risks),
          "mean", round(statistics.fmean(risks), 2))

    out_path = REPO / "scripts/mnq_5m_ipc_short_validation_trades.json"
    out_path.write_text(json.dumps(trades, indent=2, default=str))
    print(f"\nWrote {len(trades)} trades to {out_path}")
