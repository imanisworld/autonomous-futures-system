# Futures — Current State Handoff

_As of 2026-09-09. This is the single current futures handoff. Do not recreate completed infrastructure audits, redo merged fixes, or discard a strategy family from one losing implementation without first isolating the actual failure mode._

## Verdict

**PAPER ONLY / STRATEGY FAILURE-MODE AUDIT ACTIVE / NO DEPLOYMENT CHANGE.**

The current research task is to determine whether each strategy family is failing because of the **timeframe, entry, stop, target, filter, or execution contract** before classifying the family itself as broken. No runtime, risk configuration, broker route, deployment, or strategy enablement is authorized by the evidence below.

## Repository / runtime checkpoint

- Repository `main` at this handoff refresh: `febb7bb2d03428a1854dc4da08310db05e9643b8` after PR #532.
- The deployed bot/runtime was **not revalidated during this research pass**. Do not infer that current `main` is deployed.
- Previously completed Pine parity, replay routing, watcher, memory-pressure, and Tradovate exact-account-routing repairs remain completed work. Do not redo them without a new reproducible defect.
- Current paper safety posture remains unchanged.

## Research corpus now available

The previously gitignored Polygon 5-minute corpus is available for the strategy audit:

- MNQ: 621 files, about 140,115 deduplicated 5-minute rows.
- MES: 621 files, about 140,111 deduplicated 5-minute rows.
- Coverage spans the existing 2024-2026 replay population.

## Causal research contract

The current independent study uses:

- completed prior timeframe bars only;
- causal **first prior-range side broken** to identify the forming STRAT bar;
- ambiguous same-5m dual-side breaks fail closed;
- prior-bar break entry one tick beyond the boundary;
- opposite side of the prior bar as the untouched structural stop baseline;
- 2R untouched structural target baseline;
- one contract;
- one position at a time per research lane;
- 1 adverse tick on market entry and stop fills;
- target as a resting limit fill;
- pessimistic stop-first treatment when OHLC cannot establish intrabar order;
- $1.48 round-turn commission;
- multi-bar/multi-day resolution where the timeframe requires it;
- H1/H2, month, year, drawdown, slippage, and outlier checks;
- no lookahead from the final OHLC of a still-forming Daily/4H/60m bar.

### One-variable failure-mode order

For a weak or blocked strategy:

1. reproduce the baseline;
2. change stop only;
3. change target only;
4. change entry only;
5. change filter only;
6. change timeframe only;
7. stress any survivor for slippage, concentration, and period stability.

A family is not BROKEN merely because one implementation loses. A changed variant is retained only if it improves results without losing multi-period stability.

## Critical 4H alignment correction

An initial independent 4H study grouped 4H bars relative to the 18:00 ET session. That reconstruction matched the replay corpus's stored `four_hour_bar_type` only about 52% of the time and is **invalid evidence**.

The repository's actual Polygon replay converter builds 240-minute bars in **fixed UTC-aligned buckets** (`start = (ts // width) * width`). Reconstructing 4H bars that way matches the stored replay `four_hour_bar_type` **100% on both MNQ and MES** in the checked corpus.

Therefore **all session-aligned 4H P&L numbers are discarded**. Only the corrected fixed-UTC 4H results below may be used.

## Current STRAT evidence

### MNQ Daily 2-2 continuation — PROMISING BUT UNPROVEN

Untouched Daily structural bracket, one-position lane:

- 46 resolved trades; 22 wins / 24 losses;
- net **+$21,712.42**;
- PF **2.01**;
- H1 **+$9,834.46 / PF 1.84**;
- H2 **+$11,877.96 / PF 2.22**;
- 13 positive months / 7 negative months;
- positive in 2024, 2025, and 2026.

Outlier removal remains positive:

- remove largest winner: about **+$18.1k**;
- remove top 3 winners: about **+$12.0k**;
- remove top 5 winners: about **+$6.9k**.

Failure-mode finding:

- median original stop about **1,412 ticks**;
- **0/211** candidates fit the current MNQ 120-tick stop cap;
- 120-, 300-, 500-, 800-, and 1,000-tick stop-only caps do not preserve positive H1/H2 simultaneously.

**Interpretation:** the signal population is not currently disproven. The binding problem is the **Daily stop/risk architecture**. Tightening the stop simply to fit the current cap destroys stability.

