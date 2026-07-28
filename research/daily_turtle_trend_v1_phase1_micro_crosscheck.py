"""Micro-era cross-check for daily_turtle_trend_v1 Phase 1.

NOT a fresh validation attempt. The ES1!/NQ1! Phase 1 screen already returned
BROKEN (single-year profit concentration). This script only asks whether that
same finding replicates when the identical frozen rules run against the
MES1!/MNQ1! data feed (a different TradingView continuous-contract series,
same underlying index) over the 2019-2026 window both feeds cover. No
parameter changes, no rule changes, no new verdict tier introduced.
"""
from __future__ import annotations

import sys
sys.path.insert(0, "research")
from daily_turtle_trend_v1_phase1 import (
    load_bars, simulate, count_quarter_rolls, HOLDOUT_START,
)

MICRO_INSTRUMENTS = {
    "MES1!": {"file": "docs/CME_MINI_MES1!, 1D (2).csv", "point_value": 5.0, "tick": 0.25},
    "MNQ1!": {"file": "docs/CME_MINI_MNQ1!, 1D.csv", "point_value": 2.0, "tick": 0.25},
}
# Micro commission assumption, stated explicitly (lower than full-size $2.50/side):
MICRO_COMMISSION_PER_SIDE = 1.25

import daily_turtle_trend_v1_phase1 as base
base.COMMISSION_PER_SIDE = MICRO_COMMISSION_PER_SIDE
base.INSTRUMENTS.update(MICRO_INSTRUMENTS)


def summarize(label, trades, point_value):
    if not trades:
        print(f"  {label}: 0 trades")
        return
    pnls = [t.pnl(point_value, count_quarter_rolls(t.entry_date, t.exit_date)) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    net = sum(pnls)
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    eq = 0.0; peak = 0.0; maxdd = 0.0
    for p in pnls:
        eq += p; peak = max(peak, eq); maxdd = min(maxdd, eq - peak)
    by_year = {}
    for t, p in zip(trades, pnls):
        by_year[t.year()] = by_year.get(t.year(), 0.0) + p
    best_year = max(by_year.values()) if by_year else 0
    share = (best_year / net * 100) if net > 0 else None
    print(f"  {label}: n={len(trades)} win%={100*len(wins)/len(trades):.1f} net=${net:,.0f} "
          f"PF={pf:.2f} maxDD=${maxdd:,.0f}" + (f" best_year_share={share:.1f}%" if share else " (net<=0)"))
    print(f"    by year: " + ", ".join(f"{y}:${v:,.0f}" for y, v in sorted(by_year.items())))


if __name__ == "__main__":
    for inst, spec in MICRO_INSTRUMENTS.items():
        bars = load_bars(spec["file"])
        print(f"{inst}: {len(bars)} completed daily bars, {bars[0].d} -> {bars[-1].d}")
        trades = simulate(inst, bars)
        print(f"=== {inst} (micro, cost model: ${MICRO_COMMISSION_PER_SIDE}/side, 1.5 tick slippage) ===")
        dev = [t for t in trades if t.exit_date < HOLDOUT_START]
        hold = [t for t in trades if t.exit_date >= HOLDOUT_START]
        summarize("2019-2024", dev, spec["point_value"])
        summarize("2025-2026 holdout", hold, spec["point_value"])
        summarize("Full period", trades, spec["point_value"])
        print()
