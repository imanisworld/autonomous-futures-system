# Futures — Current State Handoff

_As of 2026-09-16 13:53 EDT. Historical audit docs remain evidence records but do not override this file. Repository state is not proof of VPS state; verify the box separately before claiming anything is running._

## Verdict

**PAPER / OBSERVATION / RESEARCH ONLY. NO LIVE EXECUTION OR STRATEGY PROMOTION IS APPROVED.**

Core rule: **No proof, no run.**

## What we accomplished on 2026-09-16

The work today built an evidence chain for missed-signal discovery without expanding live execution:

- **#585** — observation-only transport across MNQ/MES/M2K/MGC/MCL/MBT, 15m-isolated, epoch-scoped, idempotent, with execution backstops.
- **#586** — evidence-quality/provenance gating for gaps, roll ambiguity, code/detector/timeframe/calendar provenance, and MBT horizon uncertainty.
- **#587** — fail-closed MGC/MCL behavior while the campaign is OFF.
- **#588** — release-manifest provenance + quality-gated status; **#589** — test-only deterministic fixture repair.
- **#590/#591** — separate observation Discord events from operational/safety errors.
- **#592** — repeatable read-only why-no-trade and counterfactual reporting.
- **#593** — deterministic preserved MNQ counterfactual reproduction.
- **#594** — causal BOS/MSS first-retest event study, no trade model.
- **#595** — isolated MNQ Asian D+EMA forward-paper lane, default OFF and not activated.
- **#596** — matched Asian pre-signal winner/loser precursor audit.
- **#597** — docs-only current-state refresh.
- **#598** — MES D+EMA portability producer required for the MES input to #596.

This does **not** mean a new strategy is validated. It means the system now has a safer, more reproducible path to determine where information disappears between market structure and execution.

## Current repository state

