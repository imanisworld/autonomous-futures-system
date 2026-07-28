# daily_turtle_trend_v1 — closed research record

**Verdict: BROKEN — single-year profit concentration. Strategy closed, no further reruns planned.**

No runtime code, broker code, or deployment was added or touched by this research. Both scripts
below are standalone, read data from local TradingView CSV exports (not committed — see Data
provenance), and produce no side effects outside their own stdout.

## Frozen rule set (unchanged across every run in this record)

- Long entry: close > prior 55-session high (Donchian on HIGH, strictly prior — no lookahead)
- Short entry: close < prior 55-session low
- Exit: opposite 20-session channel (close vs. prior 20-session low/high)
- Initial stop: entry ± 2 × ATR(20), fixed at entry (Wilder-smoothed ATR), not trailing
- No pyramiding, no averaging down — one unit per instrument
- Signal read on day T's close; execution at day T+1's open (causal, no same-bar fills)
- Stops checked intraday against the day's own high/low; a gap-through fills at the day's open,
  not the theoretical stop price

## Cost and roll assumptions (stated explicitly — not specified by the operator)

- Full-size (ES/NQ): $2.50/side commission, 1.5 ticks adverse slippage per fill
- Micro (MES/MNQ): $1.25/side commission, 1.5 ticks adverse slippage per fill
- Point values used: ES $50/pt, NQ $20/pt, MES $5/pt, MNQ $2/pt (CME contract specs)
- Roll drag: an *estimated* extra round-trip-equivalent cost per quarterly boundary crossed while
  a trade is open — an approximation, not a measurement
- **Unconfirmed caveat:** the TradingView continuous-contract adjustment method (back-adjusted vs.
  raw-spliced) was never confirmed in this session. All dollar figures below carry this caveat;
  it does not change the concentration finding, which is a calendar-year distribution issue, not a
  price-level issue.

## Data provenance

Manually exported TradingView continuous-contract daily CSVs (not committed to this repo —
see Data note below):
- ES1! (CME_MINI:ES1!): 4,561 completed daily bars, 2008-06-25 → 2026-07-28
- NQ1! (CME_MINI:NQ1!): 5,105 completed daily bars, 2006-05-05 → 2026-07-28
- MES1! (CME_MINI:MES1!): 1,821 completed daily bars, 2019-05-06 → 2026-07-28 (launch-day
  placeholder bar 2019-05-03 dropped)
- MNQ1! (CME_MINI:MNQ1!): 1,820 completed daily bars, 2019-05-06 → 2026-07-28

Chronological holdout for all runs: 2025-01-01 onward held out from development.

**Data note:** the raw CSV exports are not included in this commit. TradingView market data is not
redistributable, and this is a public repository. Only the analysis scripts and this results
summary are committed; the scripts expect the same files locally under `docs/` (filenames in the
scripts) to reproduce the run.

## Results — ES1!/NQ1! (full-size, long-history feasibility screen)

| | Net P&L | PF | Max DD | Trades | Best-year share of net |
|---|---|---|---|---|---|
| ES1! development (2008-2024) | $4,738 | 1.04 | -$31,934 | 66 | **272%** |
| ES1! holdout (2025-2026) | $14,389 | 1.76 | -$18,848 | 5 | 177% |
| NQ1! development (2006-2024) | $33,425 | 1.16 | -$46,125 | 72 | **116%** |
| NQ1! holdout (2025-2026) | $52,127 | 2.26 | -$35,573 | 6 | 97% |
| Combined development | $38,163 | 1.12 | -$46,125 | 138 | — |
| Combined holdout | $66,516 | 2.11 | -$35,573 | 11 | — |

Parameter-neighborhood grid (entry ∈ {50,55,60} × exit ∈ {18,20,22}, ATR fixed at 20): all 9 cells
stay positive (combined PF 1.13–1.45) — not a knife-edge overfit to one parameter — but the frozen
point (55/20) is the *worst* cell in the grid.

## Results — MES1!/MNQ1! (micro, executable-instrument cross-check)

| | Net P&L | PF | Max DD | Trades | Best-year share of net |
|---|---|---|---|---|---|
| MES1! dev (2019-2024) | $1,516 | 1.25 | -$3,213 | 19 | **133%** |
| MES1! holdout (2025-2026) | $1,425 | 1.75 | -$1,892 | 5 | 178% |
| MNQ1! dev (2019-2024) | $4,826 | 1.43 | -$3,572 | 20 | **80%** |
| MNQ1! holdout (2025-2026) | $5,177 | 2.25 | -$3,553 | 6 | 97% |

## The disqualifying finding: single-year profit concentration

- **ES1! development:** remove 2024 alone ($12,877) and the other 16 years net to roughly **-$8,100
  combined**.
- **NQ1! development:** remove 2020 alone ($38,677 — the COVID crash/V-recovery, a singular,
  non-repeatable event) and the other 18 years net to roughly **-$5,250 combined**.
- **MNQ1! (micro) development:** the same calendar year, 2020, is again the dominant contributor
  ($3,853 of $4,826 dev net) — an independent data export corroborating the full-size finding
  rather than a full-size-specific artifact.
- Max drawdown exceeds total net profit in both full-size instruments' development windows (ES:
  -$31,934 DD vs. $4,738 net; NQ: -$46,125 DD vs. $33,425 net) — an account trading this system
  would at some point have been underwater by more than its entire lifetime gain.

Trade counts are thin throughout (19–78 trades per instrument over 6–19 years) — yearly results are
a handful-of-trades phenomenon, not a populated statistical sample, independent of the concentration
finding.

## Verdict

**`daily_turtle_trend_v1` — BROKEN (single-year profit concentration).**

This closes the strategy. It does not conclude that daily trend-following is inherently
unworkable — only that this specific frozen rule set, on this two-instrument correlated
equity-index universe, fails on its own pre-registered stop condition. No parameter retuning, no
additional data purchase, and no further reruns are planned. Any future trend-following research on
this project would need a genuinely diversified multi-asset-class universe (equity index, rates,
FX, commodities) with volatility-based cross-instrument sizing — a two-index MES/MNQ universe does
not provide the diversification that published trend-following results rely on.
