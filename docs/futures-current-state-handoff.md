# Futures — Current State Handoff

_As of 2026-09-09. This is the single current futures handoff. Do not recreate completed infrastructure audits, redo merged fixes, or discard a strategy family from one losing implementation without first isolating the actual failure mode._

## Verdict

**PAPER ONLY / STRATEGY FAILURE-MODE AUDIT ACTIVE / NO DEPLOYMENT CHANGE.**

The futures infrastructure remains in the paper-only posture. The current strategy question is no longer simply “which historical rows were negative?” The active research task is to determine whether each STRAT family is failing because of the **timeframe, entry, stop, target, filter, or execution contract** before classifying the family itself as broken.

No runtime, risk configuration, broker route, deployment, or strategy enablement is authorized by the evidence below.

## Repository / runtime checkpoint

- Repository `main` at this handoff update: `febb7bb2d03428a1854dc4da08310db05e9643b8` after PR #532.
- The deployed bot/runtime was **not revalidated during this strategy research pass**. Do not infer that repository `main` is deployed.
- Previously completed Pine parity, replay routing, watcher, memory-pressure, and Tradovate exact-account-routing repairs remain completed work. Do not redo them without a new reproducible defect.
- Existing paper safety posture remains unchanged.

## New research corpus available

The previously gitignored 5-minute Polygon corpus is now available for research:

- MNQ: 621 trading-day files, about 140,115 deduplicated 5-minute rows.
- MES: 621 trading-day files, about 140,111 deduplicated 5-minute rows.
- Coverage spans the existing 2024-2026 replay population.

This removes the prior blocker that prevented the Daily STRAT baseline from being run outside the local checkout.

## Causal STRAT research contract

The current independent failure-mode study uses:

- completed prior timeframe bars only;
- causal first-side boundary break for the forming STRAT bar;
- ambiguous same-5m dual-side breaks fail closed;
- prior-bar break entry one tick beyond the boundary;
- opposite side of the prior bar as the untouched structural stop baseline;
- 2R untouched structural target baseline;
- one contract;
- one position at a time per research lane;
- 1 adverse tick on market entry and stop fills;
- target as a resting limit fill;
- pessimistic stop-first resolution when OHLC cannot establish intrabar order;
- $1.48 round-turn commission;
- H1/H2 and calendar-month stability checks;
- no lookahead from the final OHLC of a still-forming Daily/4H/60m bar.

### Required repair order

For a weak or blocked strategy, change **one variable at a time**:

1. baseline;
2. stop;
3. target;
4. entry;
5. filter;
6. timeframe;
7. slippage / execution stress on any surviving variant.

Do not call a family broken merely because the current stop cap, target, filter, or timeframe makes it lose. A changed variant must still survive multiple periods before it can be retained.

## Current STRAT evidence — new causal study

### MNQ Daily 2-2 continuation — PROMISING BUT UNPROVEN

Untouched structural Daily bracket, one-position lane:

- 46 resolved trades;
- 22 wins / 24 losses;
- net **+$21,712.42**;
- PF **2.01**;
- H1 **+$9,834.46 / PF 1.84**;
- H2 **+$11,877.96 / PF 2.22**;
- 13 positive months / 7 negative months;
- positive in 2024, 2025, and 2026.

Failure-mode finding:

- median original stop is about **1,412 ticks**;
- **0/211** candidates fit the current MNQ 120-tick stop cap;
- 120-, 300-, 500-, 800-, and 1,000-tick stop-only caps fail to preserve positive H1/H2 simultaneously.

**Interpretation:** the signal population is not currently disproven. The binding problem is the **Daily stop/risk architecture**. Do not tighten the stop merely to force system compatibility; every tested tighter cap damaged stability.

Important sequence slice:

- the same-direction **2-2-2 continuation** slice is not the source of this edge; its standalone Daily baseline is negative on both MNQ and MES under the current contract.
- Treat 2-2-2 continuation as its own failure-mode question before discarding or promoting it.

### MES Daily 3-2 — PROMISING BUT FRAGILE

