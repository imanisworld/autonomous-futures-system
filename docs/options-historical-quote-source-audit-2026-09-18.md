# Options historical quote source audit — 2026-09-18

## Verdict

The historical options backtest remains **DATA BLOCKED** on real decision-time option-chain evidence. The repository now has deterministic retention/manifest/parity machinery, but no acceptable real historical option-quote dataset for the 212R decision population.

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

### Existing Polygon/Massive credential entitlement

Read-only entitlement probes were performed without printing or persisting the credential.

- Options reference contracts: HTTP 200 / API `OK`.
- Historical options quotes `GET /v3/quotes/{optionsTicker}`: HTTP 403 / API `NOT_AUTHORIZED` (`not entitled to this data`).
- Current option-contract snapshot: HTTP 403 / API `NOT_AUTHORIZED`.

Massive's official options documentation does expose historical quote data at `GET /v3/quotes/{optionsTicker}` and describes it as bid/ask quote history with precise timestamps. The currently configured credential cannot access it.

Sources:

- https://massive.com/docs/rest/options/overview
- https://massive.com/docs/flat-files/options/quotes

## Important selector gap

The frozen selector requires bid, ask, volume, open interest, and delta at the decision boundary. Historical bid/ask access alone is not enough to claim full selector replay. The reviewed Massive documentation describes historical quote records separately from snapshot analytics such as Greeks, IV, and open interest. This audit did not establish a historical decision-time source for every selector field, so none will be synthesized or back-filled from future/current snapshots.

## Safe work completed around the blocker

PR #686 adds an offline manifest materializer that consumes only already-normalized retained-quote JSONL, rejects path/schema/provenance drift, verifies exact dataset bytes, and atomically emits canonical `option_quotes_manifest.json` bytes. It does not fetch provider data and cannot make the missing historical dataset appear.

## Next evidence step

Once an authorized historical source exists, start with a minimal real decision-time sample before any broad pull:

1. retrieve the actual chain/quote evidence required by the frozen selector at one historical decision timestamp;
2. prove all required selector fields are decision-time valid and source-attributed;
3. normalize through the existing quote-retention rule without inference;
4. materialize + hash the dataset and manifest through PR #686 tooling;
5. run selector/fill parity on those exact bytes;
6. expand only after the one-sample proof passes.

Until then, no historical options backtest result should be treated as executable-price evidence, and `strat_212_reversal_30m_options` remains **BLOCKED / WAIT**.
