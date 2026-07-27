# Lane B MNQ Close-Momentum — Literal Baseline Results

## VERDICT

**BROKEN — REJECT**

The frozen literal rule is classified solely from the pre-registered cadence, cost, chronology, direction, concentration, and holdout checks.

## SOURCE VERIFICATION

| Claim | Primary source | Verified? | Exact definition |
|---|---|---:|---|
| NQ market/session | Baltussen et al. (2021), §§1–2, Table A1 | Yes | NQ futures, underlying cash-market hours 09:30–16:00 ET. |
| Rest-of-day signal | Baltussen et al. (2021), §1 | Yes | Previous market close through 30 minutes before current close; overnight is included. |
| Direction/holding period | Baltussen et al. (2021), Eq. (12), Table 6 | Yes | LONG if ROD return is positive, SHORT otherwise; hold only for 15:30–16:00 ET. |
| Threshold/stops/targets | Baltussen et al. (2021), Eq. (12) | Yes | None. Exact zero follows the SHORT “otherwise” branch. |
| Short sessions | Baltussen et al. (2021), §2 | Yes | Early-close days removed. |
| Costs | Baltussen et al. (2021), §3.5 | Yes | Main results are gross; no NQ cost-adjusted strategy result is published. |
| NQ evidence | Baltussen et al. (2021), Tables A1/B1 | Yes | 6,017 observations through 2020; positive significant ROD slope, but no NQ strategy P&L table. |
| 24.3% / 1.67 / +6 bps / 38% / 2.25 | Baltussen et al. (2021) | **No** | Absent from the paper; later secondary-source figures for a modified Noise-Area strategy. |