Untouched Daily structural bracket:

- 23 resolved trades;
- net **+$10,132.21**;
- PF **2.31**;
- both halves positive.

Stop-only change to the current MES 60-tick cap:

- 55 resolved trades;
- 5 wins / 50 losses;
- net **+$3,079.85**;
- PF **1.79**;
- H1 **+$2,151.29 / PF 2.15**;
- H2 **+$928.56 / PF 1.46**.

Slippage stress with that same 60-tick stop remains positive through 4 adverse ticks:

- 1 tick: **+$3,079.85 / PF 1.79**;
- 2 ticks: **+$3,011.10 / PF 1.76**;
- 3 ticks: **+$2,942.35 / PF 1.73**;
- 4 ticks: **+$2,873.60 / PF 1.71**.

**Major fragility:** only 5 of 23 active months are positive and the largest winner is about 66% of total net profit. Therefore this is **not validated** despite surviving the cap/slippage tests. The next work is concentration/outlier robustness, not strategy promotion.

### 4H 2-2 reversal — PROMISING BUT UNPROVEN

The 4H timeframe is materially stronger and more stable than the Daily implementation, especially on MES.

MNQ, untouched 4H structural bracket:

- 261 resolved;
- **+$11,856.72 / PF 1.27**;
- H1 and H2 positive.

MNQ stop-only 500-tick cap:

- 299 resolved;
- **+$9,612.98 / PF 1.23**;
- H1 **+$4,861.48 / PF 1.23**;
- H2 **+$4,751.50 / PF 1.22**;
- remains **+$9,038.48 / PF 1.21** at 4 adverse ticks;
- 13 positive months / 11 negative months.

The current MNQ 120-tick cap makes the same 4H population negative. A 300-tick cap is positive in both halves but weaker; 500 ticks is materially stronger in this independent study.

MES, untouched 4H structural bracket:

- 263 resolved;
- **+$9,755.76 / PF 1.43**;
- H1 and H2 positive.

MES stop-only 120-tick cap:

- 297 resolved;
- **+$6,887.94 / PF 1.31**;
- H1 **+$5,385.96 / PF 1.51**;
- H2 **+$1,501.98 / PF 1.13**;
- remains **+$5,915.21 / PF 1.25** at 4 adverse ticks;
- 17 positive months / 7 negative months.

The current MES 60-tick cap is only weakly positive overall and has a negative half, so it fails the stability screen.

**Interpretation:** do not classify “2-2 reversal” as globally broken. The evidence says **timeframe and stop width materially change the result**. 4H deserves continued robustness work; no runtime change follows from this research result.

### Daily 2-2 reversal / 2-2-2 reversal slice — mixed, not ready

MNQ Daily generic 2-2 reversal baseline is only marginal:

- 48 resolved;
- **+$3,084.96 / PF 1.13**;
- H1 barely positive, H2 positive;
- drawdown about **$9,331.50**.

Entry confirmation did not improve it. Tested tighter stop caps damage later-period stability. A relative-volume >=0.8 filter improves the independent baseline to **+$5,179.84 / PF 1.26** with both halves positive, but this remains a filter hypothesis, not an approved strategy.

MES Daily 2-2 reversal baseline is negative:

- 42 resolved;
- **-$2,955.91 / PF 0.78**;
- H2 strongly negative.

Stop-only, target-only, entry-confirmation, and the tested existing-context filters do not produce a stable Daily MES repair. The same pattern is materially stronger on 4H, so **Daily timeframe is part of the MES failure mode**.

The directional-two-back 2-2-2 reversal slice maps into this generic canonical 2-2 reversal population in the current classifier; it is not independently promoted.

### Daily MNQ 3-2 — WAIT / REGIME-UNSTABLE

- baseline: **-$3,843.88 / PF 0.84**;
- H1 positive, H2 strongly negative;
- stop-only variants can create positive full-period totals but H2 remains negative;
- 1R and 1.5R target-only variants become positive overall but H2 remains negative;
- entry confirmation does not improve it;
- TREND/FTFC/relative-volume filters tested so far do not rescue H2;
- 4H is negative and 60m is approximately flat/unstable under the same pattern contract.

