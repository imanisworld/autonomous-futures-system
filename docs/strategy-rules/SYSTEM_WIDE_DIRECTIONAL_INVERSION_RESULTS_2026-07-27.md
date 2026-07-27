# System-wide directional inversion audit

## VERDICT

**The answer is mixed: Lane B is historically directionally inverted; the
frozen system as a whole is not simply “the right locations, wrong direction.”**

The exact Lane B inverse is positive after all requested costs and robustness
slices. In the #346/#358 book, fixed-attempt inversion ranges from **-$1,083.43
to +$1,252.60** depending on execution mode. The canonical IOC inverse is
essentially flat after costs (**-$6.21, PF 0.998**), market is only **+$110.82,
PF 1.058**, and stop-market becomes materially worse. Only the 8-tick
marketable-limit population is convincingly positive in aggregate.

The chronological system-path inverse reaches **+$3,557.56** for IOC, market,
and marketable-limit, but that is not a clean confirmation of an inverted
book. Direction reversal makes the anchored inverse IOC orders immediately
marketable, so those three modes collapse to the same 463-fill path. The result
is also MNQ-dependent (**+$3,967.38**) while MES remains negative
(**-$409.82**). Stop-market remains negative (**-$713.54**) and trips both
instrument breakers in H1.

No active direction-mapping defect was found that explains the losses.

## SYSTEM-WIDE ORIGINAL VS INVERTED

