# Daily 2-2 and MES 1-2-2 — Retirement Audit — 2026-09-23

## Verdict

| Lane | Classification | Primary defect |
|---|---|---|
| MNQ Daily 2-2 (`daily_22_5k`) | **BROKEN for the current $5k design** | Ledger far too small for the strategy's normal risk; roll-corrected historical edge much weaker than the headline |
| MES 1-2-2 (`mes_122_1500`, `strat_122`) | **BROKEN — execution/fill realism** | The positive #547 year depends on a fill earlier than the decision; the older independent year and the full 625-day set both lose |
| New variants | **None worth preregistering** | Every tested variant failed in-sample or out-of-sample |

Both are **retired as promotion candidates**. Operator classification, 2026-09-23,
based on the two research-only audits below.

**AUDIT / DOCS ONLY.** No runtime, strategy, risk, broker, collector, TradingView or
deployment change was made. Both paper lanes are still running unchanged. Stopping them is
a separate runtime change that needs its own explicit operator GO. Do not build replacement
variants from this document.

## Daily 2-2

Forward since the 2026-09-09 epoch: 2 closed trades, 0 wins, ledger $3,613.54 (27.73%
drawdown against its 30% hard halt). The 2026-09-10 short spans the U6→Z6 roll and was
already classified `ROLL_CONTAMINATED`, so the clean forward sample is n=1. The verdict
rests on history, not on those two trades.

### Findings

1. **The ledger is too small for the strategy.** Median planned risk is about $744, or
   15% of $5,000, so two ordinary losses reach the halt. The $1,750 per-trade cap never
   applied in forward trading (actual risks were $592 and $790). Resampling the
   backtest's own 34 trades from $5,000 (roll-corrected), the probability of halting
   within 40 trades is **82%**. On raw prices it is 67% at $5k, 37% at $10k and 8% at
   $20k. The 25–27% historical drawdown was measured from a peak of about $9.5k built
   on early wins. In dollars it is 50.7% of $5,000.
2. **The replay corpus is not roll-adjusted.** `data/replay_corpus_v1_5m_4hr_audit/MNQ`
   is raw front-month data. Correcting the 8 roll gaps (+215 to +278 points each):

   | | fills | W/L | net | PF | max DD |
   |---|---|---|---|---|---|
   | raw (the activation headline) | 34 | 15/19 | +$13,572 | 1.95 | 26.6% |
   | roll-corrected | 34 | 13/21 | +$8,829 | 1.56 | 26.5% |

   A fresh $5,000 started at the out-of-sample half (2025-07-01 onward), roll-corrected,
   **halts** (8 trades, 2 wins, −$1,623). PF 1.56 is below the 1.94 single-test null
   bar.
3. **Occupancy selection inflated the headline.** If the lane were always free to take
   the next signal, the result is 74 trades at PF 1.27 raw and 1.15 roll-corrected. The
   40 signals skipped because a position was open lost money. In the out-of-sample half
   they were PF 0.44.
4. **The recent losing streak is not proof of degradation.** A price-only replay of the
   box period (2026-07-13 → 09-23) shows a 5-loss streak starting 08-10 that halts at
   32.9%. The probability of a streak of 5 or more in 41 trades at the backtest loss
   rate is about 81%. This is consistent with the historical distribution, and that
   distribution is itself the problem.

### Variants tested (declared before running; all reported)

Split fixed before looking: in-sample 2024-07-02 → 2025-06-30; out-of-sample 2025-07-01 →
2026-06-26; box period price-only. All results roll-corrected.

| Variant | In-sample | Out-of-sample | Box period |
|---|---|---|---|
| Base | PF 1.45, DD 26.5% | PF 0.56, **halted** | **halted** |
| V1 risk cap 15% of balance | **halted** | **halted** | −$1,244 |
| V2 2R from actual fill, no R:R rule | PF 1.01, **halted** | PF 0.51, **halted** | PF 2.19, n=8 |
| V3 breakeven at 1.5R | PF 1.59 | PF 0.56, **halted** | **halted** |
| V4 V1 + V2 | **halted** | **halted** | −$1,244 |

