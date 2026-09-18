# Options historical quote source audit — 2026-09-18

## Verdict

The historical options backtest remains **DATA BLOCKED** on complete real decision-time option-chain evidence. Historical bid/ask entitlement is now proven with an isolated test-only Polygon/Massive credential, but the repository still does not have a complete decision-time selector dataset for the 212R population because historical Greeks/open interest and exact underlying-price provenance remain unproven.

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
- Historical AMZN stock quote access under the same credential returned HTTP 403 / `NOT_AUTHORIZED`, so stock/underlying historical quote entitlement is not established by this key.

Massive's official options documentation describes `GET /v3/quotes/{optionsTicker}` as historical bid/ask quote history with precise timestamps. That specific bid/ask entitlement blocker is therefore cleared for the isolated test credential, but only for options quote history.

Sources:

- https://massive.com/docs/rest/options/overview
- https://massive.com/docs/flat-files/options/quotes

## Important selector gap

The frozen selector requires bid, ask, volume, open interest, and delta at the decision boundary. Historical bid/ask access alone is not enough to claim full selector replay. Massive's historical quote endpoint covers quote history; its snapshot products separately expose Greeks, IV, open interest, and the current underlying price. This audit has not established a decision-time historical source for every selector field, and the isolated test credential does not currently establish historical stock-quote entitlement for the underlying. None of those missing values will be synthesized or back-filled from future/current snapshots.

## Safe work completed around the blocker

PR #686 adds an offline manifest materializer that consumes only already-normalized retained-quote JSONL, rejects path/schema/provenance drift, verifies exact dataset bytes, and atomically emits canonical `option_quotes_manifest.json` bytes. It does not fetch provider data and cannot make the missing historical dataset appear.

The same PR adds an outcome-independent acquisition-index builder. Against the frozen local `FAMILY_POPULATION_MASTER.csv`, it selected all **81** primary-20 `STRAT_212_REVERSAL` structural episodes across 2026-09-09..15 using only family/universe identity and first-sight timestamps. It produced **81 unique symbol+decision timestamps** (62 symbol-session pairs), SHA-256 `44524ad167aa522f7291a9031aca92d71aec0fd5879d4339a74107dfa34bff16`. A regression proves changing outcome/MFE fields cannot change membership or serialized bytes. The generated index remains local evidence; it was not added to the preserved #653 package.

## Next evidence step

Historical option bid/ask access now exists, so the next step remains a minimal complete decision-time sample before any broad pull:

1. use one frozen 212R acquisition-index decision as the sample boundary;
2. retrieve exact historical option bid/ask evidence for candidate contracts at that timestamp;
3. prove decision-time sources for the remaining selector fields (especially delta/open interest and the underlying price) without using current/future snapshots;
4. normalize through the existing quote-retention rule without inference;
5. materialize + hash the dataset and manifest through PR #686 tooling;
6. run selector/fill parity on those exact bytes;
7. expand only after the one-sample proof passes.

Until then, no historical options backtest result should be treated as executable-price evidence, and `strat_212_reversal_30m_options` remains **BLOCKED / WAIT**.
