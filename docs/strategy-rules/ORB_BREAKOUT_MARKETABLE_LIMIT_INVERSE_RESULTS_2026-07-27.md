# ORB Breakout marketable-limit inverse — corrected fixed-one-contract results

## VERDICT

**VALIDATED**

## FINAL DECISION

**PROMOTE TO PAPER-BUILD CANDIDATE**

This verdict applies only to the isolated MNQ paper-build candidate defined at
preregistration SHA
`eda2c3344304fe2f9daf74da6505acdf1256fad4`.

It does not authorize runtime implementation, merge, deployment, live trading,
or a change to the deployed box.

## FROZEN CANDIDATE

- MNQ only.
- Exact committed population of 111 ORB Breakout attempts.
- Original LONG becomes inverse SHORT; original SHORT becomes inverse LONG.
- Planned entry and absolute stop/target distances are unchanged.
- Eight-tick marketable IOC entry model is unchanged.
- Baseline is one adverse entry tick, one adverse stop tick, clean target
  fills, and $1.48 round-trip commission.
- Same-bar stop/target ambiguity is pessimistic: stop first.
- Every inverse order is exactly one contract.
- Dynamic sizing recommendations are diagnostic only and cannot resize,
  suppress, or add an ORB Breakout order.
- Account P&L, the normal 20% breaker, existing-position gates, and ordinary
  account gates evolve chronologically.
- No filter, threshold, stop, target, session, breaker, execution model, or
  rescue variant changed.

The previous pass under SHA
`b2c586af8e2b624e93fe0bf18fbab4be15f2003d` remains invalid because its sizing
contract was contradictory. None of that stopped pass's economic output is
used here.

## OVERALL RESULTS

| Analysis | Attempts | Fills | Resolved | Gross | Net | Expectancy | PF | Win rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Original #358 ORB population | 111 | 6 | 6 | -$85.50 | -$94.38 | -$15.73 | 0.353 | 16.67% |
| Fixed-population inverse | 111 | 111 | 111 | $910.00 | **$745.72** | **$6.72** | **2.392** | **62.16%** |
| Chronological system-path inverse | 108 | 108 | 108 | $824.50 | **$664.66** | **$6.15** | **2.251** | **62.04%** |

All 111 fixed-population inverse IOC orders filled. The chronological path
retained 108 source identities, removed three, and added none.

## ORIGINAL-VERSUS-INVERSE ATTRIBUTION

| Effect | Result |
|---|---:|
| Common resolved attempts | 6 |
| Directional net delta on common resolved attempts | +$20.50 |
| Inverse-only resolved attempts | 105 |
| Fill-selection net delta | +$819.60 |
| Reconciled total fixed net delta | +$840.10 |
| Actual total fixed net delta | +$840.10 |

Most improvement comes from the causal fill-selection consequence of reversing
the IOC side, not merely from flipping the six original fills. That is part of
the preregistered candidate: the direction is inverted before the same bounded
eight-tick IOC model is applied.

## H1 / H2

| Path | Half | Trades | Net | Expectancy | PF | Win rate |
|---|---|---:|---:|---:|---:|---:|
| Fixed | H1 | 65 | $404.30 | $6.22 | 2.343 | 61.54% |
| Fixed | H2 | 46 | $341.42 | $7.42 | 2.455 | 63.04% |
| System | H1 | 64 | $408.78 | $6.39 | 2.379 | 62.50% |
| System | H2 | 44 | $255.88 | $5.82 | 2.090 | 61.36% |

Both halves are positive in both required analyses.

## YEAR

| Path | Year | Trades | Net | PF | Win rate |
|---|---|---:|---:|---:|---:|
| Fixed | 2025 | 61 | $401.22 | 2.438 | 63.93% |
| Fixed | 2026 | 50 | $344.50 | 2.343 | 60.00% |
| System | 2025 | 60 | $405.70 | 2.478 | 65.00% |
| System | 2026 | 48 | $258.96 | 2.009 | 58.33% |

