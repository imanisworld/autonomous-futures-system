# Futures — Current State Handoff

_As of 2026-09-09. This is the single current futures handoff. Do not recreate completed infrastructure audits, redo merged fixes, or discard a strategy family from one losing implementation without first isolating the actual failure mode._

## Verdict

**PAPER ONLY / STRATEGY FAILURE-MODE AUDIT ACTIVE / NO DEPLOYMENT CHANGE.**

The active research question is whether each strategy fails because of **timeframe, entry, stop, target, filter, or execution contract**. A family is not BROKEN merely because one implementation loses. No runtime, risk, broker, deployment, or strategy-enablement change is authorized by this handoff.

## Repo / safety checkpoint

- Repository `main` at this refresh: `febb7bb2d03428a1854dc4da08310db05e9643b8` after PR #532.
- Deployed runtime was not revalidated in this research pass; do not assume current `main` is deployed.
- Completed Pine/replay/watcher/memory/Tradovate routing fixes remain completed work; do not redo them without a new reproducible defect.
- Current risk config remains paper-only, max 3 trades/day, one open position, MNQ max stop 120 ticks, MES max stop 60 ticks, $150 daily loss cap.

## Research corpus

The previously gitignored Polygon 5-minute corpus is now available:

- MNQ: 621 files, about 140,115 deduplicated 5-minute rows.
- MES: 621 files, about 140,111 deduplicated 5-minute rows.
- Coverage spans the existing 2024-2026 replay population.

## Causal research contract

Current independent studies use:

- completed prior timeframe bars only;
- the **first prior-range side broken** to identify the forming STRAT bar;
- ambiguous same-5m dual-side breaks fail closed;
- prior-bar break entry + one tick;
- opposite side of prior bar as untouched structural stop;
- untouched 2R target baseline;
- one contract and one position at a time per lane;
- 1 adverse tick on market entry and stop exit; target fills as resting limit;
- pessimistic stop-first handling when OHLC order is unknowable;
- $1.48 round-turn commission;
- multi-day resolution when the timeframe requires it;
- H1/H2, month, year, drawdown, slippage, and outlier checks.

Failure-mode order is **baseline → stop → target → entry → filter → timeframe → execution stress**, changing one variable at a time. Standard timeframe comparison is limited to **15m, 60m, 4H, Daily** rather than mining arbitrary intervals.

## Critical 4H alignment correction

The first independent 4H reconstruction incorrectly anchored 4H bars to the 18:00 ET session. It matched stored replay `four_hour_bar_type` only about 52% of the time, so those P&L results are invalid and discarded.

The repo's Polygon converter actually builds 240-minute bars in **fixed UTC buckets** (`start = (ts // width) * width`). Reconstructing them that way matches stored replay `four_hour_bar_type` **100% on MNQ and MES** in the checked corpus.

Only fixed-UTC 4H evidence below is valid.

## Current findings

### MNQ Daily 2-2 continuation — PROMISING SIGNAL / UNSAFE FOR CURRENT RISK

Untouched Daily structural bracket:

- 46 resolved, 22W / 24L;
- **+$21,712.42 / PF 2.01**;
- H1 **+$9,834.46 / PF 1.84**;
- H2 **+$11,877.96 / PF 2.22**;
- 13 positive / 7 negative months;
- 2024, 2025, 2026 all positive;
- removing top five winners still leaves about **+$6.9k**.

The exact failure mode is increasingly clear:

- median original stop ~**1,412 ticks** (~$706 MNQ risk per contract);
- current 120-tick stop is $60 risk;
- **18/22 eventual winners (82%) first travel more than 120 ticks against entry**;
- median winner MAE ~**732 ticks**;
- median winning trade takes about **334 hours (~14 days)** to resolve;
- stop-only reductions to 30-60% of the Daily structural stop lose recent-half stability;
- a broad **70-90% of original structural stop** retains both-half profitability, confirming that the strategy genuinely needs most of the Daily range as breathing room.

