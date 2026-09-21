# Preregistration — TheStrat 2-1-2 reversal reference study v0.1

**Study ID:** `STRAT_REFERENCE_212_REVERSAL`  
**Version:** `strat-ref-212-v0.1`  
**Status:** RESEARCH ONLY / NO EXECUTION AUTHORIZATION

## Question

When implemented from the frozen public-methodology reference, with futures-specific translation choices stated explicitly rather than hidden, does a 2-1-2 **reversal** reference population show reproducible evidence on MNQ/MES?

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

Bar C is the **immediately following 60-minute source bar only**. The setup does not remain armed across later source bars. If that next source bar completes without a valid reversal trigger, the candidate expires unresolved.

## Trigger timing

The reference boundary is frozen at completion of bar B.

Trigger event:
- LONG = first strict trade above bar B high;
- SHORT = first strict trade below bar B low.

Use lower-timeframe bars available in the existing corpus to identify the first causal break. If both boundaries are first crossed within the same lowest-resolution bar and ordering cannot be established, classify `AMBIGUOUS` and do not credit a directional trigger.

Do not wait for the source-timeframe breakout candle to close and then backfill an earlier entry.

The causal watch window is exactly one source bar: from bar B close through the end of the immediately following 60-minute bar. Lower-timeframe evidence outside that window must not trigger the candidate.

## Structural magnitude

For reversal candidates only:
- LONG magnitude = bar A high;
- SHORT magnitude = bar A low.

Primary market-structure outcome is whether magnitude is reached after trigger before structural failure / watch-window termination.

Post-trigger structural failure is a strict break of the opposite inside-bar boundary. If magnitude and structural failure are both first observed within the same 5-minute bar and ordering is unknowable, classify the resolution as ambiguous and do **not** credit the magnitude hit.

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
- MAE from trigger through the first terminal structural event;
- MFE from trigger through the first terminal structural event;
- time to magnitude;
- whether opposite inside-bar boundary is crossed before trigger;
- whether structural failure occurs after trigger before magnitude;
- same-5m-bar resolution ambiguity;
- next-source-bar watch-window unresolved.

For MAE/MFE, the measurement horizon ends at the first of: magnitude hit, post-trigger structural failure, ambiguous same-5m resolution, or the end of the one-source-bar watch window. Price action after that terminal event must not contaminate excursion statistics.

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
- use an explicitly frozen commission + slippage contract;
- use stop-first handling when stop and magnitude/target are both touched inside a bar with unknown path;
- reject impossible/wrong-side brackets;
- use exactly 1 contract for comparability;
- remain paper/research only.

### Cost-model gate

The repository does **not** currently contain one universal research cost standard. Existing studies use different assumptions (including $1.24, $1.48, and $5.00 round-turn commission values, with differing slippage conventions). Therefore this study must not silently inherit whichever script is convenient.

Before Layer B is run, one cost contract must be frozen in this preregistration with:
- round-turn commission per contract;
- whether slippage is charged on entry, exit, or both;
- base adverse slippage ticks;
- stress adverse slippage ticks;
- MNQ/MES tick value source.

Until that contract is frozen, Layer B expectancy/PF/drawdown is **WAIT** and must not be produced.

Base and stress costs must be reported separately once the cost-model gate is resolved.

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

### Futures bar-alignment translation

The public reference does not establish one unique futures-session alignment for a 60-minute candle. The current research harness therefore labels its first implementation explicitly as **RTH session-aligned 60m, anchored at 09:30 ET**. That is an AFS research translation, not a claim that the public methodology mandates this futures alignment.

v0.1 also limits A/B/C construction to bars inside the **same RTH session**. It does not silently bridge the overnight/session gap or the dropped final partial RTH bucket. Consequently this first population is narrower than every possible 60-minute futures 2-1-2; the report must state `A_B_C_same_RTH_session_only`. Cross-session continuity is a separate preregistered variant.

The existing timed 3-2-2 implementation uses clock-specific 7AM/8AM/9AM bars and is a separate variant; it must not be used as silent proof that 09:30 RTH session alignment is canonical.

Before any historical result is treated as evidence about the broader public method, the report must preserve the alignment label. A clock-aligned, ETH, 4HR, or Daily variant requires a new preregistered experiment rather than post-hoc substitution.

## Session rules

Use the existing futures session calendar and record trigger session explicitly.

Do not add session exclusions based on outcome.

Any missing required bar or incomplete source bucket fails closed for that candidate. The immediately following 60-minute watch bucket must be complete at 5-minute resolution; a partial final RTH bucket is not eligible for directional credit.

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
- post-trigger structural failure Y/N;
- same-bar resolution ambiguity Y/N;
- watch-window unresolved Y/N;
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
- event IDs cannot be reproduced deterministically;
- source-bar alignment cannot be stated explicitly enough to reproduce the candidate population.

Any correction to the definition requires a new version before rerun.
