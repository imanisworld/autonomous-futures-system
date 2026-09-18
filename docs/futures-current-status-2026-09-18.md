# Futures Current Status — 2026-09-18

This is the concise operator-facing source of truth for the futures system as of the end of the 2026-09-18 work session. Historical audit documents remain evidence records; where an older summary conflicts with this file, this file governs current status unless a later dated document explicitly supersedes it.

## Verdict

**PAPER / SHADOW / DEMO EVIDENCE ONLY. NO LIVE EXECUTION APPROVED.**

The system is materially safer and more testable than it was at the start of the week, but no strategy is validated. The strongest current strategy lead is MNQ 4HR Re-Trigger, and even that remains **PROMISING BUT UNPROVEN** after correcting its entry-timing realism.

Active deployed futures release verified on the box:
- release: `a6913c06750dbe9e67ea6ba0120ac43841f1fdc9`
- service CWD matches that exact release
- `LIVE_TRADING_ENABLED=false`
- `TRADOVATE_ENV=demo`
- `SCHEDULE_MODE=always_on_shadow`
- `EXIT_MODE=static`
- `MAX_CONTRACTS_HARD_CAP=1`
- `FIVE_MIN_FEED_ENABLED=true`
- `ONE_MIN_TRIGGER_ENABLED=true`
- live-box drift guard: **OK**, no missing pins, no unpinned overrides, no mismatches

Repository `main` is ahead of the deployed futures release. Repository state must not be used as proof of deployed state.

## What is good / high confidence

### Runtime and execution safety

High confidence:
- live trading is disabled;
- the deployed broker environment is DEMO;
- the 1m lane has no live broker authority;
- collection-only roots remain non-executable by construction;
- max 1 contract hard cap is loaded;
- the global three-trades/day safety cap remains defined;
- stop-gap realism exists for already-resting stops;
- invalid brackets and invalid payloads fail closed;
- deployment is through immutable release SHA + build/verify/promote;
- the proof-critical 1m enable flag is pinned by the live-box guard.

No evidence currently supports accidental real-money execution through the tested paths.

### Feed coverage

All six configured futures roots now deliver authenticated 1m TradingView data and write isolated `tf1m/` bars:

- MNQ
- MES
- M2K
- MGC
- MCL
- MBT

At the post-deploy proof check, each root had a fresh 1m payload and an active `tf1m` file.

Role separation is deliberate:
- MNQ: 1m may observe an already-armed 4HR trigger;
- MES: 1m context collection;
- M2K/MGC/MCL/MBT: strictly observation-only 1m collection;
- 1m does not create a strategy setup.

### Backtest / replay infrastructure

High confidence for the scopes that have been explicitly audited:
- realistic same-bar ambiguity is pessimistic;
- stop-first treatment is used where price path is unknowable;
- commission/slippage stress is available;
- 4HR can now be compared under old trigger-backfill, completed-5m IOC, and causal pre-armed-touch entry models;
- causal completed-1H stop anchoring has been tested for 4HR;
- the structural-level R5/R6/R7 corpus/feature/attestation chain is reproducible;
- exact replay can be trusted for frozen, explicitly admitted scopes after parity checks.

Do **not** generalize this to “all backtests are trustworthy.” Fidelity is strategy-specific.

## What is not good / still uncertain

### No strategy is validated

There is no futures strategy that currently meets the full validation standard of:
- multiple-month stability;
- realistic executable fills;
- identical live/replay semantics;
- sufficient sample;
- controlled drawdown;
- slippage survival;
- independent forward confirmation.

Paper wins and one-off positive studies remain insufficient.

### 4HR timing defect changed the interpretation

The old 4HR historical study was too optimistic on entry timing.

Documented rule:
- the retrigger is a **level touch / break**;
- no completed 5m close is required by the strategy rule.

Old implementation:
- inferred the intrabar touch from the completed 5m bar;
- then historical replay could fill back at the original trigger price;
- forward paper/DEMO instead used current decision-time price + IOC tolerance.

That mismatch explains a large portion of the “late” / “unmarketable” behavior.

Controlled MNQ A/B, same 81 canonical candidates:

Old trigger-price backfill:
- 80 fills;
- +$2,886.60 at 1 tick.

