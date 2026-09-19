# 1-2-2 provisional IEX observer reconciliation study — preregistration

Date: 2026-09-18

## Purpose

Test whether IEX can support a **miss-allowed, research-only provisional 1-2-2 reversal observer** whose candidates are later reconciled against delayed consolidated SIP.

This is a source/timing study only. It does **not** validate 1-2-2 option expectancy, choose an option target, authorize a detector, activate V1, reserve risk, schedule a collector, route to a broker, or permit DEMO/live trading.

The existing three-session first-sight study classified `OTHER:strat_122` as `PERSISTING POSSIBLE SIGNAL` on 38 prospective episodes with +20.9 pp ex-opening first-sight 1R excess. That result is hypothesis-level evidence only and uses the old delayed first-sight clock. This study does not re-score that result or treat it as causal entry evidence.

## Frozen population

Use every outcome-independent 1-2-2 **ARMED** 30-minute RTH watch window in the existing primary-20 frozen trigger-bar snapshot for 2026-09-09 through 2026-09-15.

- snapshot manifest SHA-256: `8d9cd8a4674827abc11bf60805d53ca812c738bb28417b672a10e3f98790dc1b`
- symbols: existing primary 20
- structural 1-2-2 arms: **186**
- arm count by session: 2026-09-09 **39**, 09-10 **35**, 09-11 **43**, 09-14 **34**, 09-15 **35**
- reference direction: **97 two_up / 89 two_down**
- selection uses completed 30-minute structure only; no outcome filter

The existing trigger-time audit already showed 73 causal 1-2-2 reversal triggers versus 60 old close-classified 1-2-2 rows, with 13 additional reversals that later disappeared from the close-classified population. That timing result is context/provenance only; it is not the source-policy denominator for this study.

## Structural semantics

A 1-2-2 arm exists when a completed directional 2 follows an inside bar. The completed directional 2 supplies the fixed high/low watch boundaries and its direction is the reference direction.

The immediately following 30-minute watch window is resolved causally:

- first strict break **opposite** the reference 2 direction -> provisional 1-2-2 reversal candidate;
- first strict break in the **same** direction -> cancellation / continuation-first, no reversal candidate;
- no break -> explicit miss/no-trigger;
- equality does not count;
- simultaneous first crossings, unknown trade conditions, malformed timestamps, or truncated pagination fail closed.

This study evaluates only source observability of that causal reversal rule. The 1-2-2 target formula remains unresolved for the options lane. No 2R target convention is silently imported from the futures implementation.

## Source semantics

Authoritative delayed source: Alpaca consolidated SIP trades using the same price-forming trade eligibility semantics as `options_trigger_trade_timestamp_audit.py`.

Provisional source: Alpaca IEX trades using the **same strict-through boundary and trade-eligibility semantics**.

SIP may be used only after the provisional IEX decision has been formed. Delayed SIP cannot retroactively create an IEX candidate.

## Provisional observer rule

IEX emits a provisional 1-2-2 reversal only when its first eligible boundary break is opposite the armed directional 2.

- IEX same-direction break first -> no provisional reversal.
- IEX no break -> explicit MISS; do not synthesize/backfill later.
- IEX ambiguous/source-invalid -> `DATA_BLOCKED`.
- No SIP information may influence provisional emission.

## Delayed SIP reconciliation

For each provisional IEX reversal:

- `CONFIRMED_SAME_REVERSAL`: SIP first break is the same reversal side.
- `REJECTED_SIP_CONTINUATION_FIRST`: SIP first break is the same-direction side.
- `REJECTED_SIP_NO_BREAK`: SIP has no valid break in the watch window.
- `REJECTED_DIRECTION_MISMATCH`: both show reversals but on different sides.
- `SOURCE_INCONSISTENT_NEGATIVE_LATENCY`: IEX timestamp predates the authoritative SIP crossing for the same confirmed reversal.
- `DATA_BLOCKED`: either source cannot prove ordering.

Rejected/missed rows remain provenance and never enter a confirmed cohort. Reconciliation never rewrites the IEX decision timestamp to the SIP timestamp.

## Pre-registered measurements

Report without tuning:

1. SIP reversal / continuation / no-break / blocked counts across all 186 arms.
2. IEX reversal / continuation / no-break / blocked counts.
3. Provisional confirmation rate = confirmed / IEX provisional reversals.
4. Recall against SIP-authoritative reversals = confirmed / SIP reversals.
5. Explicit IEX misses of SIP reversals.
6. False provisional reversals by reconciliation reason.
7. Direction/session concentration for confirmed and missed SIP reversals.
8. IEX minus SIP first-cross latency for confirmed rows: min, median, p90, p95, max.
9. Negative-latency count separately.
10. Fixed descriptive latency buckets: <=1s, <=5s, <=15s, <=30s, <=60s, <=90s, <=120s.

## Interpretation rule

Allowed study verdicts are frozen before the source queries:

- `MISS_ALLOWED_RESEARCH_OBSERVER_FEASIBLE`: no blocked rows and no negative-latency source inconsistency; IEX/SIP differences remain explicit misses/rejections.
- `PROVISIONAL_SOURCE_NOT_RELIABLE_ENOUGH_FOR_RESEARCH`: any negative-latency source inconsistency is observed.
- `DATA_BLOCKED`: any row cannot be resolved under the frozen source semantics.

Even `MISS_ALLOWED_RESEARCH_OBSERVER_FEASIBLE` means only that a distinct provisional/reconciled research cohort can be formed. It does **not** mean IEX is SIP-equivalent, that 1-2-2 has an options edge, or that a prospective collector should be deployed automatically.

No production capture-lag threshold or timer cadence is selected by this study.

**No proof, no trade.**
