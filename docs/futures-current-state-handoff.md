# Futures — Current State Handoff

_As of 2026-09-09 for repository/evidence state. This is the single current futures handoff. Runtime/VPS facts are not silently refreshed by this document; verify the box separately before making deployment claims. Do not recreate completed audits, redo merged fixes, or reopen strategy work unless new evidence proves a defect._

## Verdict

**HOLD / OBSERVATION ONLY. NO STRATEGY REWRITE IS JUSTIFIED.**

The important September finding is no longer “we just need more evidence on every lane.” The standardized edge-decomposition work showed that different strategy families fail for different reasons. Several earlier positive baselines were also retired after decision-time fill/bracket corrections. The correct response is to preserve the proven diagnoses and stop rerunning the same tests.

`docs/strategy-rules/Strategy_Inventory.md` is the evidence source of truth. `docs/edge-decomposition-audit-2026-09-07.md` is the standardized decomposition source for the lanes it covers.

## Strategy isolation protocol — completed work vs future work

For any strategy we are actively evaluating, isolate six questions:

1. **Signal / timeframe** — does the raw pattern carry directional information, and does the same setup behave differently by timeframe?
2. **Entry** — is the fill causal and realistically obtainable, or is the apparent edge coming from entering at a price only known after the move?
3. **Stop** — is the documented stop compatible with normal adverse movement, or is the risk architecture rejecting/forcing geometry that destroys the setup?
4. **Target / exit** — does the setup support its documented target/holding period, or is the bracket/hold converting useful drift into losses?
5. **Filters** — do session/trend/confluence/risk gates add value or remove the profitable population?
6. **Execution realism** — does the result survive honest fills, pessimistic same-bar handling, commission, adverse slippage, full-engine ordering and position/risk constraints?

### What is already done — do not rerun

The 2026-09-07 edge-decomposition audit already pushed the core audited lanes through a standardized waterfall:

- raw signal
- 30/60/120-minute and EOD time-exit controls
- documented bracket
- plan-price vs honest resting-fill comparison where applicable
- path-independent structural risk gates
- isolated full engine with floors off and frozen
- IOC at decision-bar close with 1/2/3-tick adverse slippage
- commission
- pessimistic same-bar resolution
- chronological-half checks where applicable

That already answers most of the six-part framework for **4HR Re-Trigger, 60M 3-2-2 First Live, Miyagi, ORB Reclaim, ORB Breakout, VWAP Hold, and transition failed-breakdown reclaim**. Do not rerun the same decomposition because a later conversation asks the question again.

What it does **not** prove universally:

- it is not a same-pattern-across-every-timeframe study;
- it is not a full MAE/MFE-derived stop sweep for every strategy;
- it is not a target-multiple optimization sweep for every strategy;
- lanes marked `not decomposed` in the Strategy Inventory remain not decomposed unless a separate strategy-specific study already settles the needed question.

Only run additional isolation work when it answers a genuinely unresolved question for a strategy that still has a plausible path forward. Do not spend time decomposing a retired/unreachable/clearly negative strategy merely for completeness.

## What the completed decomposition proved

### Close-confirmed level predicates

ORB Reclaim, source ORB Breakout and VWAP Hold do not have an execution/risk-gate problem hiding a strong signal. Their raw directional information is weak/negative and the old positive results were heavily affected by unrealistic/detached fill assumptions. The risk gates generally admitted most candidates and the admitted sets were still negative.

**Action: do not “fix” these by loosening gates or changing stops.** A materially changed entry rule is a new strategy variant and requires a new preregistered population.

### Armed-trigger Strat day strategies

4HR Re-Trigger MNQ, 60M 3-2-2 First Live, and Miyagi MNQ showed real directional/bracket behavior in the historical studies, but their natural stop/R:R geometry is incompatible with the current account/risk policy. The global stop cap/R:R floor removes nearly all of the population.