Completed-5m close + 8-tick IOC:
- 37 fills;
- 44 no-fill/excluded;
- +$1,266.24;
- very uneven halves.

Pre-armed stop-touch:
- 80 fills;
- +$1,414.60 at 1 tick;
- +$1,354.60 at 2 ticks;
- +$1,294.60 at 3 ticks;
- both chronological halves positive at 3 ticks.

Conclusion:
- the old +$2.8k headline is not the executable headline;
- 4HR still survives a more realistic pre-armed touch model;
- therefore 4HR remains **PROMISING BUT UNPROVEN**, not validated and not broken solely because the old backfill was optimistic.

Two hour-boundary candidates also proved that detecting the trigger at the later 5m close can select a 1H stop candle that was not yet complete at the actual trigger time. The pre-armed model fixes that timing boundary.

### Other armed-trigger strategies may have the same timing approximation

Still requires explicit parity work:
- 60M 3-2-2 First Live;
- 12HR Miyagi;
- generic 2-1-2 / 1-2-2 next-bar boundary triggers.

These strategies should not automatically inherit the 4HR fix without their own A/B proof.

Close-confirmed strategies such as ORB/VWAP families must not be converted to touch-entry merely because 1m data now exists.

## 4HR — current evidence picture

### Canonical MNQ 4HR

Classification: **PROMISING BUT UNPROVEN / PAPER ONLY.**

The corrected pre-armed touch model is the current timing-realistic reference for entry-mechanism research. Existing wide-stop forward-paper evidence remains a separate campaign and must not be silently reinterpreted as if it had always used the new trigger semantics.

### Higher-timeframe structure attribution

Using the canonical 4HR trade population as an offline diagnostic:

Strongest adequately populated 4H context:
- 4H 2→2 continuation;
- n=29;
- +$2,085.08 under the original canonical trade outcomes;
- positive H1 and H2;
- survives 3-tick slippage in that attribution.

Recent 4H compression:
- 2+ inside bars among the last five completed 4H classifications;
- n=21;
- +$1,291.42;
- positive both halves;
- survives 3 ticks.

But:
- an immediate 4H 1→2 precursor was negative;
- an immediate 1H 1→2 precursor was worse;
- therefore “require 1→2” is **not** supported as a simple entry gate.

The small combination 2+ recent 4H inside bars + 4H 2→2 continuation was strong but only n=5. Classification: **WAIT — too small**.

### Supply / demand target geometry

For the 29 4H 2→2-continuation-context trades:

Target inside the nearest opposing 1H/4H supply/demand zone:
- n=14;
- 78.6% wins;
- +$1,971.78;
- +$140.84/trade;
- positive H1/H2;
- +$1,954.78 at 3 ticks.

Target beyond the opposing zone:
- n=6;
- 50% wins;
- −$247.38;
- H1 negative;
- H2 negative;
- −$256.38 at 3 ticks.

First-touch rejection itself was not useful:
- reject/close-back n=6, approximately flat.

Interpretation:
- opposing supply/demand is better treated as a **target/destination context** than an automatic reversal trigger;
- continuation can legitimately trade into the zone;
- extending beyond the zone is the weak geometry in this sample;
- this remains diagnostic because the inside-zone continuation cell is only n=14.

Next controlled offline test should keep the same candidates/entry/stop/fill model and compare the canonical target to a target clipped to the opposing zone. Do not retune multiple thresholds at once.

## What we are collecting now

### 1m TradingView context

All six roots:
- MNQ
- MES
- M2K
- MGC
- MCL
- MBT

Purpose:
- lower-latency trigger/context evidence;
- not a new signal engine;
- not permission to increase instrument scope.

### Existing forward evidence lanes

The previously active futures evidence campaigns remain conceptually distinct:
- MNQ wide-stop / 4HR family paper evidence;
- MNQ 3-2-2 evidence;
- MNQ Daily 2-2 continuation;
- MES 15m 1-2-2;
- cross-instrument observation;
- MNQ Asia D+EMA observational cohort.

Do not merge balances, epochs, or evidence populations across these lanes.

### Structural-level research

