# 212R provisional IEX observer + delayed SIP reconciliation — results

Date: 2026-09-18

## Verdict

**MISS-ALLOWED RESEARCH OBSERVER FEASIBLE / NOT SIP-EQUIVALENT / NOT DEPLOYED.**

The preregistered all-arm test supports IEX as a separate, incomplete **provisional research clock** only when every emitted reversal is later reconciled against consolidated SIP and rejected rows remain excluded from the confirmed cohort.

It does **not** support replacing the exact-SIP 212R trigger clock with IEX.

This source-policy result is unchanged. **Current overall 212R classification: UNPROVEN / WAIT.** PR #761 later classified the separate first-sight family-persistence lane as `NO LONGER SHOWING EXCESS`; that does not alter this IEX + delayed-SIP source result.

## Why this study is stronger than the earlier 81-row comparison

The earlier #742 comparison started from 81 already SIP-confirmed frozen reversals. That was enough to reject IEX/SIP equivalence, but not enough to measure false provisional reversals because the denominator was conditioned on SIP reversal outcomes.

This study was preregistered before the new source queries and starts from **every structurally ARMED 2-1-2 watch window** in the frozen primary-20 snapshot:

- sessions: 2026-09-09 through 2026-09-15;
- frozen 212 arms: **183**;
- snapshot manifest SHA-256: `8d9cd8a4674827abc11bf60805d53ca812c738bb28417b672a10e3f98790dc1b`;
- no outcome filter.
Both IEX and delayed SIP use the same strict-through, price-forming trade semantics. Equality does not count. Unknown source semantics fail closed.

## Authoritative SIP population

Exact delayed SIP ordering across the 183 arms produced:

- **91 reversal-first**;
- **66 continuation-first**;
- **26 no break**;
- **0 data blocked**.

This is intentionally broader than the older frozen 81 reversal set because exact source ordering is evaluated from all 183 arms rather than selecting on the old reversal outcome.

## IEX provisional population

IEX first-boundary observation produced:

- **90 provisional reversal-first**;
- **61 continuation-first**;
- **32 no break**;
- **0 data blocked**.

After delayed SIP reconciliation of those 90 provisional reversals:

- **89 confirmed same reversal**;
- **1 rejected because SIP continuation broke first**;
- **0 rejected for SIP no-break**;
- **0 direction-mismatch rejects**;
- **0 source-inconsistent negative-latency rows**.

Therefore:

- provisional confirmation rate: **89/90 = 98.9%**;
- recall of SIP-authoritative reversals: **89/91 = 97.8%**;
- false provisional reversals: **1/90 = 1.1%**;
- missed SIP reversals: **2/91 = 2.2%**.

Confirmed cohort breadth:

- LONG: **48**;
- SHORT: **41**;
- confirmations occurred in **all five sessions**.

## The one false provisional proves reconciliation is mandatory

`2026-09-09 NVDA`, watch start `18:00Z`:

- reference direction: `2U`;
- SIP first boundary: **HIGH / LONG continuation** at `18:05:04.304470466Z`;
- IEX first boundary: **LOW / SHORT reversal** at `18:16:00.160933702Z`.
An IEX-only rule would have emitted a reversal. Delayed consolidated SIP correctly rejects it because the continuation side had already broken first.

Therefore IEX provisional rows must never become confirmed 212R evidence before SIP reconciliation.

## The two full-window IEX misses

Two SIP-authoritative reversals had no IEX boundary break anywhere in the full 30-minute armed window:

- 2026-09-09 COIN, 18:00Z watch, LONG;
- 2026-09-11 IWM, 15:30Z watch, LONG.

They remain explicit misses. The policy does not backfill them later from SIP.

## Timing is still materially different from SIP

For the **89 confirmed** same-reversal rows, IEX minus SIP first-cross latency was:

- minimum: **0.000s**;
- median: **3.501s**;
- p90: **95.469s**;
- p95: **156.273s**;
- maximum: **569.811s**;
- negative latency: **0**.

Fixed descriptive buckets:
| IEX lag from SIP | Confirmed rows | % of 89 confirmed |
|---|---:|---:|
| <= 1s | 24 | 27.0% |
| <= 5s | 51 | 57.3% |
| <= 15s | 61 | 68.5% |
| <= 30s | 70 | 78.7% |
| <= 60s | 76 | 85.4% |
| <= 90s | 79 | 88.8% |
| <= 120s | 82 | 92.1% |

The long tail is why this result **does not convert IEX into the exact-SIP decision clock**. Six of the eight same-bucket misses from #742 eventually produced the same reversal on IEX later in the 30-minute watch window; that improves miss-allowed observer coverage while simultaneously demonstrating that some IEX captures can be minutes later than the actual SIP break.

No production lag threshold is selected here.

## Reproducibility

Primary result:

`data/options_212r_iex_provisional_audit_2026_09_18/result.json`

SHA-256:

`a11b3bedf2bf724618ae7e45f74e5ad330bbc36b8a206dec882a35f8f0026ee0`
A complete second provider run produced the exact same SHA-256 and byte-identical JSON.

The audit script fails closed on:

- frozen snapshot hash/count drift;
- duplicate arm identity;
- unknown trade conditions;
- malformed timestamps;
- pagination truncation;
- simultaneous exact boundary breaks;
- SIP 5m/exact-trade direction disagreement;
- source query/semantic errors.

## What this permits

This result supports a **separate prospective research cohort** with these semantics:

1. arm 212 structure before the event;
2. allow IEX to emit a provisional reversal;
3. preserve the IEX decision timestamp and any decision-time option evidence;
4. later reconcile against delayed SIP;
5. keep only SIP-confirmed same-reversal rows in the confirmed research cohort;
6. retain rejected and missed rows explicitly;
7. never rewrite the IEX timestamp to the earlier SIP timestamp.
## What this does not permit

This study does not authorize:

- calling IEX SIP-equivalent;
- merging IEX-confirmed rows into the exact-SIP cohort as if captured at SIP time;
- a collector service/timer;
- a numeric capture-lag threshold;
- scanner/risk/broker mutation;
- paper fills, DEMO, or live execution;
- 212R option expectancy or strategy promotion.

Historical exact option replay also remains separately DATA BLOCKED on causal historical Delta and contract-level open interest.

**No proof, no trade.**
