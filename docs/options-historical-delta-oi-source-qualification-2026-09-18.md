# Historical Delta/OI source qualification — 2026-09-18

## Verdict

**SOURCE QUALIFICATION COMPLETE / DATA ACCESS STILL BLOCKED.**

The remaining historical 212R selector blocker is now narrowed to an external-data decision, not another internal selector/fill implementation defect.

The frozen 212R replay already has:

- exact causal trigger-cross time and underlying price for 81/81 rows (#733);
- production trigger-price path parity for the frozen AMZN sample (#738);
- historical option bid/ask entitlement from the isolated Massive/Polygon credential;
- causal historical option volume via minute aggregates for the one-sample path.

What is still missing is a causal historical source for:

1. per-contract **Delta** at or before the exact trigger boundary;
2. per-contract **open interest** that was actually available on that trade date.

No currently configured provider on the VPS supplies both fields with proven historical semantics. No THETA, ORATS, CBOE, or DATABENTO credential/configuration is present. No purchase, subscription, credential change, or runtime integration was authorized by this audit.

## Current selector semantics that matter

The production selector rejects missing/low open interest and uses Delta for both quality gating and contract ranking. Therefore a historical source cannot be treated as a cosmetic supplement.

A replacement source must preserve, at minimum:

- option side and contract identity;
- causal timestamp semantics;
- open-interest value available by the decision time;
- signed Delta semantics for CALL vs PUT;
- no future/current snapshot back-fill;
- source attribution on every supplemented field;
- exact production-selector replay after normalization.

Raw Delta equality to the current Public provider is not assumed. Any alternate provider must be treated as a **new analytics source whose selector effect must be parity-tested** before it can retire the blocker.

## Candidate A — ThetaData Standard

Official ThetaData documentation currently shows:

- historical first-order option Greeks are available on **Standard** and Pro;
- the endpoint can return intervals as fine as tick / 10ms / 100ms / 500ms / 1s for a single day;
- each historical Greek row includes option bid, ask, Delta, an option timestamp, an underlying timestamp, and underlying price;
- historical open interest is available on Value/Standard/Pro;
- OPRA open interest is normally reported around 06:30 ET and represents the **previous trading day's end-of-day open interest**;
- Standard options history covers dates from 2016 onward, which covers the frozen September 2026 population.

References:

- https://docs.thetadata.us/Articles/Getting-Started/Subscriptions.html
- https://docs.thetadata.us/operations/option_history_greeks_first_order.html
- https://docs.thetadata.us/operations/option_history_open_interest.html
- https://docs.thetadata.us/Articles/Data-And-Requests/Option-Greeks.html

### Proposed causal mapping if explicitly authorized

For each frozen trigger:

1. use the exact #733 trigger-cross timestamp as the information boundary;
2. query the relevant expiration/strike universe for historical first-order Greeks in a narrow single-day window;
3. for each contract, retain only the latest Greek row whose **option timestamp <= trigger timestamp**;
4. also require the returned **underlying timestamp <= trigger timestamp**;
5. obtain the trade-date open-interest report and require its report timestamp to be available before the trigger; its documented meaning is prior-day EOD OI;
6. preserve Massive as the historical bid/ask + volume source unless a separate source-switch decision is made;
7. retain ThetaData provenance separately on delta and open_interest;
8. fail closed on missing rows, timestamp ambiguity, contract mismatch, or future data.

This source is the **preferred technical pilot candidate** because its documented temporal resolution is finer than one minute and it exposes both the option and underlying timestamps used by its Greek calculation.

That does **not** make ThetaData Delta equivalent to current Public Delta. ThetaData documents its own Black-Scholes Greek calculation methodology. A forward selector-parity study is therefore mandatory before historical use.

## Candidate B — ORATS Intraday Data API

Official ORATS documentation currently shows:

- historical one-minute option-chain data back to August 2020;
- OPRA-specific strike history back to January 2022;
- chain rows include call/put volume, call/put open interest, call/put bid/ask, Delta, and snapshot/quote timestamps;
- the published delta field is **call Delta**; ORATS states put Delta is call Delta minus 1;
- ORATS uses its own SMV/volatility-surface analytics.

References:

- https://orats.com/docs/historical-intraday-api
- https://orats.com/docs/definitions
- https://orats.com/one-minute-data
- https://orats.com/near-eod-data

### Qualification

ORATS can technically supply the missing fields and, unlike a Delta-only supplement, can expose most selector fields in one one-minute chain row.

However:

- its one-minute resolution is coarser than the exact trigger timestamps;
- its Delta is a vendor-model value;
- CALL/PUT Delta requires explicit side mapping: put_delta = call_delta - 1;
- using it would still be a source-semantics change that needs selector parity.

ORATS is therefore a viable fallback/pilot source, but not a drop-in proof of current Public historical analytics.

## Candidate C — Cboe DataShop Option Quote Intervals

Cboe DataShop documents historical Option Quote Intervals with:

- 1-minute or custom N-minute NBBO snapshots;
- option trade volume;
- optional open interest;
- optional implied volatility and Greeks including Delta;
- underlying bid/ask for equities and ETFs;
- historical coverage from January 2012 to present.

Reference:

- https://datashop.cboe.com/option-quote-intervals

### Qualification

This is a technically relevant archival source and could provide a single packaged historical dataset with NBBO, OI, and Greeks. It is still a purchased external dataset, has minute-level rather than exact-trigger granularity in the standard product, and its calculated Greeks remain a different analytics source from current Public.

It remains a plausible archival fallback, not an authorized source.

## Why Massive alone still does not retire the blocker

The isolated Massive credential proves historical option quote access with precise timestamps. That clears historical bid/ask.

It does not prove a historical decision-time Delta/OI snapshot equivalent to the current selector inputs. Current/future snapshot analytics may not be back-filled into September 2026 rows.

Therefore Massive remains useful for:

- historical NBBO bid/ask;
- precise quote timestamps;
- causal minute-volume construction;

but not yet for the two missing selector analytics.

## Pre-registered source-acceptance sequence

No source should be wired into the historical 81-row replay merely because its documentation lists the fields.

If the operator explicitly authorizes a provider/plan or supplies an existing credential, the next sequence is:

1. **One frozen sample only** — use the existing AMZN 2026-09-09 sample with the exact #733 trigger timestamp.
2. Retrieve a causal external Delta/OI candidate set for the full selector-relevant expiration/strike universe.
3. Normalize with field-level provenance; do not replace Massive bid/ask/volume unless separately authorized.
4. Run the real production selector using the exact frozen underlying trigger price.
5. Record which contracts pass/fail the Delta band and OI floor, plus the selected contract.
6. Run a **forward cross-provider selector-parity capture** against current Public using the same underlying/contract universe. Compare selector decisions, not merely raw Greek values.
7. If the one-sample and forward parity checks pass, expand to the frozen 81-row acquisition index.
8. Hash the raw source payloads, normalized retained rows, and manifest before any expectancy calculation.

### Mandatory fail-closed rules

- no provider row after the trigger timestamp;
- no current snapshot substituted for historical Delta/OI;
- no model-derived Delta generated inside this repository;
- no OI inferred from volume or future OI;
- no silent CALL/PUT Delta conversion except a documented provider rule;
- no missing Delta/OI accepted;
- no source mixing without per-field provenance;
- no backtest result promoted before production-selector parity is proven.

## Current decision boundary

**Preferred pilot candidate: ThetaData Standard, if the operator explicitly chooses to obtain/use it.**

Reason: it is the strongest documented temporal fit for the exact trigger boundary and provides both historical first-order Delta and historical OI semantics. This is an engineering source preference, not an authorization to subscribe or integrate.

**Alternative:** ORATS Intraday API if one-minute analytics are accepted and its call/put Delta semantics are explicitly frozen.

**Archival fallback:** Cboe DataShop Option Quote Intervals.

Until a source is explicitly authorized and parity-tested, historical 212R remains:

**DATA BLOCKED / WAIT.**