R5 candidate/outcome corpus is sealed and reproducible.
R6 features are frozen.
R7 independent attestation exists.
P8 exact outcomes show the broad structural population is net negative; no generic structural family is validated.
The consumed MES OOS holdout produced partial replication only and cannot be reused as fresh confirmation.

## What data we still need

For 4HR:
- prospective 1m armed-trigger evidence;
- actual trigger detachment versus prior 5m-close timing;
- forward fills under the causal trigger model;
- enough n to judge the 4H continuation / compression / zone-geometry cells;
- target-clipping A/B before proposing any target-rule change.

For 3-2-2:
- exact intrabar/pre-armed trigger A/B analogous to 4HR;
- more sample, including more losses;
- confirmation that lower-latency triggering does not create a new population.

For Miyagi:
- exact timing parity if it is ever considered beyond research;
- much larger sample; current n is too small.

For MES 1-2-2:
- prospective evidence under realistic accounting;
- current edge is very thin and fails stronger slippage.

For M2K/MGC/MCL/MBT:
- enough 1m/5m/15m observation history before any strategy or execution expansion is considered.

## Can we backtest well now?

**Answer: yes for audited scopes; no as a blanket statement.**

What we can do well now:
- deterministic replay over known bar corpora;
- causal candidate reconstruction for audited detectors;
- pessimistic same-bar bracket resolution;
- slippage/commission stress;
- chronological halves;
- exact entry-model A/B for strategies whose trigger semantics are specified;
- causal 1H/4H context and supply/demand attribution;
- frozen-manifest / hash-backed structural research.

What still blocks blanket trust:
- some strategies still have live/replay timing differences;
- some historical studies used optimistic decision-time fills;
- higher-timeframe/touch strategies need intrabar semantics checked individually;
- TradingView/Pine versus backend formula parity is not globally proven;
- continuous-symbol data is not the same thing as dated-contract identity;
- one strategy passing replay fidelity does not certify another.

Rule: **backtest credibility is per strategy and per execution model, never inherited globally.**

## Current confidence map

High confidence:
- runtime is not live;
- deployed 1m feed isolation;
- all six 1m feeds are arriving;
- collection-only roots cannot execute;
- pessimistic PaperBroker ambiguity;
- 4HR has a real 5m-close latency problem;
- old 4HR trigger-price backfill overstated executable results;
- pre-armed MNQ 4HR remains positive after 1/2/3-tick stress;
- broad generic structural family is not an edge.

Medium confidence / promising:
- MNQ 4HR itself;
- 4H 2→2 continuation context;
- repeated recent 4H compression as context;
- target-inside-opposing-zone geometry.

Low confidence / unresolved:
- 4HR target clipping as an actual rule;
- failed-2 / reversal discrimination at zones;
- 3-2-2 under true intrabar triggering;
- Miyagi;
- cross-instrument edge transfer;
- any live expectancy claim.

## Do not change yet

Do not:
- enable live trading;
- give 1m bars setup-discovery authority;
- let collection-only instruments enter DecisionEngine/RiskEngine/broker paths;
- replace 5m/15m feeds with 1m;
- change global risk caps to rescue a strategy;
- convert close-confirmed ORB/VWAP rules into touch triggers;
- tune target/stop/zone thresholds off the small diagnostic cells;
- call 4HR validated.

## Safe next work order

1. **4HR target-geometry controlled A/B** — same frozen population; canonical target vs zone-clipped target only.
2. **4HR prospective 1m evidence** — verify trigger touch timing, stop anchor, dedupe, and paper-only behavior on natural signals.
3. **3-2-2 timing audit** — determine whether its documented “first live break” is suffering the same completed-5m latency and run an analogous pre-armed A/B.
4. **Only then** decide whether 1m should gain paper-fill authority for any strategy.
5. Continue passive evidence collection; do not expand instruments or execution scope.

## Bottom line

The system is now substantially better at answering **why a trade was late, why a historical fill was optimistic, and whether a trigger was actually executable**.

The important change in confidence is not “we found a winning bot.” It is:

- we can now distinguish setup logic from trigger timing;
- we have a lower-latency evidence feed;
- we caught and quantified a real historical-fill optimism defect;
- the strongest MNQ 4HR signal still survives after that correction;
- and the system remains contained while we gather proof.

**No proof, no run.**
