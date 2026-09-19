# Options historical quote source audit — 2026-09-18

## Verdict

The historical options backtest remains **DATA BLOCKED** on complete real decision-time option-chain evidence. Historical option bid/ask entitlement is proven with an isolated test-only Polygon/Massive credential, and #733/#738 now prove the exact causal underlying trigger-cross time/price plus the production `context.price -> normalized price -> OPTIONS_PAPER_V1` path. The remaining causal selector-data gaps are historical decision-time **Delta** and contract-level **open interest**.

## What was checked

### Repository/local evidence

- No real historical option quote/chain JSONL dataset is present in the repository for 212R.
- Existing quote datasets in tests are fixtures only.
- The preserved 212R gate package explicitly states that it contains underlying data only and no option quote/contract/premium dataset.

### Public Individual API

Official documentation checked:

- `POST /userapigateway/marketdata/{accountId}/option-chain` returns current option-chain quotes and exposes `bidTimestamp` and `askTimestamp`.
- `POST /userapigateway/marketdata/{accountId}/quotes` returns real-time quotes and exposes `bidTimestamp` and `askTimestamp`.
- `GET /userapigateway/historicdata/{type}/{symbol}/{period}` supports `OPTION`, but the documented historical payload is bar/OHLCV data rather than an executable bid/ask quote stream.

Therefore Public's documented historical bar endpoint is not a substitute for Item-2 decision-time bid/ask quote retention. No bid/ask is to be reconstructed from OHLC bars.

Sources:

- https://public.com/api/docs/resources/market-data/get-option-chain
- https://public.com/api/docs/resources/market-data/get-quotes
- https://public.com/api/docs/resources/market-data/get-bars-v2

### Polygon/Massive credential entitlement

Read-only entitlement probes were performed without printing the credential or committing it to git.

The normal repository-local `POLYGON_API_KEY` remains unchanged and was previously observed returning `NOT_AUTHORIZED` for historical option quotes.

A separate isolated test-only credential supplied on 2026-09-18 was stored only in ignored local `.env.test-20260918` with mode `600`. Using that credential:

- Options reference contracts: HTTP 200 / API `OK`.
- Historical options quotes `GET /v3/quotes/{optionsTicker}`: HTTP 200 / API `OK`.
- A real 2026-09-10 SPY option quote query returned bid, ask, and nanosecond SIP timestamp data.
- The merged read-only entitlement probe (`scripts/options_polygon_historical_quote_probe.py`, PR #698) independently returned `ENTITLED` / HTTP 200 / API `OK` against the same isolated key.
- Historical AMZN stock quote access under the same credential returned HTTP 403 / `NOT_AUTHORIZED`. That Massive stock entitlement gap is no longer the 212R underlying-price blocker because #733 independently resolves exact causal trigger-cross trades for 81/81 frozen rows from Alpaca SIP, and #738 proves that causal price can traverse the production selector price path with replay parity.

Massive's official options documentation describes `GET /v3/quotes/{optionsTicker}` as historical bid/ask quote history with precise timestamps. That specific bid/ask entitlement blocker is therefore cleared for the isolated test credential, but only for options quote history.

Sources:

- https://massive.com/docs/rest/options/overview
- https://massive.com/docs/flat-files/options/quotes

## Important selector gap

The frozen selector requires bid, ask, volume, open interest, and delta at the decision boundary. Historical bid/ask access plus exact causal underlying trigger price still is not enough to claim full selector replay. Massive's historical quote endpoint covers quote history; its snapshot products expose current Greeks/IV/open interest, not a proven historical decision-time snapshot for the frozen selector. Therefore the remaining unresolved selector fields are historical decision-time **Delta** and contract-level **open interest**. Neither will be synthesized, model-derived, or back-filled from future/current snapshots.

## External historical-data candidates

The external-source qualification has now been tightened in `docs/options-historical-delta-oi-source-qualification-2026-09-18.md`.

Read-only vendor documentation establishes three technically relevant paths, but none is currently configured or authorized:

- **ThetaData Standard** is the preferred technical pilot candidate. Its documented historical first-order Greeks support sub-minute intervals down to tick/10ms/100ms/500ms/1s for single-day requests and return option timestamp, bid/ask, Delta, underlying timestamp, and underlying price. Its historical open-interest endpoint documents the daily OPRA value reported around 06:30 ET for the prior trading day's EOD OI. This is the strongest temporal fit to the exact #733 trigger boundary, but its Delta is ThetaData-calculated and therefore still requires cross-provider selector parity before historical use.
- **ORATS Intraday Data API** is a viable fallback. Its one-minute chain rows include call/put bid/ask, volume, open interest, and a vendor Delta; ORATS documents that the published Delta is call Delta and put Delta is call Delta minus 1. The one-minute resolution and vendor-model semantics mean it is not a drop-in reconstruction of current Public analytics.
- **Cboe DataShop Option Quote Intervals** can provide one-minute NBBO/volume with optional OI and Greeks, but remains a purchased archival source and has coarser standard timing than the exact trigger boundary.

No `THETA*`, `ORATS*`, `CBOE*`, or `DATABENTO*` credentials/configuration are present in the current VPS environment. Do not purchase or integrate any of these sources implicitly. A provider choice must be explicit; field-level provenance and a forward selector-parity study against current Public are mandatory before an external Delta/OI source can retire the historical blocker.

## Safe work completed around the blocker

PR #686 adds an offline manifest materializer that consumes only already-normalized retained-quote JSONL, rejects path/schema/provenance drift, verifies exact dataset bytes, and atomically emits canonical `option_quotes_manifest.json` bytes. It does not fetch provider data and cannot make the missing historical dataset appear.

The same PR adds an outcome-independent acquisition-index builder. Against the frozen local `FAMILY_POPULATION_MASTER.csv`, it selected all **81** primary-20 `STRAT_212_REVERSAL` structural episodes across 2026-09-09..15 using only family/universe identity and first-sight timestamps. It produced **81 unique symbol+decision timestamps** (62 symbol-session pairs), SHA-256 `44524ad167aa522f7291a9031aca92d71aec0fd5879d4339a74107dfa34bff16`. A regression proves changing outcome/MFE fields cannot change membership or serialized bytes. The generated index remains local evidence; it was not added to the preserved #653 package.

## Next evidence step

Historical option bid/ask access and exact causal underlying trigger price now exist. The remaining next step is no longer "find any source"; it is an explicit source-acceptance decision followed by a one-sample parity proof.

1. keep the exact frozen 212R trigger timestamp/price from #733/#738 as the decision boundary;
2. if the operator explicitly authorizes an external source, start with **one frozen AMZN sample only**;
3. retrieve causal per-contract Delta/OI for the selector-relevant chain, requiring every source timestamp to be at or before the trigger;
4. keep Massive bid/ask/volume unless a separate source-switch decision is made, and preserve field-level provenance;
5. run the real production selector on the exact frozen underlying price and record Delta-band/OI-floor eligibility plus the selected contract;
6. run a forward cross-provider selector-parity capture against current Public before treating the alternate Delta/OI semantics as accepted;
7. only after those checks pass, expand to the frozen 81-row acquisition index, materialize/hash the dataset through PR #686 tooling, then run selector/fill parity and expectancy.

The preferred **technical pilot** is ThetaData Standard because its documented historical first-order Greeks have sub-minute timing plus explicit option/underlying timestamps and its historical OI semantics are documented. This is not authorization to subscribe, purchase, or integrate.

Until an external source is explicitly authorized and parity-tested, no historical options backtest result should be treated as executable-price evidence, and `strat_212_reversal_30m_options` remains **BLOCKED / WAIT**.
