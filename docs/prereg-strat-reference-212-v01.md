# Preregistration — TheStrat 2-1-2 reversal reference study v0.1

**Study ID:** `STRAT_REFERENCE_212_REVERSAL`  
**Version:** `strat-ref-212-v0.1`  
**Status:** RESEARCH ONLY / NO EXECUTION AUTHORIZATION

## Question

When implemented from the frozen public-methodology reference rather than the existing AFS continuation/fixed-2R geometry, does a literal 2-1-2 **reversal** population show reproducible evidence on MNQ/MES?

This is not a test of every TheStrat setup and is not a promotion study.

## Instruments and data

- MNQ
- MES
- existing historical 5-minute replay corpus only
- no new instrument expansion
- no optimization against outcomes
- exact available date range must be recorded by the runner before results are calculated

## Pattern definition

A candidate requires three consecutive source-timeframe bars:

Bullish reversal:
- bar A = 2D
- bar B = 1
- bar C first breaks above bar B high

Bearish reversal:
- bar A = 2U
- bar B = 1
- bar C first breaks below bar B low

Bar identity uses strict high/low breaks against the immediately prior bar.

The inside bar must be complete before the trigger window begins.

## Trigger timing

The reference boundary is frozen at completion of bar B.

Trigger event:
- LONG = first strict trade above bar B high;
- SHORT = first strict trade below bar B low.

Use lower-timeframe bars available in the existing corpus to identify the first causal break. If both boundaries are first crossed within the same lowest-resolution bar and ordering cannot be established, classify `AMBIGUOUS` and do not credit a directional trigger.

Do not wait for the source-timeframe breakout candle to close and then backfill an earlier entry.

## Structural magnitude

For reversal candidates only:
- LONG magnitude = bar A high;
- SHORT magnitude = bar A low.

Primary market-structure outcome is whether magnitude is reached after trigger before structural failure / session termination.

Do not replace this magnitude with 2R.

## Risk / stop handling

The public docs do not provide one futures-specific universal stop translation.

Therefore v0.1 must **not invent a single stop and call it canonical**.

Report two layers:

### Layer A — structure study
No P&L claim. Measure:
- trigger count;
- magnitude hit;
- magnitude miss;
- MAE from trigger;
- MFE from trigger;
- time to magnitude;
- whether opposite inside-bar boundary is crossed first;
- session-end unresolved.

This layer answers whether the documented structural move exists.

### Layer B — explicitly labeled execution overlays
Only after Layer A is generated from the frozen candidate population.

Permitted preregistered overlays:
1. `INSIDE_FAR_SIDE`: opposite side of inside bar.
2. `ENTRY_BAR_FAILURE`: opposite extreme of the lowest-resolution trigger bar, only if causally knowable without lookahead.

These are experimental futures overlays, not claimed public doctrine.

No parameter sweeps. No choosing the better stop after results.

If `ENTRY_BAR_FAILURE` cannot be applied causally at order time, report it observationally only and do not use it for executable expectancy.

## Fill realism

Any P&L overlay must:
- enter no earlier than the causal trigger;
- apply adverse slippage and commission assumptions already standardized by the futures research harness;
- use stop-first handling when stop and magnitude/target are both touched inside a bar with unknown path;
- reject impossible/wrong-side brackets;
- use exactly 1 contract for comparability;
- remain paper/research only.

Base and stress costs must be reported separately.

## FTFC

Compute TheStrat FTFC separately from AFS EMA trend.

Standard reference state at trigger:
- UP if current price is above monthly, weekly, daily, and current 60m opens;
- DOWN if below all four;
- CONFLICT otherwise.

**v0.1 does not filter candidates by FTFC.** It reports results stratified by FTFC state.

Reason: filtering now would add a new selection gate before we know whether it contributes information.

The existing AFS EMA-stack trend state should also be recorded for side-by-side comparison, never substituted for FTFC.

## Source timeframe

Do not silently test every timeframe.

First source timeframe: **60 minute**.

Reason:
- objective completed-bar construction;
- public docs specifically discuss 60m as a meaningful setup timeframe;
- existing AFS timed 60m infrastructure makes identity auditing feasible.

Any 4HR/Daily variant is a new preregistered experiment.

## Session rules

Use the existing futures session calendar and record trigger session explicitly.

Do not add session exclusions based on outcome.

Any missing required bar or incomplete source bucket fails closed for that candidate.

## Splits

Use a chronological H1/H2 split frozen by the exact corpus midpoint date emitted before outcomes are summarized.

Report MNQ and MES independently and pooled only as supplemental.

## Required outputs

Per event:
- deterministic event ID;
- instrument;
- source timeframe;
- bar A/B timestamps and types;
- frozen trigger high/low;
- trigger timestamp;
- trigger direction;
- trigger price;
- ambiguous flag;
- parent magnitude;
- magnitude reached Y/N;
- time to magnitude;
- MAE;
- MFE;
- opposite boundary first Y/N;
- FTFC state;
- AFS EMA-trend state;
- session;
- H1/H2;
- data-completeness flags;
- each allowed overlay outcome/cost cell.

Aggregate:
- candidate count;
- ambiguous rate;
- magnitude-hit rate;
- median/quantile time-to-magnitude;
- MAE/MFE quantiles;
- results by MNQ/MES;
- H1/H2;
- LONG/SHORT;
- FTFC UP/DOWN/CONFLICT;
- session;
- execution-overlay expectancy/PF/drawdown only where causally executable.

## Comparison against existing AFS interpretation

On the same event IDs, report whether the current AFS classifier/path would:
- identify the sequence;
- label it continuation/reversal/other;
- generate a trade candidate;
- use a different trigger;
- use a different stop;
- use a different target;
- block due to EMA trend or another gate.

This is diagnostic only. Do not alter existing executable modules during v0.1.

## Interpretation gate

Possible classifications after audit:

- **BROKEN**: causal/reference identity is wrong, lookahead exists, or evidence cannot be trusted.
- **WAIT**: insufficient sample or unresolved data/fill problem.
- **PROMISING BUT UNPROVEN**: structure and executable overlay survive H1/H2, costs and instrument checks but no forward proof exists.
- **OVERFIT**: result depends on a narrow concentration or post-hoc choice.
- **VALIDATED** is **not available from this historical study alone**.

No paper/demo activation follows directly from this study.

## Stop conditions

Stop the study and classify BROKEN/WAIT rather than patching results if:
- source-timeframe bars are not reproducible;
- trigger ordering requires unavailable intrabar data;
- live/replay bar identity differs;
- missing-data handling selectively drops losing-looking cases;
- event IDs cannot be reproduced deterministically.

Any correction to the definition requires a new version before rerun.
