# Increment 14 — Polygon Options Client: Design Audit (no implementation)

Status: **design audit only**. No code in this document calls, imports, or
fetches from any external API. Nothing here is wired into the scanner
(`options_manager.scanner`), the replay/review layers (Increments 5-11), the
adapters/row-builder layer (Increment 13), the live `options_companion` lane,
or execution/broker/risk code. Per the Increment 12 audit's own boundary
(referenced in the Increment 13 commit message), this increment's job is to
decide *whether and how* a Polygon-backed adapter could eventually populate
`options_manager.adapters.base`'s source-neutral shapes — not to build it.

## Why this increment exists

Increment 13 defined `AdapterCandle`, `AdapterOptionQuote`,
`AdapterUnderlyingSnapshot`, and `AdapterMarketContextSnapshot` — normalized,
vendor-agnostic shapes a future adapter would populate — plus a pure
`build_watchlist_row_from_adapter_data()` that only translates already-fetched
data. Nothing fetches those shapes yet. This audit asks: if Polygon were the
vendor, what would that adapter look like, what could go wrong, and what is
explicitly *not* in scope — before any of it is built.

## What already exists to build on

`sources/polygon_client.py` (`PolygonFuturesClient`) is a working, read-only
Polygon/Massive **futures** bar client, used only for historical backfill and
replay — never order routing, never live decisions, and nothing in the live
webhook pipeline imports it. Its conventions are the right starting point for
an options-side client:

- **Fail-soft, not fail-loud on missing config**: a `configured` property
  (`bool(self.api_key)`); every fetch raises a typed error (`PolygonError`)
  rather than crashing ambiguously. Callers on optional paths catch and
  continue.
- **Rate-limit discipline for the free tier**: proactive pacing
  (`min_request_interval`) plus reactive 429 backoff honoring `Retry-After`,
  because a bulk/multi-contract fetch can burst straight through a per-minute
  window if only reactive backoff is used.
- **Never fabricates on malformed data**: a row that fails to parse
  (`KeyError`/`TypeError`/`ValueError`) is skipped, not defaulted or guessed.
- **Tests never touch the network**: `tests/test_polygon_client.py` uses
  `httpx.MockTransport` exclusively.

## Interface proposal (design only — not implemented here)

A parallel `PolygonOptionsClient` (or a second class in the same module —
open question, see below) with the same fail-soft/rate-limit/typed-error
shape, exposing methods that return the Increment 13 adapter dataclasses
directly rather than a vendor-shaped dict:

```
class PolygonOptionsClient:
    configured: bool                                   # same pattern as PolygonFuturesClient

    def fetch_underlying_candles(
        ticker: str, start: date, end: date, timeframe_minutes: int,
    ) -> list[AdapterCandle]: ...

    def fetch_option_quote(
        underlying: str, expiration: str, strike: float, contract_type: Literal["call", "put"],
    ) -> AdapterOptionQuote: ...

    def fetch_underlying_snapshot(
        ticker: str,
    ) -> AdapterUnderlyingSnapshot:                     # spot_price only —
                                                         # see scope boundary below
```

Deliberately **not proposed**: any method returning
`AdapterMarketContextSnapshot`. The Increment 12 audit already established
that GEX/gamma regime and Signa direction/grade/score are not natively
supplied by any market-data vendor audited so far — Polygon's options/stocks
endpoints give quotes, chains, and aggregates, not gamma-exposure or Signa
data. A `PolygonOptionsClient` should not attempt to synthesize those fields;
`market_context_snapshot` stays a caller-supplied value from wherever the
system separately obtains GEX (the existing in-house GEX gate,
[[project_gex_gate_dormant]]) and Signa, exactly as Increment 13 already
requires.

## Risk list

- **Cost/tier gating**: Polygon's options endpoints (chain snapshot,
  quotes, Greeks) are commonly paid-tier-only, unlike the futures aggregates
  endpoint already in use. Before writing a single line of client code,
  confirm which tier is actually provisioned and what it does and doesn't
  include (real-time vs. 15-min-delayed quotes matters a lot for a scanner
  meant to catch intraday setups).
- **Rate limits differ by endpoint class**: the futures client's ~5 req/min
  free-tier pacing may not match the options endpoints' actual limits (higher
  tiers have different windows) — this needs to be verified against Polygon's
  current published limits for whatever tier is provisioned, not assumed
  from the futures client's constants.