**Action: keep the global risk policy unchanged. These families remain parked under the previously adopted wide-stop policy decision; do not rewrite the detector or force them through current-account risk.**

### Transition failed-breakdown reclaim

The raw signal had only weak positive drift and the documented fixed bracket turned it negative. It also conflicts with the current trend/R:R/confluence architecture.

**Action: parked/broken under the documented form. Do not keep retesting long vs short to search for a rescue.**

## Decision-time execution corrections — September 8

Two important earlier positive claims are retired:

- **Inverse ORB:** the old positive IOC baseline used fills whose geometry was invalid at the correct decision-time reference. Correct decision-time replay produced 63 attempts, 41 invalid-at-fill, 22 admissible fills, about **+$29.44 / PF 1.14**, with **H2 negative**. The earlier +$1k-class headline is not valid edge evidence. Current verdict: **BROKEN — negative/insufficient evidence; no edge claim.**
- **VWAP Hold:** the old positive arrival-close result used a later price reference. At the decision-close reference the NY cell is negative (35 fills, about **-$326.92 / PF 0.49**) and both halves are negative. Current verdict: **BROKEN — negative evidence.**

The PaperBroker bracket guard/read-across work exists specifically so fills beyond their own stop/target are rejected rather than credited as evidence. Do not bypass that guard to recover an old result.

## Current strategy evidence classifications

Use the Strategy Inventory for the full table. The important active/known rows are:

- ORB Reclaim current/first_cross — **BROKEN — negative evidence**
- ORB Reclaim V4-R — **WAIT**; not separately decomposed, but same close-confirmed family; do not promote from aggregate P&L alone
- 4HR Re-Trigger MNQ — **BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS / PARKED below the adopted equity threshold**
- 4HR Re-Trigger MES — **BROKEN / WAIT**
- 12HR Miyagi — **BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS; MNQ parked**
- 60M 3-2-2 First Live — **BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS; parked**
- ORB Breakout inverted — **BROKEN — corrected decision-time evidence does not support the old edge claim**
- VWAP Hold MNQ NY — **BROKEN — negative corrected evidence**
- MES `strat_122` — **WAIT; not decomposed**
- VWAP Reclaim — **WAIT; not decomposed**
- VWAP Rejection — **BROKEN — unreachable predicate**
- Transition failed-breakdown reclaim — **BROKEN under documented bracket**

A strategy marked `not decomposed` does not automatically need another audit. First ask whether it has enough credible signal/evidence to justify the work.

## Filters / stop / target rule going forward

Do not optimize a failing lane by changing several variables at once. If new evidence creates a credible rescue hypothesis, change **one family of assumptions at a time** and version it as a new strategy population:

- signal/timeframe variant
- entry variant
- stop variant
- target/exit variant
- filter variant
- execution model variant

The old population remains frozen for comparison. No retroactive relabeling.

## Current safety posture

- paper/demo evidence only
- no averaging down
- no missing stop/target/invalidation
- full-engine and broker safety guards remain authoritative
- one-position/risk constraints remain part of realistic execution evidence
- do not fabricate signals or force traffic
- do not tune parameters because the current P&L is uncomfortable
- do not infer deployed VPS state from repository `main`; box state requires separate proof

## Repo/runtime boundary

Repository evidence has advanced materially since the prior 2026-09-06 handoff. The earlier handoff's VPS/release SHA references were point-in-time facts, not permanent truth. This refresh intentionally does not claim a new deployed futures SHA or current service state without a fresh box-side read.

Options work is separate. See `docs/options-current-state-handoff.md` for the frozen `OPTIONS_PAPER_V1` collection and its own post-sample isolation plan.

## Smallest safe next step

**Do not run another blanket futures strategy audit.**

For futures, continue only the already-approved observation/evidence lanes and run a new isolation study only when a specific unresolved strategy question has both:

1. a credible hypothesis that has not already been tested; and
2. enough independent evidence to justify the study.

Otherwise the correct action is **WAIT / PARKED / BROKEN as already classified.**