**Interpretation:** no stable repair has been found yet. This is much closer to a genuine rejection than the families above, but keep it WAIT until the predefined failure-mode matrix is fully documented and reproduced.

### Daily 3-2-2 reversal — WAIT / SAMPLE + WALK-FORWARD PROBLEM

Daily MNQ:

- 17 resolved;
- **+$1,341.34 / PF 1.15**;
- H1 negative, H2 positive.

Daily MES:

- 15 resolved;
- **+$1,547.80 / PF 1.38**;
- H1 negative, H2 positive.

Stop, target, and entry variants do not remove the Daily half-to-half instability. Sample size is also too small.

### MNQ 60m 3-2-2 reversal — PROMISING RESEARCH CONFIRMATION, NOT A NEW DEPLOYMENT CLAIM

Independent causal 60m evidence supports the existing conclusion that this family should not be discarded solely because the current MNQ 120-tick cap is too tight.

Untouched structural bracket:

- 284 resolved;
- **+$7,403.68 / PF 1.24**;
- both halves positive.

Stop-only 500-tick cap:

- 291 resolved;
- **+$9,879.32 / PF 1.37**;
- H1 **+$4,848.90 / PF 1.37**;
- H2 **+$5,030.42 / PF 1.37**;
- remains **+$9,221.32 / PF 1.34** at 4 adverse ticks;
- 17 positive months / 7 negative months.

300 ticks is also positive in both halves but materially weaker. The current 120-tick cap is negative.

This corroborates the existing wide-stop 60M 3-2-2 research direction; it does **not** supersede the dedicated engine-parity/IOC evidence or authorize a cap change.

MES 60m 3-2-2 does not show the same stability because H2 is negative.

## Existing non-STRAT evidence — preserve prior classifications unless new causal evidence supersedes them

- ORB Reclaim current/first_cross — negative historical evidence.
- ORB Reclaim V4-R — WAIT.
- Inverse ORB / VWAP evidence with invalid bracket geometry must not be used as execution proof.
- 4HR Re-Trigger, Miyagi, and dedicated 60M 3-2-2 work should be interpreted by their latest family-specific audit artifacts, not by stale broad labels from the September 6 handoff.
- MES `strat_122` has separate current-engine research on main; do not duplicate that lane inside this Daily audit.

## Current safety posture

- Paper only.
- No broker/deployment behavior change from this handoff.
- Max 3 trades/day remains the currently configured isolated-lane cap.
- One open position at a time remains the current global position rule.
- No averaging down.
- Bracket/stop requirements remain in force.
- No optimistic same-bar fills.
- Do not tune several variables together and call the result evidence.
- Do not promote a result that depends on one period, one outlier, or a tiny sample.

## Research tooling status

Draft PR #527 (`chatgpt/daily-strat-failure-mode-baseline`) is **research only** and must not be merged for execution.

Its first CI run exposed a test-code defect: the test compared the returned `StratContext` object directly with a string instead of checking `.strat_sequence`. The classifier itself returned the expected `strat_22_continuation`. Fix that test before treating #527 as CI-clean.

The current independent corpus results above were produced from the uploaded 5-minute corpus; they should be committed as evidence only after the harness/test contract is corrected and reproduced.

## Smallest safe next step

Continue the strategy audit, not the runtime:

1. fix #527's test assertion and the causal first-side pattern labeling in the research harness;
2. reproduce the uploaded-corpus results with the corrected harness;
3. stress **4H 2-2 reversal**, **MNQ Daily 2-2 continuation**, and **MNQ 60m 3-2-2** for month/outlier/risk concentration without changing another strategy variable;
4. stress **MES Daily 3-2** specifically for winner concentration because its P&L is carried by very few wins;
5. finish the one-variable matrix for the weak Daily families before assigning BROKEN;
6. make no strategy/runtime/config change unless a surviving variant passes the same causal and multi-period evidence contract.