- **Options contract identifier complexity**: OCC-style option tickers
  (e.g. `O:SPY250117C00600000`) encode underlying, expiration, type, and
  strike in one string with specific padding/format rules — a parsing bug
  here is a silent-wrong-strike class of bug, the options equivalent of the
  nanosecond `window_start` bug this repo already fixed once in
  `bar_history`. Needs dedicated round-trip tests (encode a strike/expiry,
  decode it back, assert equality) before trusting any real chain data.
- **Staleness / halted or thin markets**: unlike liquid index futures, many
  option contracts have wide bid/ask, zero volume, or stale last-trade
  timestamps — `AdapterOptionQuote.spread_percent`/`volume`/`open_interest`
  need well-defined "we don't know" (`None`) semantics rather than a computed
  0% spread from a stale quote.
- **Auth/secret handling**: same `POLYGON_API_KEY` env var as the futures
  client, or a separate key/tier — needs an explicit decision (shared vs.
  isolated credential) before any wiring, and either way must never be
  logged, journaled, or exposed via any `/status/*` endpoint (same standard
  as [[project_webhook_transport_security]]).
- **Scope creep into the live options lane**: `options_companion` already
  has a live paper lane on Public.com's REST API
  ([[project_options_companion_lane]]). A Polygon adapter for the advisory-only
  `strat_212` scanner track must not be confused with, or accidentally
  wired into, that already-live lane — they are separate data sources for
  separate purposes.
- **No caching layer designed yet**: repeated chain/quote fetches for the
  same underlying within a scan cycle would multiply request volume
  needlessly; a caching strategy needs its own design pass, not an
  afterthought bolted onto the first implementation.

## Test plan (for whenever implementation is approved)

- Unit tests exclusively via `httpx.MockTransport`, matching
  `tests/test_polygon_client.py` — no real network calls in CI, ever.
- Fail-soft coverage: unconfigured client raises `PolygonError` from every
  fetch method, `configured` is `False`.
- 429 backoff coverage: mirrors the existing `Retry-After`-aware test in
  `test_polygon_client.py`.
- Malformed-row coverage: a chain/quote response missing required fields
  is skipped/`None`, never fabricated — same discipline as
  `fetch_bars`'s `except (KeyError, TypeError, ValueError): continue`.
- OCC ticker round-trip tests: encode → decode → assert equality, plus at
  least one hand-verified real-world example ticker (matching the existing
  precedent of verifying `front_contract`/`contract_schedule` against known
  worked cases).
- Adapter-shape conformance: every returned value must actually be an
  `AdapterCandle`/`AdapterOptionQuote`/`AdapterUnderlyingSnapshot` instance
  (not a dict, not a vendor-shaped object) — a type-level test, not just a
  values test, so a future refactor can't silently drift from the Increment
  13 contract.

## Do-not-build-yet checklist

- No `PolygonOptionsClient` implementation (this doc is the design, not the
  code).
- No network calls, no HTTP client construction, no live API key usage.
- No option-chain fetch, no quote fetch, no underlying-snapshot fetch against
  a real endpoint.
- No wiring into `options_manager.scanner`, `options_manager.adapters.row_builder`,
  `options_manager.review`, or `options_manager.replay`.
- No wiring into the live `options_companion` lane or its Public.com data
  source.
- No `AdapterMarketContextSnapshot` population attempt (GEX/Signa/HTF stay
  caller-supplied, per the Increment 12 boundary).
- No credential provisioning decision made here — shared vs. separate
  `POLYGON_API_KEY`/tier is an open question for whoever picks up
  implementation, not decided by this audit.

## Open questions for whoever picks this up

1. Is a paid Polygon options tier actually provisioned, and which one? This
   gates almost everything else (real-time vs. delayed, which endpoints are
   even reachable).
2. Shared `PolygonFuturesClient`/`PolygonOptionsClient` module, or fully
   separate files? (Leaning separate, given the futures client's docstring
   already scopes it to futures-only and the options one has meaningfully
   different rate-limit/tier concerns — but not decided here.)
3. Caching strategy for repeated intraday chain fetches — needed before
   the scanner track could realistically use this at any scan cadence.
4. Should this even be built before the scanner/replay/review track
   (Increments 1-13) has any real (non-caller-supplied-fixture) usage
   proving the advisory-only validators are worth feeding real data into?
   This audit does not answer that prioritization question — it only
   describes what the client would need to look like if/when that decision
   is made.