The original #358 rerun reconciled exactly on attempts, fills, resolved trades,
and net P&L for all four modes. The experiment used frozen code `74b1407` (the
strategy/replay/risk tree used to generate #358), the 626-file corrected corpus
with digest
`4ab5812659910235e8a26e7417f851e0a403855ff75183322e99b0b36970d3d4`,
one adverse tick, $1.48 round-trip commission, static exits, and pessimistic
stop-first same-bar resolution.

The fixed-attempt answer is the proper pure direction diagnostic:

| Mode | Original net | Fixed-attempt inverse net | Inverse PF | Interpretation |
|---|---:|---:|---:|---|
| IOC limit | -$802.28 | -$6.21 | 0.998 | Costs consume the gross inverse; no edge |
| Market | -$778.00 | +$110.82 | 1.058 | Too thin and only H1 is observable |
| Marketable limit | -$386.75 | +$1,252.60 | 1.186 | Promising, but H1 is -$51.92 and MES is -$654.26 |
| Stop-market | -$798.31 | -$1,083.43 | 0.500 | Inversion makes the result worse |

This disproves the simple claim that the old approximately -$800 result
automatically becomes a robust +$800 when direction is reversed.

## #346/#358 EXECUTION-MODE MATRIX

### A. Same approved-attempt population

| Mode | Original attempts | Original fills | Original net | Inverse attempts | Inverse fills | Inverse net | Inverse PF | Inverse max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| IOC limit | 165 | 97 | -$802.28 | 165 | 165 | -$6.21 | 0.998 | $1,129.38 |
| Market | 108 | 108 | -$778.00 | 108 | 108 | +$110.82 | 1.058 | $552.36 |
| Marketable limit | 537 | 101 | -$386.75 | 537 | 537 | +$1,252.60 | 1.186 | $949.25 |
| Stop-market | 110 | 109 | -$798.31 | 110 | 66 | -$1,083.43 | 0.500 | $1,083.43 |

The opposite IOC fills are not assumed. They are recomputed at the original
decision-bar close. All happen to fill because the unchanged anchored price is
on the immediately marketable side after direction reversal. Stop-market does
not share that property: only 66 of 110 opposite triggers fill on the next bar.

Same-bar ambiguous mirrored brackets were resolved stop-first: IOC 4, market 5,
marketable limit 39, stop-market 0.

### B. Chronological inverted system path

| Mode | Attempts | Fills | Net | PF | Max DD | Breaker result |
|---|---:|---:|---:|---:|---:|---|
| IOC limit | 463 | 463 | +$3,557.56 | 1.359 | $1,033.34 | MES halts 2025-10-28; MNQ runs through 2026-07-22 |
| Market | 463 | 463 | +$3,557.56 | 1.359 | $1,033.34 | Same path |
| Marketable limit | 463 | 463 | +$3,557.56 | 1.359 | $1,033.34 | Same path |
| Stop-market | 94 | 48 | -$713.54 | 0.492 | $713.54 | MNQ halts 2025-09-04; MES halts 2025-09-23 |

StopLimit remains excluded because the frozen PaperBroker has no valid
StopLimit model.

## TRADE-LEVEL VS SYSTEM-PATH INVERSION

Trade-level inversion freezes the approved attempts produced by each original
#358 arm. Counterfactual P&L cannot create or remove later attempts. This gives
the unconfounded answer about those exact decisions.

System-path inversion changes direction just before execution and lets the
resulting P&L flow through account balance, daily state, open-position blocking,
and the 20% drawdown breaker. It therefore exposes 463 attempts in the three
immediately marketable modes, versus 165/108/537 in their different original
paths.

The +$3,557.56 path is real under that counterfactual, but it combines
direction, changed fill availability, and changed breaker survival. It cannot
be cited as “the inverse of the original 97/108/101 fills.”

## PER-STRATEGY MATRIX

The table below uses the widest fixed population, the 537 original
marketable-limit attempts. “N” is resolved inverse trades.

| Strategy | N | Original net / PF | Inverse net / PF | Inverse H1 / H2 | Inverse L / S | Inverse max DD | Classification |
|---|---:|---:|---:|---:|---:|---:|---|
| ORB Breakout | 111 | -$94.38 / 0.353 | +$745.72 / 2.392 | +$404.30 / +$341.42 | +$219.96 / +$525.76 | $73.86 | **INVERTED EDGE** |
| ORB Reclaim | 346 | -$300.29 / 0.887 | +$175.42 / 1.029 | -$507.60 / +$683.02 | $0 / +$175.42 | $1,036.26 | **NEITHER HAS EDGE** |
| ORB Rejection | 16 | -$20.94 / 0.000 | +$73.82 / 4.371 | +$33.62 / +$40.20 | +$73.82 / $0 | $10.44 | **AMBIGUOUS** (N too small) |
| VWAP Reclaim | 43 | +$19.52 / 1.317 | +$193.04 / 2.258 | +$3.80 / +$189.24 | $0 / +$193.04 | $70.40 | **AMBIGUOUS** |
| VWAP Rejection | 7 | $0 / n/a | +$45.52 / 10.896 | +$34.84 / +$10.68 | +$45.52 / $0 | $4.60 | **AMBIGUOUS** (N=7) |
| PDL Reclaim | 14 | +$9.34 / 1.584 | +$19.08 / 1.354 | -$20.88 / +$39.96 | +$19.08 / $0 | $36.84 | **AMBIGUOUS** |
| PDH Reclaim | 0 | $0 / n/a | $0 / n/a | $0 / $0 | $0 / $0 | $0 | **AMBIGUOUS** (no observations) |
| VWAP Hold | 0 | $0 / n/a | $0 / n/a | $0 / $0 | $0 / $0 | $0 | **AMBIGUOUS** (no observations) |

ORB Breakout is the one component that clears the requested historical
robustness screen in its marketable-limit inverse: all four quarters are
positive (+$349.38, +$54.92, +$254.08, +$87.34), both inverse directions are
positive, all three sessions are positive, top five winners are 25.4% of gross
winner dollars, and N=111. It still fails cross-execution confirmation because
the inverse stop-market variant is negative.

No strategy qualifies as ORIGINAL EDGE in this frozen evidence. No strategy is
classified DIRECTION-MAPPING BUG FOUND.

## ORB RECLAIM ORIGINAL VS MEAN-REVERSION INVERSE

ORB Reclaim does identify locations with some later mean reversion, but not a
stable inverse edge.

| Mode | Original fills / net | Fixed inverse fills / net | Inverse PF |
|---|---:|---:|---:|
| IOC | 86 / -$588.28 | 131 / -$300.13 | 0.899 |
| Market | 81 / -$370.88 | 81 / -$106.88 | 0.942 |
| Marketable limit | 85 / -$300.29 | 346 / +$175.42 | 1.029 |
| Stop-market | 81 / -$354.63 | 54 / -$938.17 | 0.524 |

For the positive marketable-limit inverse:

- MNQ: +$829.68; MES: -$654.26.
- New York: +$639.17; Asian: +$102.63; London: -$566.38.
- H1: -$507.60; H2: +$683.02.
- All inverse trades are SHORT because the frozen ORB Reclaim detector is
  long-only.
- Gross before commission is +$687.50; commission is $512.08; net is +$175.42.
- Top five winners are only 11.2% of winner dollars, so giant-winner
  concentration is not the failure.

The chronological inverse is +$1,724.30, but remains negative in H1
(-$330.92), MES (-$409.82), Asian (-$70.93), and London (-$166.88). Its
+$2,055.22 H2 and +$1,962.11 New York results dominate. Classification:
**NEITHER HAS EDGE**, not “inverted ORB Reclaim.”

## LANE B CLOSE-MOMENTUM INVERSE

| Version | Trades | Gross | Net | Exp/trade | PF | WR | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| Original | 490 | -$4,858.50 | -$6,073.70 | -$12.3953 | 0.744 | 45.92% | $7,423.22 |
| Exact inverse | 490 | +$4,858.50 | +$3,643.30 | +$7.4353 | 1.194 | 51.43% | $1,320.96 |

### H1/H2, direction, and untouched holdout

| Slice | Inverse N | Inverse net | PF |
|---|---:|---:|---:|
| H1 | 245 | +$3,474.90 | 1.381 |
| H2 | 245 | +$168.40 | 1.017 |
| LONG | 222 | +$2,838.44 | 1.282 |
| SHORT | 268 | +$804.86 | 1.092 |
| Untouched final 25% | 123 | +$47.96 | 1.008 |

All four chronological quarters are positive: +$402.94, +$3,071.96,
+$120.44, and +$47.96. The result is persistent but decays sharply; H2 and
holdout are barely over break-even.

### Mathematical reconciliation

Entry and exit timestamps and raw prices are identical. If `s` is original
exposure (+1 LONG, -1 SHORT), raw gross is
`s × (exit - entry) × $2`; inverse exposure is `-s`, so inverse gross is
exactly its negative. The measured reconciliation error is $0.00.

Net does not negate: each side of the counterfactual again pays adverse
slippage and the $1.48 commission. Thus:

`inverse net = -original gross - inverse slippage - inverse commission`

At one tick per side that is
`$4,858.50 - $490.00 - $725.20 = $3,643.30`, not +$6,073.70.

## COST SENSITIVITY

| Lane B adverse ticks per side | Net | Exp/trade | PF | Max DD |
|---:|---:|---:|---:|---:|
| 1 | +$3,643.30 | +$7.4353 | 1.194 | $1,320.96 |
| 2 | +$3,153.30 | +$6.4353 | 1.166 | $1,347.96 |
| 3 | +$2,663.30 | +$5.4353 | 1.138 | $1,374.96 |
| 4 | +$2,173.30 | +$4.4353 | 1.111 | $1,401.96 |

System inversions retain the frozen one-adverse-tick #358 posture; no
unrequested parameter sweep was introduced.

## CONCENTRATION

Lane B top winner is $546.52 and top five sum to $2,194.10. Net remains
+$1,449.20 after removing the top five.

The positive marketable-limit system inverse has top-one/top-three/top-five
winner shares of 1.9%/5.4%/8.6%. The chronological +$3,557.56 path has
2.9%/8.3%/12.8%. Aggregate system profitability is not a several-giant-winner
artifact; its weaknesses are instrument, period, session, and execution-model
dependence.

## BREAKER IMPACT

The original IOC path stopped MNQ on 2025-09-08 and MES on 2025-12-11, leaving
no H2 approved attempts. The immediately marketable system inverse keeps MNQ
alive through 2026-07-22 and exposes most of the profitable H2 population, but
MES still breaches the 20% breaker on 2025-10-28.

This is why fixed-attempt and system-path answers differ by thousands of
dollars. The system-path result is a path-dependent counterfactual, not
evidence that the original stopped population would itself have earned
+$3,557.56 when flipped.

## DIRECTIONAL-SEMANTICS AUDIT

- ORB breakout maps `above + VWAP above + trend UP` to LONG with stop below
  and target above; the below/down branch is the exact SHORT mirror.
- ORB Reclaim maps `reclaimed_high + VWAP above` to LONG. ORB Rejection maps
  `rejected_high` to SHORT. Their bracket signs are consistent.
- VWAP Reclaim and PDH continuation map bullish context to LONG. VWAP Hold,
  VWAP Rejection, and PDL continuation map bearish context to SHORT.
- `2U/2D` aliases normalize explicitly; bare `2` stays directionless and fails
  closed. Generic completed-pattern direction follows the current 2U/2D bar.
  The causal 2-1-2 state machine explicitly distinguishes continuation from
  1-2-2 reversal and constructs matching bracket sides.
- Replay copies `decision.setup.direction`, entry, stop, and target directly
  into `BracketOrder`. Runtime does the same. PaperBroker applies adverse
  entry slippage and bracket resolution symmetrically by side.
- The generic Strat classifier, causal pattern identities, and #359 60M
  3-2-2 identity are separate code paths; no identity collision appears in
  the frozen #346/#358 attempts, which contain no Strat fills.

## BUGS FOUND

**No active direction-mapping bug was found.**

Two nomenclature caveats are real but do not flip an order:

1. `pdh_reclaim`/`pdl_reclaim` use “above PDH” and “below PDL” continuation
   states rather than an observed cross-back reclaim. The code comments disclose
   this. It is a strategy-definition/name issue, not LONG/SHORT inversion.
2. VWAP Rejection previously had an impossible same-bar reclaim/below
   condition; the frozen code uses a causal prior-bar `failed_reclaim` flag.
   That historical defect suppressed candidates rather than reversing them.

The negative results therefore belong under **STRATEGY HYPOTHESIS WRONG** or
**NO EDGE**, not an execution-side direction bug.

## WHAT THIS PROVES

- Literal Lane B close momentum was directionally backward in this sample;
  its exact fixed-time inverse passes the requested historical robustness
  screen after costs.
- The old frozen book is not uniformly directionally backward.
- ORB Breakout locations support a credible marketable-limit mean-reversion
  research hypothesis.
- ORB Reclaim's aggregate inverse is unstable and cannot be promoted.
- Breaker survival and opposite-side fill mechanics can dominate a
  system-path inversion.

## WHAT IT DOES NOT PROVE

- It does not prove future profitability or live fill quality.
- It does not validate deploying Lane B or any inverted system.
- It does not show that +$3,557.56 is obtainable by flipping the original
  #358 fills; that path contains hundreds of newly available trades.
- It does not validate changing ORB Reclaim into production mean reversion.
- It does not justify filters, parameter tuning, more contracts, or any
  change to #359.

## WHICH INVERSES DESERVE A SEPARATE FROZEN RESEARCH PR

1. **Lane B exact inverse** deserves a new, separately preregistered forward
   paper/research PR. Its H2 and holdout margins are thin, so it is not a
   deployment candidate.
2. **ORB Breakout marketable-limit inverse** deserves an isolated frozen
   research PR using the same detector locations and explicit mean-reversion
   execution. It passed H1/H2, all quarters, both directions, all sessions,
   concentration, and N in this audit, but must reconcile its failure under
   stop-market and must not inherit the full system-path result.
3. No other system inverse currently deserves promotion.

No box access, deployment, runtime/config change, sizing change, #359 change,
or parameter/filter optimization was performed.