## EQUAL-COUNT CHRONOLOGICAL PERIODS

| Path | Period | Dates | Trades | Net | PF |
|---|---|---|---:|---:|---:|
| Fixed | P1 | 2025-07-24–2025-09-12 | 27 | $272.04 | 4.027 |
| Fixed | P2 | 2025-09-15–2025-12-09 | 28 | $112.56 | 1.667 |
| Fixed | P3 | 2025-12-16–2026-03-16 | 28 | $255.06 | 3.719 |
| Fixed | P4 | 2026-03-17–2026-07-21 | 28 | $106.06 | 1.579 |
| System | P1 | 2025-07-24–2025-09-12 | 27 | $272.04 | 4.027 |
| System | P2 | 2025-09-15–2025-12-09 | 27 | $117.04 | 1.713 |
| System | P3 | 2025-12-16–2026-03-16 | 27 | $247.04 | 3.633 |
| System | P4 | 2026-03-16–2026-07-21 | 27 | $28.54 | 1.156 |

Every period is positive. The most recent system period is materially weaker,
but it remains positive rather than showing catastrophic decay.

## INVERSE LONG / SHORT

| Path | Direction | Trades | Net | PF | Win rate |
|---|---|---:|---:|---:|---:|
| Fixed | LONG | 23 | $219.96 | 3.688 | 65.22% |
| Fixed | SHORT | 88 | $525.76 | 2.159 | 61.36% |
| System | LONG | 23 | $219.96 | 3.688 | 65.22% |
| System | SHORT | 85 | $444.70 | 1.990 | 61.18% |

Both inverse directions are independently positive.

## SESSION

| Path | Session | Trades | Net | PF |
|---|---|---:|---:|---:|
| Fixed | Asian | 6 | $95.62 | 13.783 |
| Fixed | London | 71 | $486.92 | 2.391 |
| Fixed | New York | 34 | $163.18 | 1.916 |
| System | Asian | 6 | $95.62 | 13.783 |
| System | London | 68 | $405.86 | 2.175 |
| System | New York | 34 | $163.18 | 1.916 |

All sessions are positive. Asian has only six observations, so its very high
PF is not treated as stable standalone evidence. The result does not depend on
Asian: London and New York are both positive.

## SLIPPAGE STRESS

The baseline has one adverse tick. The frozen stress tiers add one through four
ticks, producing total PaperBroker slippage of two through five ticks.

| Total slippage | Fixed net | Fixed PF | System net | System PF |
|---:|---:|---:|---:|---:|
| 1 tick baseline | $745.72 | 2.392 | $664.66 | 2.251 |
| 2 ticks | $638.22 | 2.104 | $559.66 | 1.977 |
| 3 ticks | $530.72 | 1.854 | $454.66 | 1.740 |
| 4 ticks | $423.22 | 1.637 | $349.66 | 1.532 |
| 5 ticks | $315.72 | 1.445 | $252.18 | 1.359 |

Both paths remain profitable through the full preregistered +4-tick stress.

## RECENT WINDOWS

- Fixed latest 25%: 28 trades, +$106.06, PF 1.579.
- System latest 25%: 27 trades, +$28.54, PF 1.156.
- Latest system rolling three months: 16 trades, +$13.32, PF 1.111.
- Latest system rolling six months: 42 trades, +$248.34, PF 2.079.
- Every system rolling three- and six-month window is positive.
- One fixed rolling three-month window ending 2026-01 is effectively flat:
  -$1.10, PF 0.989. Later fixed windows recover and finish positive.

The latest path is weaker and ends underwater, but it is not a negative recent
regime across the required system-path windows.

## CONCENTRATION

| Removal | Fixed net remaining | System net remaining |
|---|---:|---:|
| Top 1 winner | $671.70 | $599.14 |
| Top 5 winners | $420.12 | $354.06 |
| Top 10 winners | $229.02 | $196.46 |