GitHub `main` is **`6bf3691b5b87e19f50b5e7d98133c0db81607cdf`** (#592).

Merged futures work today: **#585–#592**, including the test-only #589. These repository merges do **not** prove the VPS is currently running `main`.

## Current open audit / research work

### #593 — MNQ missed-opportunity producer

**AUDIT / RESEARCH ONLY.** The preserved counterfactual producer now reproduces the original snapshot including WIN/LOSS/NO_FILL/EXPIRED accounting. Repeated real-data output was byte-identical, cohort reconciliation passed, and exact-head CI is green. No runtime behavior change.

### #594 — BOS/MSS first-retest event study

**RESEARCH ONLY / EVENT STUDY ONLY. NOT A STRATEGY.**

Frozen causal definition:

- complete 5m bars -> complete 15m triples;
- swing length 7;
- first directional break establishes state only;
- next same-direction break = BOS;
- next opposite-direction break = MSS;
- event known only at the 15m break-bar close;
- first later retest within 120m decides `RETEST_HOLD`, `RETEST_FAIL`, or `NO_RETEST`;
- no later successful touch can replace an earlier failed first touch.

Measurements are descriptive only: signed move, MFE, MAE, directional rates, and mean/median movement at 15/30/60/120m.

No entry, stop, target, P&L strategy, PaperBroker, RiskEngine, promotion rule, or execution path exists. CHoCH is not claimed.

**Exact-head CI is green.** Remaining proof is the Phase-1 MNQ run against the preserved multi-month 5m corpus and review of the resulting event behavior. MES is conditional later; M2K/MGC/MCL/MBT remain out of scope.

### #595 — MNQ Asian D+EMA forward paper cohort

**DRAFT / OFF / NOT ACTIVATED.**

Scope: MNQ only, Asian only, 15m only, `ema_pullback_trend` + `strat_22_continuation_observed`, one open cohort position, canonical PaperBroker IOC, no external-broker route.

The population-delta audit found no material change when evaluation expanded to every claimed authoritative 15m MNQ bar: no D+EMA candidates were added in either 2026-07-13..08-31 or September, and the historical one-position streams remained identical.

The follow-up also centralized MNQ economics, added crash-safe/idempotent persistence, corrected day-roll EXPIRED timestamps, and pinned fixture provenance.

**Exact-head CI is green.** This proves stronger code/evidence parity, not forward profitability or activation authority.

### #596 — Asian pre-signal precursor audit

**RESEARCH ONLY / DRAFT.**

Compares already-labeled Asian winners and losers using strictly pre-signal information. Populations stay separated by instrument × strategy × session × direction. No feature ranking, threshold tuning, gate proposal, relabeling, or automatic strategy conclusion.

**Exact-head CI is green.** Remaining proof is the real preserved-corpus run plus per-strategy/direction and chronological H1/H2 review.

The MES leg requires a canonical MES D+EMA terminal WIN/LOSS cohort from #598 first. #594 remains separate and is not an automatic dependency of #596.

### #598 — MES D+EMA baseline portability

**RESEARCH ONLY / AUDIT ONLY.**

Purpose: create the canonical MES D+EMA population and Asian terminal WIN/LOSS cohort consumed by the MES leg of #596.

Boundaries:

- stacked on #593; MNQ producer untouched;
- MES 15m D+EMA only;
- Asian/London/New York kept separate;
- original shadow candidates and original entry/stop/target only;
- canonical offline PaperBroker IOC;
- MES tolerance 16 ticks / 4 points;
- one adverse tick entry and stop slippage;
- pessimistic same-bar stop-first;
- WIN/LOSS/NO_FILL/EXPIRED stay separate;
- no Tradovate, webhook, service, env, deployment, or trading-rule change.

**Exact-head CI is green.** The real evidence run is still required. Run 2026-07-13..08-31 separately from September, quarantine the **2026-09-14 22:00–23:55Z** roll seam, require byte-identical reruns/hashes, report sessions separately, and pass only Asian terminal WIN/LOSS rows to #596.

### #597 — docs refresh

**DOCS ONLY / DRAFT.** No runtime/config/risk/campaign/deployment behavior change.

## Current missed-signal question

The question is not merely “why did the bot say NO_TRADE?”

> **What useful market moves occur that the current futures system never turns into valid signals, and exactly where in the pipeline are they lost?**

Audit:

`market structure -> available inputs -> regime/state -> setup detection -> candidate creation -> filters/gates -> decision -> execution`

Classify each miss as:

1. **Input blindness** — needed information is absent.
2. **Detector blindness** — information exists but no setup recognizes it.
3. **Classification/gate suppression** — a candidate exists but current regime/trend/session/confluence logic removes it.
4. **Execution-only rejection** — the signal exists but risk/fill/execution rejects it; this is not a missed-signal defect.

Do not assume every visible historical move was a valid ex-ante signal.

## What is settled vs. unproven

### Settled enough not to redo

- Additional roots can be observed without granting trading eligibility.
- Bad/provenance-ambiguous evidence can be blocked from readiness counts without deleting it.
- The MNQ missed-opportunity study has a reproducible preserved path (#593).
- #595's broader 15m evaluation does not materially change the historical D+EMA population.
- #594/#596/#598 have green exact-head CI and explicit research-only boundaries.

### Still unproven

- That the system actually should produce materially more **valid** signals.
- Which missed-signal bucket dominates across enough independent sessions.
- Whether BOS/MSS carries repeatable directional information on preserved MNQ history.
- Whether pre-signal winner/loser differences survive preserved-corpus runs and chronological splits.
- Whether MES independently reproduces the MNQ D+EMA behavior.
- Whether #595 should ever be activated; green CI and historical parity are not forward evidence.
- Current VPS/runtime state without fresh box proof.

## Research-chain relationships

- **#593** = reproducible MNQ counterfactual producer.
- **#594** = separate BOS/MSS causal event study.
- **#595** = paper-forward candidate for one MNQ Asian D+EMA population; OFF.
- **#596** = broad matched pre-signal discovery.
- **#598** = MES producer required to create the canonical MES input to #596.

MES order:

`#598 canonical MES D+EMA population -> Asian terminal WIN/LOSS cohort -> MES leg of #596`

#594 is not an automatic dependency of #596.

## Runtime facts requiring fresh box proof

Do not infer from GitHub `main`:

- exact running futures release SHA;
- whether #590/#591/#592 are deployed in the futures process;
- whether `DISCORD_ROUTE_OBSERVATION` is active;
- current six-root 15m feed health;
- current campaign counts/outcomes;
- current paper/demo positions or working orders;
- broker-account routing state.

## Required sequence

1. Finish the independent #595 audit; keep it OFF absent separate activation authorization.
2. Complete #598 MES evidence runs with roll-seam quarantine and deterministic hashes.
3. Feed only #598's Asian terminal WIN/LOSS cohort into the MES leg of #596.
4. Run #596 on canonical preserved cohorts without threshold tuning.
5. Run #594 Phase-1 MNQ on the preserved multi-month 5m corpus as the separate structure study.
6. Complete the broader missed-signal census across the full pipeline.
7. Only after repeatable independent evidence should a separate preregistered hypothesis be proposed.
8. Only after that should the smallest paper-only forward test be considered.

## Hard boundaries

Do not enable live execution; submit broker orders from research code; relax global risk rules merely to make an experiment pass; change evidence parameters mid-study; pool instruments/sessions/strategies/directions to satisfy gates; fabricate MES candidates/labels; turn BOS/MSS event behavior directly into a trading strategy; or activate #595 based only on CI/historical parity.

## Safe next step

**Continue the offline evidence chain. #595 remains OFF. Complete #598 before the MES leg of #596. Run #596 on canonical preserved cohorts without rule tuning. Run #594's MNQ preserved-corpus event study separately. No deployment, restart, environment change, campaign change, or broker action is authorized by this handoff.**
