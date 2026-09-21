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

For reporting, `trigger_price` is the frozen **structural boundary** (bar B high/low), not an executable fill assumption. Because MNQ/MES trade on a 0.25-point grid, the minimum feasible strict-break price is one contract tick beyond that boundary. Record that value separately as `minimum_strict_break_price`. Any later execution overlay must begin from at least that strict-break price before applying its frozen adverse-fill assumption; it may not treat the boundary itself as a filled futures price.

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

For MAE/MFE, the full 5-minute **trigger bar is excluded** because its high/low cannot be ordered relative to the intrabar trigger. Excursion measurement begins with the first subsequent 5-minute bar and ends at the first of: magnitude hit, post-trigger structural failure, ambiguous same-5m resolution, or the end of the one-source-bar watch window. If the structural outcome terminates inside the trigger bar, MAE/MFE are reported as unavailable rather than fabricated from unordered OHLC. Price action after the terminal event must not contaminate excursion statistics.

This layer answers whether the documented structural move exists.

### Layer B — explicitly labeled execution overlays
Only after Layer A is generated from the frozen candidate population.

Permitted preregistered overlays:
1. `INSIDE_FAR_SIDE`: opposite side of inside bar.
2. `ENTRY_BAR_FAILURE`: opposite extreme of the lowest-resolution trigger bar.

These are experimental futures overlays, not claimed public doctrine.

No parameter sweeps. No choosing the better stop after results.

With the frozen **5-minute OHLC trigger resolution**, the completed trigger-bar opposite extreme is not knowable at the intrabar trigger instant. Therefore `ENTRY_BAR_FAILURE` is preregistered as **observational-only in v0.1** and must not contribute executable expectancy/PF/drawdown. Only `INSIDE_FAR_SIDE` is eligible for the executable Layer B cell, and only after the cost-model gate is frozen. A future lower-resolution/order-path dataset would require a new version before changing that status.

## Fill realism

Any P&L overlay must:
- enter no earlier than the causal trigger and never model a fill at the structural boundary itself;
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

v0.1 also limits A/B/C construction to bars inside the **same RTH session**. It does not silently bridge the overnight/session gap or the dropped final partial RTH bucket. Consequently this first population is narrower than every possible 60-minute futures 2-1-2; the report must state `A_B_C_same_RTH_session_only_full_RTH_non_roll`. Cross-session continuity is a separate preregistered variant.

The existing timed 3-2-2 implementation uses clock-specific 7AM/8AM/9AM bars and is a separate variant; it must not be used as silent proof that 09:30 RTH session alignment is canonical.

Before any historical result is treated as evidence about the broader public method, the report must preserve the alignment label. A clock-aligned, ETH, 4HR, or Daily variant requires a new preregistered experiment rather than post-hoc substitution.

## Session rules

v0.1 uses the same conservative whole-RTH eligibility convention already used by the futures research harness:
- 09:30–16:00 ET RTH only;
- exactly 78 unique, contiguous 5-minute bars are required;
- holiday/early-close/partial sessions that do not supply the full 78-bar RTH window are ineligible rather than silently padded;
- existing futures-research roll exclusions apply: Mon–Fri of the third-Friday week in Mar/Jun/Sep/Dec plus the next available session;
- A/B/C must remain inside the same eligible RTH session.

Record the trigger session explicitly. Do not add session exclusions based on outcome.

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
- completed bar C timestamp/type when the full next-source-bar window is available;
- frozen trigger high/low;
- trigger timestamp;
- trigger direction;
- structural trigger-boundary price and role;
- minimum strict-break price from the contract tick grid;
- ambiguous flag;
- parent magnitude;
- structural-magnitude distance from the minimum strict-break price;
- whether the magnitude remains on the executable side of that minimum strict-break price;
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
- shared AFS classifier sequence/direction on completed bar C, when available;
- explicit status showing whether the executable AFS path has actually been compared;
- session;
- H1/H2;
- data-completeness flags;
- each allowed overlay outcome/cost cell.

Aggregate:
- candidate count;
- ambiguous rate;
- magnitude-hit rate;
- time-to-magnitude p25/p50/p75/p90 using deterministic linear interpolation on sorted observations;
- MAE/MFE p25/p50/p75/p90 using the same deterministic linear interpolation;
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

The shared classifier comparison may be computed from completed A/B/C bars without invoking executable logic. That **does not** prove the current executable futures path would have generated the same trade, because the existing futures `strat_212` implementation is continuation-only and may operate under different runtime context/gates. Report classifier-only evidence separately from executable-path evidence and leave the latter unavailable until actually reproduced.

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