The top winner contributes 5.78% of fixed winner dollars and 5.48% of system
winner dollars. Removing the top ten winners leaves both paths profitable.

## DRAWDOWN, LOSING STREAK, AND RECOVERY

| Path | Max drawdown | Peak–trough | Longest losing streak | Max recovery | Terminal state |
|---|---:|---|---:|---|---|
| Fixed | $73.86 | 2026-06-09–2026-07-21 | 5 | 90 calendar days / 20 observations | Unrecovered, 42 days / 7 trades |
| System | $73.86 | 2026-06-09–2026-07-21 | 5 | 50 calendar days / 16 observations | Unrecovered, 42 days / 7 trades |

The terminal drawdown is a real limitation and must remain visible in any
paper-build review. It is not large enough relative to baseline net to overturn
the otherwise broad robustness result.

## BREAKER / SYSTEM-PATH EFFECTS

- Retained source attempts: 108.
- Removed source attempts: 3.
- Added attempts: 0.
- Fixed net: $745.72.
- System net: $664.66.
- Non-additive system-minus-fixed difference: -$81.06.
- The three removed fixed-path outcomes sum to exactly $81.06.
- The baseline inverse did not create a new MNQ breaker halt.
- The existing MES breaker date/reason is unchanged from the original system;
  MES contributes no ORB Breakout attempts to this candidate.

Removed stable identities:

1. 2025-12-08 14:00Z, original LONG, London.
2. 2026-04-22 12:15Z, original LONG, London.
3. 2026-06-02 12:30Z, original LONG, London.

This is a modest population difference, not a fixed/system contradiction.

## FIXED-ONE-CONTRACT AUDIT

- Original source validation: 111 sizing evaluations, all recommendations one.
- Baseline chronological inverse: 109 ORB sizing evaluations.
- Diagnostic recommendations: 49 for one contract and 60 for two contracts.
- Submitted ORB quantity: exactly one in all 109 evaluations.
- Chronological attempts reaching execution: 108; ordinary gates suppressed
  the remaining population differences.
- Other strategies retained their normal sizing behavior.

The condition that invalidated the previous specification is therefore
resolved exactly as preregistered.

## CAUSALITY / FILL REALISM

All frozen checks passed:

- exact #358 base and corpus hashes;
- exact 111-attempt stable identity digest;
- completed signal before entry construction;
- resolution starts on the next bar;
- bounded eight-tick IOC caps;
- pessimistic same-bar handling;
- exactly one submitted contract;
- commission and adverse slippage;
- frozen continuous-contract corpus convention.

Thirteen fixed-population trades encountered later bars touching both stop and
target. All were resolved stop-first as preregistered.

## DECISION BASIS

The candidate is positive in both required analyses, both halves, both years,
all four chronological periods, both inverse directions, and all three
sessions. It survives the entire slippage ladder and top-ten-winner removal.
The system path retains 108 of 111 attempts without a new MNQ breaker.

The weak latest system quarter, unrecovered terminal drawdown, MNQ-only scope,
and dominance of fill-selection improvement are explicit limitations. They
are appropriate paper-observation risks, but they do not make the completed
preregistered result mixed or negative.

Classification: **VALIDATED**.

Final decision: **PROMOTE TO PAPER-BUILD CANDIDATE**.

## MINIMUM PAPER-BUILD BOUNDARY

Any later paper build must preserve:

- MNQ only;
- the exact frozen ORB Breakout qualifications;
- inverse direction before IOC evaluation;
- exactly one contract regardless of dynamic sizing recommendation;
- the same eight-tick IOC cap;
- the same static mirrored stop/target geometry;
- the same costs, slippage, and pessimistic ambiguity behavior;
- the normal breaker and ordinary position/account gates; and
- separate diagnostics for recommended versus submitted quantity.

A runtime-parity/build review is still required before implementation. Any
semantic change requires a new preregistration and validation.

No runtime code, #359, #360, Lane B, deployed box, broker, configuration, or
deployment was changed.