The same-direction **2-2-2 continuation** slice is negative/unstable on both MNQ and MES and is not the source of the broader MNQ Daily 2-2 continuation edge.

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

That same 60-tick variant remains positive through 4 adverse slippage ticks: about **+$2,873.60 / PF 1.71** at 4 ticks.

However the edge is highly concentrated:

- only 5 of 23 active months are positive;
- largest winner is about 66% of full-period net;
- removing the top 2 winners makes the result negative;
- removing the top 3 makes it more negative.

**Interpretation:** positive but too outlier-dependent to validate. The next work is causal context decomposition / outlier robustness, not promotion.

### MNQ 4H 2-2 reversal — PROMISING BUT UNPROVEN

These are the **corrected fixed-UTC 4H results**.

Untouched structural bracket:

- 256 resolved;
- net **+$8,494.62**;
- PF **1.20**;
- H1 **+$7,187.06 / PF 1.41**;
- H2 **+$1,307.56 / PF 1.05**.

The current MNQ 120-tick cap destroys the edge:

- **-$1,790.70 / PF 0.91**;
- H1 negative.

A broad stop-only plateau from roughly **300 to 800 ticks** stays positive in both halves rather than relying on one magic setting. Examples:

- 300 ticks: **+$9,867.48 / PF 1.31**;
- 400 ticks: **+$13,416.52 / PF 1.39**;
- 500 ticks: **+$10,411.80 / PF 1.28**;
- 600 ticks: **+$11,270.78 / PF 1.29**;
- 800 ticks: **+$9,419.04 / PF 1.23**.

Those representative variants remain positive through 4 adverse slippage ticks.

At 400 ticks:

- 16 positive / 8 negative months;
- 2024 slightly negative, 2025 and 2026 positive;
- removing the top 5 winners still leaves about **+$6.2k**.

At 600 ticks:

- 16 positive / 8 negative months;
- 2024, 2025, and 2026 are positive;
- removing the top 5 winners still leaves about **+$4.1k**.

**Interpretation:** the 4H signal is promising and the current 120-tick cap is a clear blocker, but selecting a single cap from this same sample would be post-hoc optimization. The broad plateau should be preregistered and confirmed, not tuned to the best cell.

### MES 4H 2-2 reversal — WAIT / NO REPAIR FOUND

The earlier claim that MES 4H 2-2 reversal was positive came from the invalid session-aligned reconstruction and is withdrawn.

Correct fixed-UTC baseline:

- 273 resolved;
- net **-$1,142.79**;
- PF **0.95**;
- H1 slightly positive;
- H2 **-$1,611.51 / PF 0.88**.

Stop-only caps from 60 through 600 ticks remain negative overall or unstable. No corrected 4H stop width tested rescues MES.

**Interpretation:** 4H does **not** rescue MES 2-2 reversal. Combined with the negative Daily result and weak other timeframe evidence, MES 2-2 reversal is moving toward rejection, but remains WAIT until the predefined matrix is completely reproduced/documented.

### Daily 2-2 reversal — mixed by instrument

MNQ Daily generic 2-2 reversal:

- 48 resolved;
- **+$3,084.96 / PF 1.13**;
- H1 barely positive, H2 positive;
- drawdown about **$9.3k**.

Entry confirmation does not improve it. Tighter stop caps damage later-period stability. A relative-volume >=0.8 filter improves the independent baseline to about **+$5,179.84 / PF 1.26** with both halves positive, but this is a filter hypothesis requiring independent confirmation.

MES Daily generic 2-2 reversal:

- 42 resolved;
- **-$2,955.91 / PF 0.78**;
- H2 strongly negative;
- stop-only, target-only, entry-confirmation, and tested existing-context filters do not produce a stable repair.

The directional-two-back 2-2-2 reversal slice maps into this canonical generic 2-2 reversal population and is not independently promoted.

### MNQ Daily 3-2 — WAIT / WALK-FORWARD UNSTABLE

- baseline **-$3,843.88 / PF 0.84**;
- H1 positive, H2 strongly negative;
- some stop-only or 1R/1.5R target-only variants make the full-period total positive, but **H2 remains negative**;
- entry confirmation does not improve it;
- tested TREND/FTFC/relative-volume filters do not rescue H2;
- alternate timeframe checks so far do not produce a stable replacement.

**Interpretation:** no stable repair has been found. This is close to rejection but remains WAIT until the matrix is fully reproduced.

