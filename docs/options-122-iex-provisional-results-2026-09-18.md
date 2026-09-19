# 1-2-2 provisional IEX observer reconciliation — result

Date: 2026-09-18

## Verdict

**MISS_ALLOWED_RESEARCH_OBSERVER_FEASIBLE.**

The preregistered 186-arm, outcome-independent source study supports IEX as a separate **provisional 1-2-2 research clock with mandatory delayed-SIP reconciliation**.

It does **not** support treating IEX as SIP-equivalent, activating 1-2-2 in V1, choosing an option target, scheduling a collector, or making any DEMO/live trade claim.

## Frozen population and proof

- primary-20 symbols;
- sessions 2026-09-09 through 2026-09-15;
- every structurally ARMED 1-2-2 watch window from the frozen trigger snapshot;
- arms: **186**;
- snapshot manifest SHA-256: `8d9cd8a4674827abc11bf60805d53ca812c738bb28417b672a10e3f98790dc1b`;
- preregistration committed and pushed before source queries: `b6c14851bfcb481e8d91061d657d74a1947c70d0`;
- result JSON SHA-256: `67f89feec8a3ae3beea8a15d4c7adb15957f58c910336c97a73a874e1c449db2`;
- immediate independent rerun: **byte-identical**, same SHA-256.

No option outcomes, P&L, or later trade result was used to select the 186 arms.

## Source result

Authoritative delayed SIP first-break classes:

- reversal: **73**;
- continuation: **89**;
- no break: **24**;
- blocked: **0**.

IEX first-break classes:

- reversal: **71**;
- continuation: **86**;
- no break: **29**;
- blocked: **0**.

Reconciliation:

- confirmed same reversal: **69**;
- explicit miss / no provisional: **115**;
- rejected because SIP continuation broke first: **2**;
- blocked: **0**;
- negative-latency source inconsistency: **0**.

Therefore:

- provisional reversal confirmation: **69/71 = 97.18%**;
- recall of SIP-authoritative reversals: **69/73 = 94.52%**;
- false provisional reversals: **2/71 = 2.82%**;
- SIP reversals missed by the provisional policy: **4/73 = 5.48%**.

Confirmed reversals include **32 LONG / 37 SHORT** and occur in all five frozen sessions.

## Timing difference remains material

For the 69 confirmed reversals, IEX minus SIP first-cross latency was:

- min: **0.000s**;
- median: **3.450s**;
- p90: **93.290s**;
- p95: **183.659s**;
- max: **479.095s**;
- negative latency: **0**.

Fixed descriptive buckets:

- <=1s: 24/69;
- <=5s: 38/69;
- <=15s: 45/69;
- <=30s: 53/69;
- <=60s: 58/69;
- <=90s: 61/69;
- <=120s: 64/69.

The long tail is why this result cannot be relabeled as SIP equivalence.

## Explicit differences

False provisional reversals:

- 2026-09-10 MRK 17:30Z watch: IEX saw SHORT reversal first; SIP had LONG continuation first.
- 2026-09-11 COIN 19:30Z watch: IEX saw LONG reversal first; SIP had SHORT continuation first.

Four SIP reversals were not emitted as provisional IEX reversals:

- 2026-09-09 COIN 16:30Z: SIP SHORT reversal; IEX no break.
- 2026-09-09 PLTR 16:00Z: SIP SHORT reversal; IEX no break.
- 2026-09-11 JPM 16:00Z: SIP SHORT reversal; IEX no break.
- 2026-09-15 GE 19:00Z: SIP LONG reversal; IEX first showed same-direction SHORT continuation.

These rows remain explicit misses/rejections. They are not backfilled or rewritten to the SIP timestamp.

## What this resolves

1. The causal 1-2-2 source population is observable under the same miss-allowed IEX + delayed-SIP research architecture previously tested for 212R.
2. Exact-SIP equivalence is still false; confirmed rows must remain a distinct source cohort.
3. The old first-sight 1-2-2 signal now has a technically feasible path to **prospective causal observation**, but it still does not have option expectancy proof.
4. The current 1-2-2 options target formula remains unresolved. The futures 2R convention is not silently imported.

## Next gate

Do **not** add 1-2-2 to V1 yet.

Before any prospective collector is scheduled or deployed, separately freeze:

1. IEX-trigger-to-selector-evidence capture-lag eligibility;
2. collector cadence / polling load;
3. an observation-only 1-2-2 release that cannot mutate scanner/risk/broker/order state;
4. the option-evidence fields to capture at the provisional trigger;
5. delayed-SIP reconciliation and cohort separation;
6. the primary causal structural endpoint. The existing first-sight study used 1R as its primary structural endpoint; any causal follow-up must pre-register its endpoint before collecting new sessions rather than choosing it after outcomes.

Target/runner management is a later strategy question. Source feasibility alone does not authorize it.

**No proof, no trade.**
