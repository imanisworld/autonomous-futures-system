# Futures — Validated vs. Unvalidated (Status Pointer)

_As of 2026-09-16 evening._

## Status of this document

The previous 2026-07-08 contents of this file are **retired as a current-state summary**. The authoritative current handoff is:

- `docs/futures-current-state-handoff.md`

The evidence-classification inventory remains:

- `docs/strategy-rules/Strategy_Inventory.md`

Do not use this file alone to infer VPS deployment, enabled campaigns, environment pins, broker state, or strategy promotion status.

## Current repository facts

Current GitHub `main` is **`2521ebb92c4add36fb2c21f04c13df84250b332f`** after docs-only Options PR #599. The futures runtime state is tracked separately and must not be inferred from this SHA.

Relevant merged futures work:

- #585–#588 observation transport + evidence quality/provenance support;
- #589 deterministic historical fixture repair (test-only);
- #590 optional observation Discord route;
- #591 failure/safety routing to the error channel;
- #592 read-only missed-opportunity / why-no-trade reporting.

Open futures work remains research/docs/paper-only and is not deployment authority.

## Current strategy/evidence classifications

- **MNQ missed-opportunity reproduction (#593):** REPRODUCED / AUDIT ONLY.
- **MNQ Asian D+EMA (#595):** PROMISING BUT UNPROVEN / PAPER ONLY. Code safety approved; operational activation held.
- **MES D+EMA (#598 evidence):** BROKEN. Failed after collapse in every session and in both historical windows; no MES forward cohort warranted.
- **New York D+EMA:** BROKEN on both MNQ and MES.
- **Broad gate relaxation / tighter stops / lateness / low-displacement variants:** REJECTED or OVERFIT; no strategy change justified.
- **Pre-signal precursor representation (#596):** PARTIAL / UNPROVEN. Broad range/chop tendency partially replicated, but 94/118 features were unstable and only one strategy had adequate September sample size; no tuned rule or gate change.
- **BOS/MSS (#594):** WAIT / NO USABLE COVERAGE in the completed precursor chain. Event-study tooling exists, but no empirical strategy claim is supported.
- **Live execution:** NOT APPROVED.

## Runtime state from the final operator/Claude ledger

- deployed futures release: `62546883` (#588);
- cross-instrument observation campaign armed at 12:17Z with a fresh epoch;
- all six roots healthy and collecting since;
- TradingView auth repaired and feed watchdog installed;
- both running futures evidence campaigns untouched after arming;
- `f9d5395` release containing #590/#591 is built but held pending the missing `DISCORD_ROUTE_OBSERVATION` key.

This runtime record is distinct from GitHub `main`.

## What remains genuinely unvalidated

1. whether MNQ Asian D+EMA is a real repeatable edge or sample noise;
2. whether prospective paper performance resembles the historical result at all;
3. whether the range/chop precursor observation is causal/useful rather than merely descriptive;
4. whether future preserved data provides enough BOS/MSS coverage for a separate structure study.

The historical MNQ Asia result is not strong enough for validation: collapsed PF is roughly 1.46–1.59 on about 107 independent trades, null p95 PF is about 1.94, September was approximately flat, and MES did not replicate.

## Current posture

**PAPER / OBSERVATION ONLY. HOLD on strategy changes. NO LIVE EXECUTION OR STRATEGY PROMOTION.**

The retrospective research loop is closed unless new evidence creates a specific question. The next meaningful strategy-learning event is prospective #595 paper evidence after the September 30 release restriction lifts and explicit activation authorization is given.

For the exact next sequence and hard boundaries, read `docs/futures-current-state-handoff.md`.