### Daily 3-2-2 reversal — WAIT / SAMPLE + WALK-FORWARD PROBLEM

MNQ Daily:

- 17 resolved;
- **+$1,341.34 / PF 1.15**;
- H1 negative, H2 positive.

MES Daily:

- 15 resolved;
- **+$1,547.80 / PF 1.38**;
- H1 negative, H2 positive.

Stop, target, and entry variants do not remove the basic Daily half-to-half instability, and samples are too small.

### MNQ 60m 3-2-2 reversal — PROMISING BUT UNPROVEN

Independent causal 60m evidence supports the existing wide-stop-family conclusion.

Untouched structural bracket:

- 284 resolved;
- **+$7,403.68 / PF 1.24**;
- both halves positive.

The current MNQ 120-tick cap makes the population negative: about **-$5,974.82 / PF 0.64**.

A broad stop-only plateau, rather than one isolated cap, is positive. Representative results:

- 300 ticks: about **+$3.1k / PF 1.12**;
- 400 ticks: about **+$6.5k / PF 1.24**;
- 500 ticks: **+$9,879.32 / PF 1.37**;
- 600 ticks: about **+$10.1k / PF 1.37**;
- 800 ticks: about **+$8.5k / PF 1.30**.

At 500 ticks:

- H1 **+$4,848.90 / PF 1.37**;
- H2 **+$5,030.42 / PF 1.37**;
- 4-tick slippage still **+$9,221.32 / PF 1.34**;
- 17 positive / 7 negative months;
- 2024, 2025, and 2026 all positive;
- removing the top 5 winners still leaves about **+$4.5k**.

**Interpretation:** the current 120-tick cap is a clear mismatch for this family. This corroborates the dedicated 60M 3-2-2 wide-stop research; it does **not** supersede its engine-parity/IOC evidence or authorize a config change.

MES 60m 3-2-2 does not show the same stability because H2 is negative.

## Existing non-STRAT evidence — preserve latest family-specific audits

- ORB Reclaim current/first_cross — negative historical evidence.
- ORB Reclaim V4-R — WAIT.
- Inverse ORB / VWAP evidence with invalid bracket geometry must not be used as execution proof.
- 4HR Re-Trigger, Miyagi, and dedicated 60M 3-2-2 work should be interpreted by their latest family-specific audit artifacts, not stale broad labels from the September 6 handoff.
- MES `strat_122` has separate current-engine research on `main`; do not duplicate that lane inside this audit.

## Current safety posture

- Paper only.
- No broker/deployment behavior change from this handoff.
- Max 3 trades/day remains the currently configured isolated-lane cap.
- One open position at a time remains the current global rule.
- No averaging down.
- Bracket/stop requirements remain in force.
- No optimistic same-bar fills.
- Do not tune several variables together and call the result evidence.
- Do not promote a result dependent on one period, one outlier, or a tiny sample.

## Research tooling status

Draft PR #527 (`chatgpt/daily-strat-failure-mode-baseline`) is **research only** and is not yet trustworthy as the canonical evidence runner.

Three defects were identified before merge:

1. its first CI test compares a returned `StratContext` object directly to a string instead of checking `.strat_sequence`;
2. its candidate detector independently searches both boundaries and can mislabel a later opposite-side break as another setup instead of assigning the forming STRAT bar from the first boundary broken;
3. its Daily resolver stops at the end of the trigger session, while a Daily structural stop/2R target can require multi-day resolution and the lane must enforce one-position-at-a-time across that horizon.

The independent uploaded-corpus results above therefore remain **research evidence pending reproduction through a corrected #527-style harness**. Do not merge #527 merely to make CI green.

## Smallest safe next step

Continue the audit, not the runtime:

1. correct #527's causal pattern identification, multi-day resolution, one-position lane, and test assertion;
2. reproduce the uploaded-corpus Daily results with the corrected harness;
3. preregister broad stop ranges rather than selecting the best historical cell for **MNQ 4H 2-2 reversal** and **MNQ 60m 3-2-2**;
4. investigate **MNQ Daily 2-2 continuation** with diagnostics that explain why winners require such wide structural adverse excursion before changing another variable;
5. decompose **MES Daily 3-2** winner concentration before deciding whether a causal filter exists or the apparent edge is just outlier dependence;
6. finish the weak-family matrix before assigning BROKEN;
7. make no runtime/config change unless a surviving variant passes the same causal, realistic-fill, and multi-period contract.
