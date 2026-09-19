# 212R IEX vs consolidated SIP trigger study — 2026-09-18

## Verdict

**IEX IS NOT SOURCE-EQUIVALENT TO CONSOLIDATED SIP FOR THE FROZEN 212R TRIGGER CLOCK.**

Do not silently substitute IEX for SIP in collector v0.3.

The study is useful only as a source-coverage comparison and as evidence for a possible future **miss-allowed provisional observer** design that would require explicit policy and later SIP reconciliation.

## Follow-up status

The “future design” proposed by this 81-row source-equivalence study has now been tested separately in #756 on an outcome-independent denominator of **183 structurally ARMED 212 windows**.

That preregistered study supports IEX only as a **miss-allowed provisional research observer with mandatory delayed-SIP reconciliation**:

- 90 IEX provisional reversals;
- 89 confirmed by delayed SIP;
- 1 false provisional rejected because SIP continuation broke first;
- 2 of 91 SIP-authoritative reversals missed by IEX;
- confirmed timing still has a long tail (median 3.501s, p95 156.273s, max 569.811s).

See `docs/options-212r-iex-provisional-results-2026-09-18.md`.

This does not change this document's original conclusion: IEX is **not** consolidated-SIP equivalent. The 81-row study remains provenance for that rejection; #756 is the authority for the narrower provisional-observer result.

## Frozen population

Population: exact frozen 81-row 2-1-2 reversal set.

Authoritative consolidated-SIP event source: `data/options_trigger_trade_timestamp_audit_2026_09_18/events.jsonl`.

For each frozen event, historical Alpaca IEX trades were queried only over the same proven five-minute trigger bucket. The same strict boundary semantics were applied:

- LONG: first eligible trade with price strictly **> trigger**;
- SHORT: first eligible trade with price strictly **< trigger**.

The first IEX break of either frozen boundary was compared with the authoritative consolidated-SIP first break.

## Result

Total frozen events: **81**

IEX same first-break direction: **73/81 (90.1%)**

IEX opposite-side-first: **0/81**

IEX no cross in the same five-minute bucket: **8/81 (9.9%)**

Exact IEX/SIP trigger timestamp match: **4/81**

Among the 73 matched-direction rows, IEX was never earlier than SIP in this study.

IEX delay relative to consolidated SIP on matched rows:

- minimum: **0.000s**
- median: **3.501s**
- p90: **51.065s**
- p95: **71.887s**
- maximum: **132.851s**

Coverage measured against all 81 SIP-confirmed events:

- same-side IEX cross within 1s: **20/81 (24.7%)**
- within 5s: **43/81 (53.1%)**
- within 15s: **53/81 (65.4%)**
- within 30s: **61/81 (75.3%)**
- within 60s: **67/81 (82.7%)**
- within 90s: **70/81 (86.4%)**
- within 120s: **72/81 (88.9%)**

## Eight SIP-confirmed events missed by IEX in the same bucket

- 2026-09-09 AMZN SHORT
- 2026-09-09 COIN LONG
- 2026-09-10 NFLX LONG
- 2026-09-11 IWM LONG
- 2026-09-11 WMT LONG
- 2026-09-11 XOM LONG
- 2026-09-14 COIN SHORT
- 2026-09-15 QQQ LONG

## Reproducibility

The entire comparison was run twice.

Primary comparison SHA-256:

`bb98b447f5f51fd09d680df9b2bd983f5a1d540f27795f38fe2e3f4cb3bb1b96`

Repeat comparison SHA-256:

`bb98b447f5f51fd09d680df9b2bd983f5a1d540f27795f38fe2e3f4cb3bb1b96`

The JSON outputs were byte-identical, including all 81 event rows and summary statistics.

Repository artifact:

`data/options_212r_iex_vs_sip_2026_09_18/comparison.json`

## Interpretation

This study does **not** support treating IEX as consolidated-SIP equivalent.

The encouraging part is that IEX produced no opposite-side-first event in the frozen 81. But it missed 8 SIP-confirmed breaks entirely in the same five-minute trigger bucket and was materially late on several others. That is enough to reject an unqualified substitution.

That narrower design has now been pre-registered and validated separately in #756 as a **miss-allowed provisional research observer with mandatory delayed-SIP reconciliation**. It remains a distinct source policy, is not deployed, and does not make IEX SIP-equivalent.

## Safety

Research only.

No collector service/timer, scanner mutation, risk reservation, broker/account endpoint, order route, DEMO, or live execution was added.

**212R remains PROMISING BUT UNPROVEN / WAIT.**