The claimed 24.3% return, 1.67 Sharpe, +6 bps/trade, 38% win rate, and
2.25 payoff ratio are **not present in the primary paper**. They are
secondary-source figures for a materially different, modified strategy.
Primary sources: [publisher PDF](https://pure.eur.nl/ws/portalfiles/portal/58145484/1_s2.0_S0304405X21001598_main.pdf),
[university record](https://repub.eur.nl/pub/131621), and the distinct
[Quantitativo adaptation](https://www.quantitativo.com/p/intraday-momentum-for-es-and-nq).
Full definitions are frozen in the preregistration.

## PRE-REGISTERED RULE

At 15:30 ET, compare the just-closed 15:25 five-minute bar with the prior
full session's 16:00 close. Go LONG if the return is positive and SHORT
otherwise; enter at the 15:30 bar open and exit at the 15:55 bar close
(16:00 ET). No threshold, stop, target, or filter. Baseline costs are
$1.48 round-trip commission plus one adverse tick per side.

## DATASET / COVERAGE

- Raw cache coverage: 2024-07-02 through 2026-06-26.
- Full sessions found: 491.
- Shortened/incomplete RTH sessions excluded: 21.
- Missing weekdays disclosed: 7.
- Eligible signals after the required prior close: 490.
- Average cadence: 4.76 trades/week.

## BASELINE RESULTS

| Scope | Trades | L / S | W / L | WR | Gross | Net | Exp/trade | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| All | 490 | 268 / 222 | 225 / 265 | 45.9% | $-4,858.50 | $-6,073.70 | $-12.40 | 0.74 | $-7,423.22 |

Commissions were $725.20; adverse slippage cost was $490.00. Longest losing streak: 14. Average win: $78.35; average loss: $-89.44. Signals/fills/resolved were 490/490/490.

## H1 / H2

| Half | Trades | L / S | W / L | WR | Gross | Net | Exp/trade | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| H1 | 245 | 130 / 115 | 106 / 139 | 43.3% | $-4,082.50 | $-4,690.10 | $-19.14 | 0.65 | $-4,930.62 |
| H2 | 245 | 138 / 107 | 119 / 126 | 48.6% | $-776.00 | $-1,383.60 | $-5.65 | 0.87 | $-2,938.58 |

## LONG / SHORT

| Direction | Trades | L / S | W / L | WR | Gross | Net | Exp/trade | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LONG | 268 | 268 / 0 | 130 / 138 | 48.5% | $-1,469.50 | $-2,134.14 | $-7.96 | 0.79 | $-3,529.22 |
| SHORT | 222 | 0 / 222 | 95 / 127 | 42.8% | $-3,389.00 | $-3,939.56 | $-17.75 | 0.71 | $-4,980.76 |

## COST SENSITIVITY

| Adverse ticks/side | Trades | L / S | W / L | WR | Gross | Net | Exp/trade | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 tick | 490 | 268 / 222 | 225 / 265 | 45.9% | $-4,858.50 | $-6,073.70 | $-12.40 | 0.74 | $-7,423.22 |
| 2 ticks | 490 | 268 / 222 | 220 / 270 | 44.9% | $-4,858.50 | $-6,563.70 | $-13.40 | 0.73 | $-7,837.22 |
| 3 ticks | 490 | 268 / 222 | 216 / 274 | 44.1% | $-4,858.50 | $-7,053.70 | $-14.40 | 0.71 | $-8,277.84 |
| 4 ticks | 490 | 268 / 222 | 213 / 277 | 43.5% | $-4,858.50 | $-7,543.70 | $-15.40 | 0.69 | $-8,735.84 |

## CONCENTRATION

- Top winner contribution: $451.02.
- Top five winners: $1,766.10 (-29.08% of total net).
- Net with top winner removed: $-6,524.72.
- Net with top five removed: $-7,839.80.
- Because total net is negative, the top-five percentage has no positive-profit concentration interpretation.

## HOLDOUT

| Partition | Trades | L / S | W / L | WR | Gross | Net | Exp/trade | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Development 75% | 367 | 204 / 163 | 162 / 205 | 44.1% | $-4,505.50 | $-5,415.66 | $-14.76 | 0.69 | $-5,744.36 |
| Untouched final 25% | 123 | 64 / 59 | 63 / 60 | 51.2% | $-353.00 | $-658.04 | $-5.35 | 0.89 | $-2,029.64 |

The split index and 25% fraction were frozen before results. No rule was
changed after the holdout was opened.

## FAILURE MODES

- The edge does not remain positive through all 1–4 tick-per-side cost stresses.
- The frozen result is not positive in both chronological halves.
- At least one direction is not independently positive.
- The untouched final 25% holdout is not positive.
- The baseline is already negative before concentration adjustment; removing the top five winners makes it still worse.

## WHAT THE RESULT PROVES

- Whether the literal paper rule survives this local MNQ five-minute corpus under the frozen cost model.
- Whether its historical result is stable across chronology, direction, calendar blocks, and an untouched final 25% holdout.

## WHAT IT DOES NOT PROVE

- That the academic NQ sample or its tick-level construction was exactly replicated.
- That five-minute boundary prices can be filled live at the modeled slippage.
- That any historical edge will persist prospectively.
- That the unrelated Quantitativo Noise-Area NQ statistics are reproducible.

## DESCRIPTIVE PERIOD / REGIME STABILITY

| Quarter | Trades | L / S | W / L | WR | Gross | Net | Exp/trade | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2024-Q3 | 61 | 34 / 27 | 35 / 26 | 57.4% | $-317.00 | $-468.28 | $-7.68 | 0.81 | $-1,109.64 |
| 2024-Q4 | 62 | 35 / 27 | 22 / 40 | 35.5% | $-212.50 | $-366.26 | $-5.91 | 0.84 | $-1,170.44 |
| 2025-Q1 | 59 | 27 / 32 | 20 / 39 | 33.9% | $-1,677.50 | $-1,823.82 | $-30.91 | 0.52 | $-1,874.42 |
| 2025-Q2 | 62 | 34 / 28 | 28 / 34 | 45.2% | $-1,941.00 | $-2,094.76 | $-33.79 | 0.55 | $-3,002.42 |
| 2025-Q3 | 63 | 37 / 26 | 35 / 28 | 55.6% | $211.50 | $55.26 | $0.88 | 1.04 | $-426.24 |
| 2025-Q4 | 62 | 37 / 25 | 24 / 38 | 38.7% | $-470.50 | $-624.26 | $-10.07 | 0.78 | $-1,259.72 |
| 2026-Q1 | 61 | 28 / 33 | 26 / 35 | 42.6% | $-1,051.50 | $-1,202.78 | $-19.72 | 0.60 | $-1,423.92 |
| 2026-Q2 | 60 | 36 / 24 | 35 / 25 | 58.3% | $600.00 | $451.20 | $7.52 | 1.15 | $-932.08 |

| Signal-time regime | Trades | L / S | W / L | WR | Gross | Net | Exp/trade | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CHOPPY | 21 | 11 / 10 | 8 / 13 | 38.1% | $-1,210.50 | $-1,262.58 | $-60.12 | 0.48 | $-1,392.36 |
| CONSOLIDATING | 51 | 30 / 21 | 26 / 25 | 51.0% | $253.00 | $126.52 | $2.48 | 1.08 | $-600.66 |
| TRENDING | 418 | 227 / 191 | 191 / 227 | 45.7% | $-3,901.00 | $-4,937.64 | $-11.81 | 0.75 | $-6,427.38 |

Month-level results are preserved in
`scripts/lane_b_mnq_close_momentum_results.json`. These descriptive
breakdowns were not used to create filters.

## RECOMMENDATION

**REJECT**

No deploy, runtime change, execution change, sizing change, or parameter
optimization was performed.