The actual-fill R:R ≥ 2 rule, the IOC cancel and the context gate each blocked groups
that historically lost money, so they are not the cause. No direction, session,
range/ATR or relative-volume subgroup is significant (all Fisher p ≥ 0.14).

## MES 1-2-2

The deployed engine reproduces #547 exactly (40 trades, 11 wins, +$32.05, PF 1.035). Zero
forward trades since 2026-09-09 is plausible at about 3 trades a month, so the frequency
is not the defect.

### Findings

1. **The booked entry is not attainable by the decision it follows.** The trigger is
   crossed inside the watched 15m bar, but the lane only decides at that bar's close,
   when its gates read the bar's regime label. Paper and replay then book the entry at
   the trigger price from earlier in the bar. This is the gap the 2026-09-19 pre-arm
   audit (`docs/mes-122-prearm-feasibility-audit-2026-09-19.md`) identified; this audit
   measures its cost. Entry at the decision-bar close +1 tick:

   | Window | Booked | Decision-close +1 tick |
   |---|---|---|
   | #547 year (in-sample) | +$26 | **−$465** |
   | 2024-09-23 → 2025-07-23 (older, independent) | −$89 | −$332 |
   | 2026-07 → 09 | +$31 | +$10 |

   21 of 74 trades also exit on the entry bar at exact structural prices. A resting-stop
   design gated on the arm-bar label instead would be a different strategy identity. It
   is untested, and this document does not propose it.
2. **Independent data loses even with the booked fills.** On the older year, which was
   never used for 1-2-2 selection: 28 trades, **−$89, PF 0.84**. The full 625-day set
   (74 trades) is −$32.
3. **The 15-point target floor hurts.** Trades whose target the floor raised lost money
   in every window (−$72 / −$489 / −$6). The same trades at the native 2R target did
   better in the first two windows (−$9.50 / −$260). All of the in-sample profit comes
   from the 12 trades the floor did not change (+$515). On the older year that group is
   3 trades, −$17.
4. **Frequency is limited before any gate.** The detector tracks 2-1-2 and 1-2-2 in one
   shared watch state, so 72% of completed 1-2-2 patterns (4,315 of 6,026) never arm.
   Of 216 triggered setups blocked by the TRENDING gate, only 30 would pass the
   remaining gates (volume < 0.8 and trend strength block the rest).

### Higher-frequency candidates (declared before computing outcomes; all reported)

Each candidate admits some RANGE_BOUND setups for 1-2-2 and keeps every other gate.

| Candidate | Extra trades | Added trades (IS / older-year OOS) |
|---|---|---|
| C1 all RANGE_BOUND | +38–46% | −$331 / −$182 |
| C2 + trend agrees with the trade | +35% | −$277 / same as C1 |
| C3 + New York session only | +7–8% | −$174 / +$31 |
| C4 + above-median volatility | +10–14% | −$210 / −$58 |

None reaches 1.5x frequency. Every candidate's added trades lose in-sample.

## Method notes and caveats

- Costs: 1 adverse tick on entry, +1 tick on same-bar exits, 1-tick stop slippage, $1.48
  round-turn commission (MES). The MES engine's drawdown breaker was disabled for
  research only.
- The MES 625-day dataset (2024-09-23 → 2026-09-22) has no provenance manifest. It
  reproduces 39 of the 40 #547 trades. Live labels (Pine) and rebuilt historical labels
  agree on 88% of the 849 overlapping bars.
- The Daily roll correction shifts OHLC and EMAs but does not recompute EMAs across the
  roll. Box 5m bars are missing 2026-07-03 → 07-12. The box-period replay has no context
  fields, so it is price-only.
- Forward samples for both lanes are too small to carry any conclusion by themselves.
  Both verdicts rest on the historical re-examination.

## What this changes

- MES 1-2-2 is no longer described as "PROMISING BUT UNPROVEN"; its inventory row is
  **BROKEN**.
- Daily 2-2 and MES 1-2-2 are removed as promotion candidates.
- Any future Daily-style swing evidence needs roll-adjusted data first.
- Nothing else. No runtime change, no replacement variants.