The existing relative-volume >=0.8 gate improves the untouched wide-stop population, but attempting to make the strategy risk-compatible with a 500-tick stop produces only a narrow/fragile result: 2026 is slightly negative and removing the top three winners turns it negative. Smaller stops fail more clearly.

**Conclusion:** this is a real-looking swing signal, not an intraday-risk strategy. No robust current-account-compatible stop has been found. Do not throw away the signal, but do not paper-enable it under current risk rules.

The same-direction **2-2-2 continuation** slice is negative/unstable on MNQ and MES and is not the source of the broader MNQ Daily 2-2 continuation edge.

### MNQ 4H 2-2 reversal — PROMISING BUT UNPROVEN

Correct fixed-UTC baseline:

- 256 resolved;
- **+$8,494.62 / PF 1.20**;
- H1 **+$7,187.06 / PF 1.41**;
- H2 **+$1,307.56 / PF 1.05**.

The current 120-tick cap destroys the edge: about **-$1.79k / PF 0.91**.

A broad 300-800 tick stop plateau stays positive in both halves. The 300-tick cell is especially relevant because 300 MNQ ticks is about **$150 risk per contract**, equal to the current daily-loss cap (but still above the current 120-tick per-trade stop cap):

- 300 ticks: **+$9,867.48 / PF 1.31**;
- 15 positive / 9 negative months;
- 2024 +$928, 2025 +$6,075, 2026 +$2,865;
- removing top five winners still leaves about **+$4.39k**;
- 8 adverse slippage ticks still leaves about **+$7.81k / PF 1.23**.

Applying current-like signal quality gates (relative volume >=0.8, STRONG trend, direction aligned) weakens but does not erase the 300-tick result:

- about 207 resolved;
- **+$5,971.64 / PF 1.29**;
- H1 **+$1,330.56 / PF 1.13**;
- H2 **+$4,641.08 / PF 1.46**;
- 2024, 2025, 2026 all positive;
- still positive through 8-tick slippage;
- removing top five winners leaves only about +$783, so concentration remains a concern.

**Conclusion:** timeframe and stop architecture matter. This family is not validated, and selecting the historically best cap would be post-hoc. A preregistered stop range / forward confirmation is required before any risk-rule discussion.

### MES 4H / Daily / 60m / 15m 2-2 reversal — WAIT, MOVING TOWARD REJECTION

Corrected evidence does **not** support the earlier session-aligned claim that MES 4H rescued 2-2 reversal.

Untouched structural baselines:

- Daily: **-$2,955.91 / PF 0.78**, H2 strongly negative;
- corrected 4H: **-$1,142.79 / PF 0.95**, H2 negative;
- 60m: **-$2,850.20 / PF 0.94**;
- 15m: **-$10,596.30 / PF 0.90**.

Daily stop-only, target-only, entry-confirmation, and tested existing-context filters do not produce a stable repair. Corrected 4H stop caps from 60 through 600 ticks do not rescue it either.

**Conclusion:** MES 2-2 reversal now has negative evidence across all four preregistered standard timeframes and multiple Daily repair variables. Keep WAIT until the matrix is formally reproduced, but this is now close to a defensible BROKEN classification for MES rather than a stop-only problem.

### MES Daily 3-2 — PROMISING RAW SIGNAL / CURRENT CONTRACT UNSTABLE

Untouched Daily structural bracket:

- 23 resolved;
- **+$10,132.21 / PF 2.31**;
- both halves positive.

Existing relative-volume gate >=0.8, with the original structural stop, improves the population:

- about **+$11.3k / PF 2.90**;
- both halves positive;
- removing top three winners still leaves about **+$1.25k**.

However the structural stop is far outside current risk:

- median Daily 3-2 stop ~**381 ticks** (~$476 MES risk/contract);
- 0 candidates fit the current MES 60-tick stop cap.

A 60-tick stop by itself is positive overall, but the apparent recent-half success depends on a low-volume 2026 winner. When the **existing >=0.8 volume gate and current 60-tick stop are both enforced**, H2 and 2026 turn negative.

