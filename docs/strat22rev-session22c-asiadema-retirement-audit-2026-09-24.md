# Strat 2-2 Reversal, Session 2-2 Continuation, Asia D+EMA — Retirement Audit — 2026-09-24

## Verdict

| Lane | Classification | Primary defect |
|---|---|---|
| MNQ Strat 2-2 reversal (`MNQ_STRAT_22_REVERSAL_MODE`) | **RETIRE** | No edge out of sample or forward; no better than random direction |
| MNQ Session 2-2 continuation H6/H7 (`SESSION_22C_PAPER_MODE`) | **RETIRE** | Selection-year edge; loses on the untouched months before it under every fill model |
| MNQ Asia D+EMA cohort (`ASIA_D_EMA_PAPER_MODE`) | **RETIRE** | Independent-year PF 1.07, not significant; second half negative |
| New variants | **None worth preregistering** | Every declared variant failed its out-of-sample check |

All three are **retired as promotion candidates** (operator classification, 2026-09-24),
based on separate research-only audits run at release `ea928c6`. Companion to
`docs/daily22-mes122-retirement-audit-2026-09-23.md`.

**AUDIT / DOCS ONLY.** No runtime, strategy, risk, broker, collector, TradingView or
deployment change was made. All three paper collectors are still running unchanged.
Stopping or downgrading any of them is a separate runtime change that needs its own
explicit operator GO.

**Registered preregs are not modified.**
`docs/prereg-mnq-portfolio-ex-asia-forward-2026-09-23.md` keeps its frozen family set
(including `DAILY_22_COMPLETED_CLOSE`) and its H2 comparison against `ASIA_D_EMA`. Its §6
forbids removing families after registration. The Session 2-2 H6/H7 one-look in
`docs/prereg-mnq-volume-label-and-sunday-reopen-2026-09-21.md` is also unchanged.

Each audit checked the three failure modes found on 2026-09-23: raw front-month corpora
that aren't roll-adjusted, headline inflation from the one-position rule, and entries
booked at a price the decision could not reach. **None of those explains these three
failures.** In all three lanes the entry is attainable at decision time. The signals
simply don't hold up on independent data.

## Strat 2-2 reversal

Honest fill (next-bar open +1 tick, 1-tick trade-through for targets), roll seams
excluded, one position at a time, $1.48 round-turn commission:

| Period | Trades | W/L | PF | Expectancy | Net | Max DD |
|---|---|---|---|---|---|---|
| In-sample 2024-07 → 2025-06 | 1,607 | 643/964 | 1.075 | +$3.65 | +$5,870 | −$5,700 |
| Out-of-sample 2025-07 → 2026-06 | 1,783 | 672/1,111 | **0.921** | −$4.17 | −$7,436 | −$10,767 |
| Forward replay 2026-07-16 → 09-22 | 313 | — | 0.925 | −$4.85 | −$1,518 | −$2,957 |
| Live paper ledger, booked | 294 | 122/172 | 0.97 | −$2.09 | −$613 | −$2,840 |

- **Random-direction control** (200 matched seeds on the out-of-sample population): the
  null median is PF 0.925 and p95 is 1.004. The real 0.921 sits at the 46th percentile.
  The in-sample result is at the 77th percentile, also below p95. This repeats the
  tranche-2 sign flip (`docs/strat-shadow-tranche2-2026-07-16.md`).
- **Live matches replay.** 711 of 714 live candidates matched, and 284 of 285 matched
  trades had the same win/loss.
- **No split holds.** Long and short are both negative out of sample and forward. No
  session is positive in all three periods, and out-of-sample months split 5 positive /
  7 negative.
- **The result isn't driven by outliers.** The top 3 trades are 2.9% of out-of-sample
  gross wins. Always-free (no one-position limit) is PF 0.949.
- **Variants** (declared before scoring):

  | Variant | IS PF | OOS PF | Fwd PF | Result |
  |---|---|---|---|---|
  | V1 FTFC (higher timeframes lined up with the trade) | 1.103 | 1.185 | 1.05 | fails: OOS is only at the 78th percentile of its own null |
  | V2 in-force close only | 1.179 | 0.909 | 0.877 | fails |
  | V3 V1 + V2 | 1.068 | 1.038 | 0.947 | fails |
  | V4 resting stop at the trigger | 0.862 | 0.801 | 0.74 | fails |

  V1's gain comes from the bracket, not the 2-2 reversal signal: randomizing direction
  inside the FTFC filter earns about the same.

## Session 2-2 continuation (H6 Asia / H7 Sunday)

The 09-21 grid numbers reproduce exactly on their own corpus (H6 n=1,049, PF 1.25; H7
n=139, PF 1.75). These were the top 2 of 1,253 grid cells, and the permutation test did
not account for that selection. On the 9.7 months immediately before the corpus
(2024-10 → 2025-07, same data source, never used for selection), both cells lose under
every fill model:

| Oct 2024 – Jul 2025 | H6 Asia | H7 Sunday |
|---|---|---|
| Grid booking (as researched) | PF 0.89, −$1,117 | PF 0.84, −$405 |
| As built (lane fill) | PF 0.76, −$3,582 | PF 0.66, −$792 |

As built (Asia 1.5R + Sunday 1.0R, lane fill, roll-corrected, net of $2.96 commission):

| Period | Trades | W/L | PF | Expectancy | Net | Max DD |
|---|---|---|---|---|---|---|
| Selection year | 657 | 292/365 | 1.25 | +$9.02 | +$5,927 | $1,704 |
| Earlier independent months | 512 | 204/308 | **0.75** | −$8.54 | −$4,375 | $4,638 |
| 2026-07-24 → 09-20 | 113 | 48/65 | 1.32 | +$14.29 | +$1,615 | $1,208 |
| Forward on box (Asia only) | 6 | 2/4 | 0.87 gross | −$6.09 | −$36.51 | $143 |

- **Outside the selection year, Sunday totals 74 trades and −$1,374.**
- **The lane differs from its prereg in two places.**
  - It admits a trend match at any strength, while the prereg requires the full EMA
    stack. 3 of the 6 forward trades fail the full stack.
  - It measures the target from the trigger price, not the fill. On 09-23 02:15 the
    target sat 5.4R from the actual fill.

  Neither difference rescues the strategy. The registered spec also loses on the earlier
  months (PF 0.88 grid booking, 0.83 with honest resting fills).
- **The gates don't help.** On the earlier months every Asia filter made results worse
  than no filter (no filter PF 0.96, as built 0.76).
- **Variants** (declared before scoring; the rule needed PF ≥ 1.15 and a profit on the
  earlier months): V1 target from actual fill, PF 0.83; V2 registered EMA-stack filter,
  PF 0.79; V3 resting order at the trigger, PF 0.83. **All fail.**

## Asia D+EMA

The lane replayed exactly (same candidate helpers, fill model and one-position rule) on
2025-07-24 → 2026-07-12, which the lane's design did not use. #915 and the 09-21 grid
had already looked at this period in aggregate. Honest fill, $1.48 commission, the 4
roll nights excluded:

| Version | Trades | W/L | PF | Expectancy | Net | Max DD |
|---|---|---|---|---|---|---|
| Booked (lane fill, raw, no commission) | 668 | 235/433 | 1.096 | +$4.50 | +$3,005 | $2,789 |
| Honest + commission + roll nights removed | 654 | 231/423 | **1.07** | +$3.25 | +$2,126 | $3,427 |
| Forward paper on box, booked | 15 | 8/7 | 1.90 | +$31 | +$468 | $227 |

- **The result isn't significant.** PF 1.07 sits at the 90th percentile of a matched
  random-direction null (500 seeds: median 0.95, p95 1.11), so p ≈ 0.10. Detecting this
  expectancy would take about 6,200 trades.
- **It is unstable.** Jul–Jan: 352 trades, PF 1.42. Feb–Jul 12: 302 trades, PF 0.88,
  −$2,336. Net excluding the top 3 days is +$334 for the year.
- **The leg the 09-16 audit credited loses.** `ema_pullback_trend` is PF 0.75 over the
  independent year, and its shorts are PF 0.50.
- **Rolls inflate the raw result.** The corpus switches contracts at 00:00Z inside the
  Asian session on 4 nights, and those nights made +$1,623.
- **Fills are fine.** Next-bar-open entries are no worse than booked (PF 1.12 vs 1.10).
  Busy-skipped candidates are PF 0.99.
- **Variants** (declared before scoring, judged on Feb–Jul 12): V1 20:00–02:59 ET only,
  PF 0.94; V2 EMA-pullback only, PF 0.73; V3 22-continuation only, PF 0.995; V4
  22c EMA-aligned, no cohort D, 1.5R, PF 1.045 (misses the ≥ 1.30 bar and is negative
  without its best 3 days). **All fail.**

## Caveats

- Historical FTFC for Strat 2-2 reversal is a reconstruction from 15m bars, not the
  TradingView series.
- The earlier Session 2-2 months use reconstructed, unvalidated market-condition labels.
- The Asia D+EMA independent year was not pristine (see above).
- Forward samples are small for Session 2-2 (6) and Asia D+EMA (15). The verdicts rest
  on the historical independent periods. For Strat 2-2 reversal the forward sample (294)
  independently agrees.

## What this changes

- All three lanes are removed as promotion candidates.
- Nothing else: no runtime change, no prereg change, no replacement variants. Continued
  collection is allowed only as zero-cost telemetry, or, for Session 2-2, to close its
  registered one-look.
