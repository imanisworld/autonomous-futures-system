# Futures — Current State Handoff

_As of 2026-09-16. This is the single current futures handoff. Historical audit docs remain evidence records, but they do not override this file. Repository state is not proof of VPS/deployment state; verify the box separately before claiming anything is running._

## Verdict

**PAPER / OBSERVATION / RESEARCH ONLY. NO LIVE EXECUTION OR STRATEGY PROMOTION IS APPROVED.**

Core rule: **No proof, no run.**

## Current repository state

Current GitHub `main` is **`6bf3691b5b87e19f50b5e7d98133c0db81607cdf`** (#592).

Important merged work on 2026-09-16:

- **#585–#588** — cross-instrument observation transport plus evidence-quality, continuity, roll, and provenance gates;
- **#590** — dedicated optional Discord `observation` route;
- **#591** — operational failures/safety alerts routed to `error` instead of heartbeat;
- **#592** — read-only missed-opportunity and why-no-trade reporting.

These repository merges do **not** prove the VPS is running those commits. The deployed SHA, effective environment pins, Discord route activation, feed health, campaign state, and broker state remain box-side facts that must be verified separately.

## Current open audit / research PRs

### #593 — preserved MNQ missed-opportunity producer

**AUDIT ONLY / RESEARCH ONLY.** It preserves the September counterfactual producer and reproduces the archived study from the preserved snapshot. It does not modify runtime strategy, risk, broker, webhook, service, environment, campaign, or deployment behavior.

### #594 — causal BOS/MSS first-retest event study

**RESEARCH ONLY / EVENT STUDY ONLY.** It measures causal swing-structure events and first retests. It defines no entry/stop/target/P&L strategy and grants no execution eligibility. Do not treat it as a new trading strategy.

### #595 — MNQ Asian D+EMA forward paper cohort

**DRAFT / NOT ACTIVATED. CURRENT REVIEW PRIORITY.**

Scope is intentionally narrow:

- MNQ root only;
- Asian session only;
- `ema_pullback_trend` and `strat_22_continuation_observed` only;
- authoritative 15-minute decision bars only;
- one open cohort position at a time;
- canonical PaperBroker IOC model only;
- default OFF;
- no London or New York population;
- no external-broker route.

The PR itself authorizes no deployment, `.env` change, restart, epoch, or activation. Finish the independent #595 audit before any activation decision.

### #596 — Asian pre-signal precursor audit

**RESEARCH ONLY / DRAFT.** It compares already-labeled Asian-session winners and losers using strictly pre-signal information. Its output is descriptive only: no feature ranking, threshold tuning, gate proposal, strategy change, or promotion verdict is authorized from the script alone.

# Current strategy question: missed-signal discovery

The current question is **not** simply “why did the bot say NO_TRADE?”

The question is:

> **What useful market moves occur that the current futures system never turns into valid signals, and exactly where in the pipeline are they lost?**

Audit the chain:

`market structure -> available inputs -> regime/state -> setup detection -> candidate creation -> filters/gates -> final decision -> execution`

Classify each miss into one of four buckets:

1. **Input blindness** — the information needed to recognize the move is not present in the system inputs/state.
2. **Detector blindness** — the information is present but no existing setup recognizes it.
3. **Classification/gate suppression** — a valid candidate exists but regime/trend/session/confluence logic removes it.
4. **Execution-only rejection** — the signal existed and was rejected only by risk/fill/execution constraints. This is **not** a missed-signal defect.

Discovery must be evidence-first. Do not create or tune a strategy merely because a historical move looks attractive. First prove that the same missing information or failure stage recurs across multiple sessions and survives chronological splits.

## Relationship of #592 / #593 / #594 / #596

- **#592** is merged reporting infrastructure. It explains existing journal decisions and aggregates explicit counterfactual rows. It does **not** recreate the counterfactual producer by itself.
- **#593** preserves/reproduces the producer that generates the audited MNQ counterfactual populations.
- **#596** is the broader matched precursor study for already-labeled Asian winners vs losers.
- **#594** is a narrower BOS/MSS event study and is **not** an automatic dependency of #596. Run it only if the broader evidence leaves a specific structure question unresolved.

Do not merge these into one broad tuning exercise.

## Separate MES D+EMA validation

MES D+EMA replication/validation is a **separate offline evidence study**, not the broad missed-signal audit.

Its required posture is preservation-first:

- reuse proven MNQ methodology, scripts, corpus, and artifacts where exact reuse is possible;
- reconstruct the exact MES D+EMA population rather than assuming MES behaves like MNQ;
- keep 2026-07-13..2026-08-31 historical evidence separate from September evidence;
- use canonical MES IOC economics and pessimistic same-bar handling;
- collapse surviving streams to one open position at a time;
- stress realistic costs/slippage;
- compare MES with MNQ without forcing the MNQ answer onto MES;
- create only the smallest additive MES-specific artifacts required.

No repo/runtime/Pine/campaign/epoch/env/service/broker changes are authorized by that evidence study.

## Cross-instrument observation status

Repository support now exists for observation-only collection across **MNQ, MES, M2K, MGC, MCL, and MBT** with population-isolated evidence and quality/provenance gates. That support grants **no strategy, risk, broker, paper-trade, or live execution eligibility** to the additional instruments.

Do not infer runtime activation from source code. Current campaign counts, terminal outcomes, trusted-feed state, exact deployed release, observation Discord route, and effective environment pins are runtime facts and must be checked on the box.

## Existing strategy work — do not restart without a new question

The following work has already been deeply audited and should not be rerun merely because the broader missed-signal investigation exists:

- **4HR Re-Trigger:** signal/detector, bracket, risk-gate, replay, and IOC behavior have already been decomposed. Reopen only for a specific new evidence question.
- **60M 3-2-2 First Live:** stop-cap, R:R, trend/gate attribution, replay, and IOC behavior have already been examined. Do not restart the same waterfall.
- **12HR Miyagi:** sample size remains a material limitation; more plumbing work does not create evidence.
- **ORB / VWAP / inverse-ORB:** historical positive headlines invalidated by later decision-time/fill-reference audits remain retired unless genuinely new independent evidence reopens them.

Historical evidence files are preserved audit records. Do not delete, overwrite, or rewrite them merely because they are no longer current-state summaries.

## Runtime facts that remain UNKNOWN without fresh box proof

Do not infer any of the following from GitHub `main`:

- exact deployed release SHA;
- whether #590/#591 are deployed;
- whether `DISCORD_ROUTE_OBSERVATION` is populated and active;
- current six-root 15-minute feed health;
- current cross-instrument campaign counts/outcomes;
- current status of older forward paper evidence lanes;
- current paper/demo positions or working orders;
- broker-account routing state.

If any of those facts matter to a decision, re-check the box first.

## Required sequence

1. **Finish the independent audit of #595.**
2. Do **not** activate #595 from code review alone.
3. Preserve/reconcile the research-only missed-signal work (#593/#594/#596) rather than rerunning proven corpus work.
4. Run/finish missed-signal discovery and identify where useful moves actually disappear. **No strategy build during discovery.**
5. Keep the MES D+EMA replication/validation separate and offline.
6. Only after a missing pattern is repeatable across adequate independent evidence should the smallest possible paper-only forward cohort be proposed.

## Hard boundaries

Do not:

- enable live execution;
- submit broker orders from research/audit code;
- modify `risk_rules.yaml` to make an experiment pass;
- change strategy parameters during an evidence epoch;
- activate #595 without separate deployment/runtime proof and explicit authorization;
- pool instruments, strategies, directions, sessions, variants, or epochs to satisfy evidence gates;
- overwrite historical evidence or corpus artifacts;
- convert one-day, one-session, or one-sample results into validation;
- treat an open draft PR as approved deployment authority.

## Safe next step

**Audit #595. Documentation and offline research may continue in parallel. This handoff update itself requires no deployment, restart, environment change, campaign change, or broker action.**
