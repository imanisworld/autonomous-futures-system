# Futures — Current State Handoff

_As of 2026-09-16. This is the single current futures handoff. Historical audit docs remain evidence records, but they do not override this file. Repository state is not proof of VPS/deployment state; verify the box separately before claiming anything is running._

## Verdict

**PAPER / OBSERVATION / RESEARCH ONLY. NO LIVE EXECUTION OR STRATEGY PROMOTION IS APPROVED.**

Core rule: **No proof, no run.**

## Current repository state

Current GitHub `main` is **`6bf3691b5b87e19f50b5e7d98133c0db81607cdf`** (#592).

Merged on 2026-09-16:

- **#585–#588** — cross-instrument observation transport plus evidence-quality, continuity, roll, and provenance gates;
- **#590** — dedicated optional Discord `observation` route;
- **#591** — operational failures/safety alerts routed to `error` instead of heartbeat;
- **#592** — read-only missed-opportunity and why-no-trade reporting.

These merges do **not** prove the VPS is running those commits. Deployed SHA, effective environment pins, Discord route activation, feed health, campaign state, positions/orders, and broker state remain box-side facts.

## Current open audit / research PRs

### #593 — preserved MNQ missed-opportunity producer

**AUDIT ONLY / RESEARCH ONLY.** The preserved September counterfactual producer now reproduces the original snapshot including WIN/LOSS/NO_FILL/EXPIRED accounting, and exact-head CI is green. It changes no runtime behavior. Merge still requires explicit operator authorization.

### #594 — causal BOS/MSS first-retest event study

**RESEARCH ONLY / EVENT STUDY ONLY. NOT A STRATEGY.**

Question: does the repo's own causal swing-structure signal carry directional information that the current setup families are not exploiting?

Frozen event definition:

- exact complete 5-minute bars aggregated into complete 15-minute triples;
- incomplete 15-minute buckets skipped; duplicate 5-minute timestamps fail closed;
- swing length fixed at 7;
- pivot exists only after seven right-side 15-minute bars close;
- first directional break establishes state only;
- next same-direction break = BOS;
- next opposite-direction break = MSS;
- event is knowable only at the 15-minute break-bar close;
- equal-extreme pivot ties fail closed.

First retest uses only later 5-minute bars for up to 120 minutes. First touch decides: `RETEST_HOLD`, `RETEST_FAIL`, or `NO_RETEST`; a later successful touch cannot overwrite an earlier failed first touch.

Measurements are descriptive only at 15/30/60/120 minutes: signed move, MFE, MAE, positive-direction rate, MFE>MAE, and mean/median movement.

**No entry, stop, target, P&L strategy, commission model, PaperBroker, RiskEngine, promotion rule, or execution path exists in #594.**

CHoCH is not claimed. The repo has no canonical causal CHoCH definition.

Instrument order is MNQ first; MES only if MNQ evidence warrants portability; M2K/MGC/MCL/MBT remain out of scope. Never pool instruments.

Remaining proof: exact-head CI plus Phase-1 MNQ against the preserved multi-month 5-minute corpus. Do not build bracket strategy logic from the event study alone.

### #595 — MNQ Asian D+EMA forward paper cohort

**DRAFT / NOT ACTIVATED.**

Scope remains MNQ-only, Asian-session-only, 15-minute decision bars, `ema_pullback_trend` + `strat_22_continuation_observed`, one open cohort position, canonical PaperBroker IOC, default OFF, and no external-broker route.

Audit follow-up at head `8ff7cfb` closed one important population-parity concern. Expanding evaluation from the archived producer's qualifying journal decisions to every claimed authoritative 15-minute MNQ bar added **no D+EMA candidates** in either 2026-07-13..08-31 or September, and the historical one-position streams were identical.

The same follow-up hardened:

- MNQ contract economics through central metadata;
- crash-safe/idempotent persistence;
- day-roll EXPIRED timestamps;
- fixture/provenance hashes.

That is stronger parity and persistence proof, **not activation authorization**. Finish the independent audit and require separate deployment/runtime proof before any activation decision.

### #596 — Asian pre-signal precursor audit

**RESEARCH ONLY / DRAFT.** It compares already-labeled Asian winners and losers using strictly pre-signal information. It does not create candidates, infer labels, rank features, tune thresholds, propose gates, or authorize a strategy change.

The **MES leg cannot run honestly yet** because it requires a canonical MES D+EMA terminal WIN/LOSS cohort. #598 now exists specifically to produce that input. Do not fabricate or relabel MES precursor rows.

#594 is separate. BOS/MSS is not an automatic dependency of #596; use #594 only if the broader precursor evidence leaves a structure-specific question unresolved.

### #598 — MES Asian D+EMA baseline portability

**RESEARCH ONLY / AUDIT ONLY.** This is the repo-side MES portability producer required to create the canonical MES D+EMA population and the Asian terminal WIN/LOSS cohort consumed by the MES leg of #596.

Boundaries:

- stacked on #593; the MNQ producer is not modified;
- MES 15-minute D+EMA population only;
- Asian / London / New York kept separate;
- cohort D + existing EMA alignment only; no A/B/C gate experiment;
- original journal `shadow_candidates` and original entry/stop/target only;
- canonical offline `PaperBroker` IOC;
- MES tolerance = 16 ticks / 4.0 points;
- one adverse tick entry and stop slippage;
- clean target, pessimistic same-bar stop-first;
- WIN / LOSS / NO_FILL / EXPIRED remain separate;
- no Tradovate, webhook, service, environment, deployment, or trading-rule change.

Exact-head repo proof is green: **5,587 passed / 7 skipped**, CodeQL success, Analyze python success, Analyze actions success.

A provenance-manifested MES 5-minute extension exists locally through **2026-09-16 08:50Z**, but the actual evidence reproduction is still required. The **2026-09-14 22:00–23:55Z** contract-roll seam must be explicitly quarantined/flagged; September 16 is partial through 08:50Z.

Required #598 evidence sequence:

1. produce/preserve MES 15-minute replay candles and journal rows through the existing replay path; never synthesize shadow candidates;
2. run 2026-07-13..08-31 independently;
3. run 2026-09-01..latest proven complete date independently;
4. quarantine the Sept 14 roll seam;
5. repeat runs and require byte-identical outputs/hashes;
6. report the full D+EMA population and per-session counts separately;
7. pass only Asian terminal WIN/LOSS rows into #596.

### #597 — current-state docs refresh

**DOCS ONLY / DRAFT.** This PR updates current-state documentation only. It changes no runtime/config/risk/campaign/deployment behavior.

## Current strategy question: missed-signal discovery

The question is not simply “why did the bot say NO_TRADE?”

> **What useful market moves occur that the current futures system never turns into valid signals, and exactly where in the pipeline are they lost?**

Audit the chain:

`market structure -> available inputs -> regime/state -> setup detection -> candidate creation -> filters/gates -> final decision -> execution`

Classify each miss as:

1. **Input blindness** — needed information is not present in system inputs/state.
2. **Detector blindness** — information is present but no existing setup recognizes it.
3. **Classification/gate suppression** — a valid candidate exists but regime/trend/session/confluence logic removes it.
4. **Execution-only rejection** — signal existed and only risk/fill/execution rejected it. This is **not** a missed-signal defect.

Discovery is evidence-first. Do not create or tune a strategy because a historical move looks attractive. First prove that the missing information or failure stage repeats across multiple sessions and chronological splits.

## Relationship of the current research chain

- **#592** = merged read-only reporting infrastructure.
- **#593** = preserved/reproduced MNQ counterfactual producer.
- **#598** = canonical MES D+EMA producer for the MES precursor input.
- **#596** = broad matched pre-signal precursor audit.
- **#594** = narrower causal BOS/MSS first-retest event study; separate, not an automatic dependency.
- **#595** = actual MNQ Asian D+EMA forward-paper candidate, still OFF.

For MES precursor work the required order is:

`#598 canonical MES D+EMA population -> Asian terminal WIN/LOSS cohort -> #596 precursor audit`

Do not collapse this into one tuning exercise.

## Existing strategy work — do not restart without a new question

4HR Re-Trigger, 60M 3-2-2 First Live, Miyagi, ORB/VWAP/inverse-ORB have already had substantial detector/bracket/risk/fill evidence work. Do not rerun settled waterfalls merely because the broader missed-signal investigation exists. Historical evidence files remain preserved audit records.

## Runtime facts that remain UNKNOWN without fresh box proof

Do not infer from GitHub `main`:

- exact deployed release SHA;
- whether #590/#591 are deployed;
- whether `DISCORD_ROUTE_OBSERVATION` is populated and active;
- current six-root 15-minute feed health;
- current cross-instrument campaign counts/outcomes;
- current status of older forward paper evidence lanes;
- current paper/demo positions or working orders;
- broker-account routing state.

## Required sequence

1. **Finish the independent audit of #595.** Do not activate it from code review alone.
2. Preserve #593 as the proven MNQ counterfactual reproduction path.
3. Complete #598's MES replay/evidence runs with roll-seam quarantine and deterministic hashes.
4. Feed only #598's Asian terminal WIN/LOSS cohort into the MES leg of #596.
5. Run #596 as descriptive precursor discovery; no strategy build or threshold tuning during discovery.
6. Run #594 Phase-1 MNQ separately when a structure-specific question actually remains; #594 is not a dependency of #596.
7. Only after repeatable independent evidence should the smallest possible paper-only forward hypothesis be proposed.

## Hard boundaries

Do not:

- enable live execution;
- submit broker orders from research/audit code;
- modify `risk_rules.yaml` to make an experiment pass;
- change strategy parameters during an evidence epoch;
- activate #595 without separate deployment/runtime proof and explicit authorization;
- pool instruments, strategies, directions, sessions, variants, or epochs to satisfy evidence gates;
- synthesize MES shadow candidates or let #596 infer outcome labels from price bars;
- overwrite historical evidence or corpus artifacts;
- treat BOS/MSS event behavior as a trade signal before separate bracket/fill/risk proof;
- treat an open draft PR as deployment authority.

## Safe next step

**Continue the offline evidence chain. #595 remains OFF. For MES precursor work, complete #598 before the MES leg of #596. For BOS/MSS, run the Phase-1 MNQ preserved-corpus event study only when the structure-specific question is needed. No deployment, restart, environment change, campaign change, or broker action is authorized by this handoff.**
