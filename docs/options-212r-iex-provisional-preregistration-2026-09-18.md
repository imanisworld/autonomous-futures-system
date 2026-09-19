# 212R provisional IEX observer reconciliation study — preregistration

Date: 2026-09-18

## Purpose

Test whether IEX can support a **miss-allowed, research-only provisional 212R observer** whose candidates are later reconciled against delayed consolidated SIP.

This is not a test of IEX/SIP equivalence and cannot authorize 212R, DEMO, paper fills, live execution, scanner activation, risk reservation, broker routing, scheduling, or deployment.

## Frozen population

Use every outcome-independent 2-1-2 **ARMED** 30-minute RTH watch window in the existing primary-20 frozen trigger-bar snapshot for 2026-09-09 through 2026-09-15.

- snapshot manifest SHA-256: `8d9cd8a4674827abc11bf60805d53ca812c738bb28417b672a10e3f98790dc1b`
- symbols: existing primary 20
- structural 212 arms: **183**
- selection uses completed 30-minute structure only; no outcome filter

The previously merged 81-row IEX-vs-SIP comparison is context/provenance only. It is not the denominator for this study because it was conditioned on SIP-confirmed frozen reversal events.

## Source semantics

For each frozen 212 arm, retain its fixed high/low boundaries and reference 2 direction.

Authoritative delayed source: Alpaca consolidated SIP trades using the same price-forming trade eligibility semantics as `options_trigger_trade_timestamp_audit.py`.

Provisional source: Alpaca IEX trades using the **same strict-through boundary and trade-eligibility semantics**.

A high boundary break is LONG; a low boundary break is SHORT. Equality prints do not count. Unknown trade conditions, truncated pagination, malformed timestamps, or simultaneous first crossings fail closed.

## Provisional observer rule

IEX may emit a provisional 212R candidate only when its first eligible boundary break in the armed 30-minute watch window is opposite the reference 2 direction.

- IEX continuation first -> no provisional reversal candidate.
- IEX no break -> explicit MISS; do not synthesize or backfill a candidate later.
- IEX ambiguous/source-invalid -> DATA_BLOCKED.
- No current or delayed SIP information may be used to decide whether the IEX candidate is emitted.

## Delayed SIP reconciliation rule

After the SIP window becomes queryable, classify each provisional IEX reversal:

- `CONFIRMED_SAME_REVERSAL`: SIP first break is the same reversal side.
- `REJECTED_SIP_CONTINUATION_FIRST`: SIP first break is the continuation side.
- `REJECTED_SIP_NO_BREAK`: SIP has no valid boundary break in the watch window.
- `DATA_BLOCKED`: SIP ordering cannot be proven.

A rejected provisional candidate remains provenance but must never enter the confirmed evidence cohort. Reconciliation must not rewrite the IEX decision timestamp into the SIP timestamp.

## Pre-registered measurements

Report, without tuning:

1. SIP-authoritative reversal / continuation / no-break / blocked counts across all 183 arms.
2. IEX provisional reversal / continuation / no-break / blocked counts.
3. Provisional reversal confirmation rate: confirmed / IEX provisional reversals.
4. Recall against SIP-authoritative reversals: confirmed / SIP reversals.
5. Explicit IEX misses of SIP reversals.
6. False provisional reversals, split by SIP continuation-first vs SIP no-break.
7. Direction/session concentration for confirmed and missed rows.
8. IEX minus SIP first-cross latency for confirmed rows: min, median, p90, p95, max; negative latency count is reported separately and treated as source inconsistency rather than silently accepted.
9. Fixed descriptive latency buckets: <=1s, <=5s, <=15s, <=30s, <=60s, <=90s, <=120s. These are diagnostics, not deployment thresholds.

## Interpretation rules

This study may support only one of these conclusions:

- `MISS_ALLOWED_RESEARCH_OBSERVER_FEASIBLE`: a deterministic IEX-first / delayed-SIP-reconcile cohort can be formed with explicit misses/rejections and no hidden substitution.
- `PROVISIONAL_SOURCE_NOT_RELIABLE_ENOUGH_FOR_RESEARCH`: reconciliation failure, source inconsistency, or false-provisional behavior is too severe to justify a separate prospective cohort.
- `DATA_BLOCKED`: source/semantic completeness is insufficient to decide.

Even the first conclusion does **not** make IEX SIP-equivalent and does not validate exact-SIP 212R expectancy. A prospective IEX cohort would be a distinct source policy and must stay separate from exact-SIP evidence.

No numeric production capture-lag or timer cadence will be selected by this study.