**Conclusion:** raw Daily 3-2 has a signal, and volume quality helps it, but the current stop/risk contract still does not fit. Do not promote the earlier 60-tick result as current-system compatible.

### MNQ Daily 3-2 — WAIT / WALK-FORWARD FAILURE

- Daily baseline **-$3,843.88 / PF 0.84**; H1 positive, H2 strongly negative.
- Stop-only and 1R/1.5R target-only variants can make the total positive, but H2 remains negative.
- Entry confirmation and tested TREND/FTFC/relative-volume filters do not rescue H2.
- 15m also flips from positive H1 to negative H2.
- 60m is approximately flat/unstable.
- corrected 4H is negative overall with H2 strongly negative.

**Conclusion:** no stable entry/stop/target/filter/timeframe repair has been found. This is close to a genuine rejection; retain WAIT until the full matrix is committed/reproduced.

### Daily 3-2-2 reversal — WAIT / SMALL SAMPLE

Daily MNQ: 17 resolved, **+$1,341.34 / PF 1.15**, H1 negative / H2 positive.

Daily MES: 15 resolved, **+$1,547.80 / PF 1.38**, H1 negative / H2 positive.

Stop, target, and entry variants do not remove the basic Daily instability. Sample is too small.

### MNQ 60m 3-2-2 reversal — PROMISING BUT UNPROVEN

Untouched structural baseline:

- 284 resolved;
- **+$7,403.68 / PF 1.24**;
- both halves positive.

The current MNQ 120-tick cap destroys it: about **-$5,974.82 / PF 0.64**.

A broad stop plateau is positive rather than one magic cell:

- 300 ticks ~+$3.1k / PF 1.12;
- 400 ticks ~+$6.5k / PF 1.24;
- 500 ticks **+$9,879.32 / PF 1.37**;
- 600 ticks ~+$10.1k / PF 1.37;
- 800 ticks ~+$8.5k / PF 1.30.

At 500 ticks:

- H1 **+$4,848.90 / PF 1.37**;
- H2 **+$5,030.42 / PF 1.37**;
- 17 positive / 7 negative months;
- 2024, 2025, 2026 positive;
- 4-tick slippage **+$9,221.32 / PF 1.34**;
- remove top five winners and about **+$4.5k** remains.

A separate causal 15m 3-2-2 population is also positive in both halves, while Daily is too small/unstable. This supports the view that the family itself should not be discarded, but the dedicated current 60M 3-2-2 engine/IOC audit remains authoritative for deployment decisions.

## Other strategy work

- ORB Reclaim current/first_cross — retain latest negative evidence.
- ORB Reclaim V4-R — WAIT.
- Inverse ORB / VWAP evidence with invalid bracket geometry must not be used as execution proof.
- 4HR Re-Trigger and Miyagi should be interpreted through their latest family-specific audits, not the stale September 6 broad labels.
- MES `strat_122` has separate current-engine work on `main`; do not duplicate it in this audit.

## Research tooling blocker — PR #527

Draft PR #527 is research-only and must **not** be merged as-is. Three defects were caught:

1. its test compares a returned `StratContext` object to a string instead of checking `.strat_sequence`;
2. candidate detection independently searches both boundaries, so a later opposite-side break can be mislabeled instead of assigning the forming pattern from the first boundary broken;
3. Daily resolution stops at end-of-session instead of allowing the multi-day target/stop horizon and enforcing one-position-at-a-time across that horizon.

The uploaded-corpus findings above therefore remain independent research evidence until reproduced by a corrected canonical harness.

## Safe next step

1. Correct or replace #527 before treating the Daily harness as canonical.
2. Do **not** change runtime/risk settings.
3. Continue robustness/decomposition on the surviving research leads: MNQ 4H 2-2 reversal, MNQ 60m 3-2-2, MNQ Daily 2-2 continuation, MES Daily 3-2.
4. Finish and commit the complete failure matrix for weak families before assigning BROKEN.
5. Any candidate stop/risk change must be preregistered and then confirmed outside the cell used to discover it; do not select the historical best cap and call it validated.
