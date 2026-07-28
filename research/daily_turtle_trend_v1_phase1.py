"""Phase 1 feasibility screen for daily_turtle_trend_v1 (frozen Donchian/ATR system).

Research-only. No runtime wiring, no broker code, no deployment. Reads the
manually-exported TradingView continuous-contract CSVs in docs/ and reports
whether the frozen rule set is even worth carrying further — it does not
attempt to validate a tradeable system.

Frozen rules (do not change after seeing results):
  - Long entry:  close > prior 55-session high (Donchian, computed on HIGH)
  - Short entry: close < prior 55-session low  (Donchian, computed on LOW)
  - Exit:        opposite 20-session channel (close vs prior 20-session low/high)
  - Initial stop: entry +/- 2 * ATR(20), fixed at entry (not trailing)
  - No pyramiding, no averaging down, one unit per instrument
  - Signal read on day T's close; execution at day T+1's open (no lookahead)
  - Stops checked intraday against the day's own high/low; gap-through fills
    at the day's open, not at the theoretical stop price

Data provenance: TradingView manual CSV export, CME_MINI:ES1!/NQ1! continuous
contracts. The adjustment methodology (back-adjusted vs raw-spliced) was never
confirmed — see caveat in the printed report. Roll drag below is an explicit
ESTIMATE (one extra round-trip cost per quarterly boundary crossed while a
trade is open), not a measurement, precisely because that methodology is
unconfirmed.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime

ENTRY_N = 55
EXIT_N = 20
ATR_N = 20
STOP_MULT = 2.0

COMMISSION_PER_SIDE = 2.50   # USD, stated assumption (not given by operator)
SLIPPAGE_TICKS = 1.5         # midpoint of the instructed 1-2 tick range

INSTRUMENTS = {
    "ES1!": {"file": "docs/CME_MINI_ES1!, 1D (2).csv", "point_value": 50.0, "tick": 0.25},
    "NQ1!": {"file": "docs/CME_MINI_NQ1!, 1D (2).csv", "point_value": 20.0, "tick": 5.0 / 0.25 * 0.25},  # placeholder, fixed below
}
# NQ tick value is $5.00 per 0.25pt tick -> point value = 5.00/0.25 = $20/pt (matches above)
INSTRUMENTS["NQ1!"]["tick"] = 0.25

HOLDOUT_START = date(2025, 1, 1)
QUARTER_ROLL_MONTHS = {3, 6, 9, 12}


@dataclass
class Bar:
    d: date
    o: float
    h: float
    l: float
    c: float
    v: float


@dataclass
class Trade:
    instrument: str
    direction: int  # +1 long, -1 short
    entry_date: date
    entry_price: float
    exit_date: date
    exit_price: float
    exit_reason: str
    stop_price: float
    n_at_entry: float

    def points(self) -> float:
        return (self.exit_price - self.entry_price) * self.direction

    def pnl(self, point_value: float, roll_crossings: int) -> float:
        gross = self.points() * point_value
        cost = 2 * COMMISSION_PER_SIDE + roll_crossings * (2 * COMMISSION_PER_SIDE)
        return gross - cost

    def year(self) -> int:
        return self.exit_date.year


def load_bars(path: str) -> list[Bar]:
    rows = []
    with open(path, newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            d = datetime.strptime(row["time"], "%Y-%m-%d").date()
            rows.append(Bar(d, float(row["open"]), float(row["high"]),
                             float(row["low"]), float(row["close"]), float(row["Volume"])))
    rows.sort(key=lambda b: b.d)
    # drop the still-forming last session (incomplete bar at export time)
    today_incomplete = rows[-1].d
    rows = [b for b in rows if b.d < today_incomplete] if len(rows) > 1 else rows
    return rows


def wilder_atr(bars: list[Bar], n: int) -> list[float | None]:
    trs = [None] * len(bars)
    for i in range(1, len(bars)):
        h, l, pc = bars[i].h, bars[i].l, bars[i - 1].c
        trs[i] = max(h - l, abs(h - pc), abs(l - pc))
    atr = [None] * len(bars)
    if len(bars) <= n:
        return atr
    seed = sum(trs[1:n + 1]) / n
    atr[n] = seed
    for i in range(n + 1, len(bars)):
        atr[i] = (atr[i - 1] * (n - 1) + trs[i]) / n
    return atr


def rolling_prior_max(vals: list[float], n: int) -> list[float | None]:
    out = [None] * len(vals)
    for i in range(len(vals)):
        if i - n < 0:
            continue
        out[i] = max(vals[i - n:i])
    return out


def rolling_prior_min(vals: list[float], n: int) -> list[float | None]:
    out = [None] * len(vals)
    for i in range(len(vals)):
        if i - n < 0:
            continue
        out[i] = min(vals[i - n:i])
    return out


def count_quarter_rolls(d0: date, d1: date) -> int:
    count = 0
    y, m = d0.year, d0.month
    while (y, m) < (d1.year, d1.month):
        m += 1
        if m > 12:
            m = 1
            y += 1
        if m in QUARTER_ROLL_MONTHS:
            count += 1
    return count


def simulate(instrument: str, bars: list[Bar], entry_n=ENTRY_N, exit_n=EXIT_N, atr_n=ATR_N) -> list[Trade]:
    highs = [b.h for b in bars]
    lows = [b.l for b in bars]
    closes = [b.c for b in bars]

    hi_ch_entry = rolling_prior_max(highs, entry_n)
    lo_ch_entry = rolling_prior_min(lows, entry_n)
    lo_ch_exit = rolling_prior_min(lows, exit_n)
    hi_ch_exit = rolling_prior_max(highs, exit_n)
    atr = wilder_atr(bars, atr_n)

    trades: list[Trade] = []
    position = 0
    entry_price = entry_date = stop_price = n_at_entry = None

    start = max(entry_n, exit_n, atr_n) + 1
    for i in range(start, len(bars)):
        today = bars[i]

        if position != 0:
            if position == 1:
                if today.l <= stop_price:
                    fill = stop_price if today.o >= stop_price else today.o
                    fill -= SLIPPAGE_TICKS * INSTRUMENTS[instrument]["tick"]
                    trades.append(Trade(instrument, 1, entry_date, entry_price, today.d, fill,
                                         "STOP", stop_price, n_at_entry))
                    position = 0
            else:
                if today.h >= stop_price:
                    fill = stop_price if today.o <= stop_price else today.o
                    fill += SLIPPAGE_TICKS * INSTRUMENTS[instrument]["tick"]
                    trades.append(Trade(instrument, -1, entry_date, entry_price, today.d, fill,
                                         "STOP", stop_price, n_at_entry))
                    position = 0

        if position != 0 and lo_ch_exit[i - 1] is not None and hi_ch_exit[i - 1] is not None:
            yc = closes[i - 1]
            if position == 1 and yc < lo_ch_exit[i - 1]:
                fill = today.o - SLIPPAGE_TICKS * INSTRUMENTS[instrument]["tick"]
                trades.append(Trade(instrument, 1, entry_date, entry_price, today.d, fill,
                                     "CHANNEL", stop_price, n_at_entry))
                position = 0
            elif position == -1 and yc > hi_ch_exit[i - 1]:
                fill = today.o + SLIPPAGE_TICKS * INSTRUMENTS[instrument]["tick"]
                trades.append(Trade(instrument, -1, entry_date, entry_price, today.d, fill,
                                     "CHANNEL", stop_price, n_at_entry))
                position = 0

        if position == 0 and hi_ch_entry[i - 1] is not None and atr[i - 1] is not None:
            yc = closes[i - 1]
            if yc > hi_ch_entry[i - 1]:
                position = 1
                entry_price = today.o + SLIPPAGE_TICKS * INSTRUMENTS[instrument]["tick"]
                entry_date = today.d
                n_at_entry = atr[i - 1]
                stop_price = entry_price - STOP_MULT * n_at_entry
            elif yc < lo_ch_entry[i - 1]:
                position = -1
                entry_price = today.o - SLIPPAGE_TICKS * INSTRUMENTS[instrument]["tick"]
                entry_date = today.d
                n_at_entry = atr[i - 1]
                stop_price = entry_price + STOP_MULT * n_at_entry

    return trades


def report(instrument: str, trades: list[Trade], point_value: float):
    def pnl(t: Trade) -> float:
        rolls = count_quarter_rolls(t.entry_date, t.exit_date)
        return t.pnl(point_value, rolls)

    def summarize(subset: list[Trade], label: str):
        if not subset:
            print(f"  {label}: 0 trades")
            return
        pnls = [pnl(t) for t in subset]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_win = sum(wins)
        gross_loss = -sum(losses)
        net = sum(pnls)
        pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
        # equity curve + max drawdown
        eq = 0.0
        peak = 0.0
        maxdd = 0.0
        for p in pnls:
            eq += p
            peak = max(peak, eq)
            maxdd = min(maxdd, eq - peak)
        by_year: dict[int, float] = {}
        for t, p in zip(subset, pnls):
            by_year[t.year()] = by_year.get(t.year(), 0.0) + p
        best_year_share = (max(by_year.values()) / net * 100) if net > 0 and by_year else None
        print(f"  {label}: n={len(subset)} win%={100*len(wins)/len(subset):.1f} "
              f"net=${net:,.0f} PF={pf:.2f} maxDD=${maxdd:,.0f} "
              f"best_year_share={best_year_share:.1f}%" if best_year_share is not None
              else f"  {label}: n={len(subset)} win%={100*len(wins)/len(subset):.1f} net=${net:,.0f} PF={pf:.2f} maxDD=${maxdd:,.0f} (net<=0, no concentration %)")
        print(f"    by year: " + ", ".join(f"{y}:${v:,.0f}" for y, v in sorted(by_year.items())))

    print(f"=== {instrument} ===")
    dev = [t for t in trades if t.exit_date < HOLDOUT_START]
    hold = [t for t in trades if t.exit_date >= HOLDOUT_START]
    summarize(dev, "Development (< 2025)")
    summarize(hold, "Holdout (2025-2026)")
    summarize(trades, "Full period")
    print()


if __name__ == "__main__":
    all_trades = []
    for instrument, spec in INSTRUMENTS.items():
        bars = load_bars(spec["file"])
        print(f"{instrument}: {len(bars)} completed daily bars, {bars[0].d} -> {bars[-1].d}")
        trades = simulate(instrument, bars)
        report(instrument, trades, spec["point_value"])
        all_trades.extend((t, spec["point_value"]) for t in trades)

    print("=== COMBINED PORTFOLIO (ES1! + NQ1!, 1 contract each) ===")
    def pnl_combined(t: Trade, pv: float) -> float:
        rolls = count_quarter_rolls(t.entry_date, t.exit_date)
        return t.pnl(pv, rolls)

    dev = [(t, pv) for t, pv in all_trades if t.exit_date < HOLDOUT_START]
    hold = [(t, pv) for t, pv in all_trades if t.exit_date >= HOLDOUT_START]
    for label, subset in [("Development (< 2025)", dev), ("Holdout (2025-2026)", hold), ("Full period", all_trades)]:
        if not subset:
            print(f"  {label}: 0 trades")
            continue
        pnls = [pnl_combined(t, pv) for t, pv in subset]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        net = sum(pnls)
        gross_win = sum(wins)
        gross_loss = -sum(losses)
        pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
        eq = 0.0; peak = 0.0; maxdd = 0.0
        for p in pnls:
            eq += p; peak = max(peak, eq); maxdd = min(maxdd, eq - peak)
        by_year: dict[int, float] = {}
        for (t, pv), p in zip(subset, pnls):
            by_year[t.year()] = by_year.get(t.year(), 0.0) + p
        print(f"  {label}: n={len(subset)} win%={100*len(wins)/len(subset):.1f} net=${net:,.0f} PF={pf:.2f} maxDD=${maxdd:,.0f}")
        print(f"    by year: " + ", ".join(f"{y}:${v:,.0f}" for y, v in sorted(by_year.items())))
