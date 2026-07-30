# Signa Entitlement, MCP and REST Schema Audit — 2026-07-29

**VERDICT: PARTIAL**

REST is fully audited and proven. **MCP could not be audited** — the Signa MCP
server is not connected to this session, so sections A.1–A.4 of the brief are
BLOCKED pending operator action. Everything REST-side passed.

Read-only throughout: 37+ probe calls, zero writes, zero broker/paper/alert
surfaces, no production code modified, nothing deployed.

---

## 1. Account entitlement table

Proven by call, not by marketing page.

| Capability | REST path | Result | Doc'd tier | MCP tier badge |
|---|---|---|---|---|
| Health | `GET /api/v1/health` | ✅ 200 | Free | FREE |
| Quote | `GET /api/v1/quote/:symbol` | ✅ 200 | Free | FREE |
| Signal | `GET /api/v1/signal` | ✅ 200 | **Pro** | FREE |
| History | `GET /api/v1/history/:symbol` | ✅ 200 | **Pro** | FREE |
| Scan | `POST /api/v1/scan` | ✅ 200 | Pro | PAID |
| Screener | `POST /api/v1/screener` | ✅ 200 (needs `symbols`) | Pro | — |
| Analysis | `GET /api/v1/analysis` | ✅ 200 | Pro | — |
| Earnings | `GET /api/v1/earnings` | ✅ 200 | Pro | — |
| **GEX** | `GET /api/v1/gex/:symbol` | ✅ **200** | *undocumented* | PAID |
| Options flow | — | ❌ no REST route | — | PAID |
| Dark pool | — | ❌ no REST route | — | PAID |
| Fundamentals | — | ❌ no REST route | — | PAID |
| A2A route | `/api/v1/a2a/route` | **not called** | Enterprise | — |

**This key has full paid entitlement to everything with a REST route.** No
403/402 was returned anywhere. The published tier labels contradict each other
and both contradict reality — `signal`/`history` are labelled Pro in the REST
reference and FREE in the MCP tool list, and `gex` has no REST documentation at
all yet works.

`/api/v1/gex/:symbol` being live is the material finding: **it is a working
replacement for the cancelled GEX Sniper feed, at no additional cost.**

## 2. MCP tool inventory — BLOCKED

The Signa MCP server is **not connected to this session**. I searched the
available tool surface; the only MCP servers present are brokerage/market-data
ones unrelated to Signa. The config in the vendor docs targets Claude Desktop.

From the vendor's published tool list (**unverified against this account** —
these are documentation claims, not proven entitlement):

| Tool | Badge | Params |
|---|---|---|
| `get_signal` | FREE | `ticker` |
| `get_quote` | FREE | `ticker` |
| `get_history` | FREE | `symbol, timeframe?, limit?` |
| `get_health` | FREE | none |
| `scan_symbols` | PAID | `direction?, min_score?, limit?` |
| `get_options_flow` | PAID | `ticker?, limit?` |
| `get_dark_pool` | PAID | `ticker?, limit?` |
| `get_gex` | PAID | `symbol, expiry?, expiry_days?` |
| `get_fundamentals` | PAID | `ticker` |

To unblock: add the server (endpoint `https://app.getsigna.ai/api/mcp/sse`,
bearer auth) and re-run. This session cannot complete OAuth/registration.

## 3. REST endpoint inventory — verified

Base `https://app.getsigna.ai`, auth `Authorization: Bearer <key>`.

| Path | Method | Real params | Notes |
|---|---|---|---|
| `/api/v1/health` | GET | — | no auth needed |
| `/api/v1/quote/:symbol` | GET | — | |
| `/api/v1/signal` | GET | `sym`, **`tf`** | `timeframe` silently ignored |
| `/api/v1/history/:symbol` | GET | **`tf`**, **`limit`** | **`bars` silently ignored** |
| `/api/v1/scan` | POST | `symbols[]` (**required**), `minScore`, `tier` | |
| `/api/v1/screener` | POST | `symbols[]` (**required**), `limit` | `universe` NOT honoured |
| `/api/v1/analysis` | GET | `sym` | |
| `/api/v1/earnings` | GET | `symbol` | |
| `/api/v1/gex/:symbol` | GET | `expiry`, `expiry_days` | undocumented but live |

## 4–5. Call results and verified schemas

**History.** All seven timeframes work: `5m 15m 30m 1h 4h 1d 1w`. Candles are
`{t,o,h,l,c,v}`; `t` is **epoch milliseconds**; ordering **ascending**;
`limit=1000` honoured. Adjusted-vs-unadjusted and RTH-vs-extended coverage are
**not stated in the payload** — UNPROVEN (see §12).

**GEX.** `levels{gammaFlipLevel, flipLevel, callWall, putWall, maxGammaStrike,
regimeAboveFlip}`, `strikes[]{strike, expiry, netGex}`, `underlying{price,
regularClose, change, changePct, isMarketOpen, isExtended, timestamp}`, `asOf`,
`meta.source="Signa Gamma Exposure"`, `meta.filters.expiry_days`. Default scope
14 days (135 SPY strikes); explicit `expiry` widens to 190; NVDA returns 33.

**Scan.** `results[]{symbol, score, tier, bias, confidence, stage, triggers[],
price, change24h, rsi}`, `meta{count, generated_at}`. Ranking fields are
`score` + `tier` + `bias`. **No pagination** — the caller supplies the universe,
so there is nothing to page. `minScore=50` correctly returned 0 results on a
5-symbol set; `tier` filtering works.

**Health.** `providers[]{name, healthy, priority}` — first provider is
`Twelvedata`. So Signa is itself a reseller of upstream data.

**Signal.** Unchanged from the earlier audit: three surfaces (`engine`, `signa`,
`data`) that disagree; `engine` timeframe-invariant. NVDA captured as a second
symbol; same shape.

**Determinism.** Two identical back-to-back `signal` calls returned
byte-identical payloads. ✅

## 6. MCP versus REST differences

The important one: **`scan_symbols` (MCP) advertises `direction/min_score/limit`
with no `symbols` parameter, implying market-wide discovery. REST `/scan` and
`/screener` both REQUIRE an explicit `symbols` array** — `{"universe":"sp500"}`
returns HTTP 400 "No valid symbols provided".

If true market-wide discovery exists, it is **MCP-only**. Over REST we must
supply our own universe. This directly affects the proposed "Signa scanner for
candidate discovery" design: over REST it is a *ranker of a list we choose*, not
a discoverer of tickers we don't know about. **UNPROVEN until MCP is connected.**

Options flow, dark pool, and fundamentals are likewise MCP-only — no REST route
exists. One route `/api/v1/options-flow/:id` exists but expects a UUID (it
returned a Postgres type error on a ticker); probing stopped rather than fishing
for internal identifiers.

## 7. Rate limits and quota

`x-ratelimit-limit: 60`, `x-ratelimit-remaining`, `x-ratelimit-reset`,
`x-ratelimit-window: 60` — **60 requests/minute**, matching the paid tier, not
Free (10/min). Headers appeared on only **4 of 37** calls, so they cannot be
relied on for quota tracking; a client must count its own usage.

Latency: 155–1657 ms. GEX is the slowest (~1.0–1.7 s) — too slow for a hot path,
fine for a periodic observation.

## 8. Existing repo defects found by this audit

1. **`sources/signa_client.py` has no history, scan, screener, gex, analysis, or
   earnings support at all.** It only calls `/api/v1/signal`. Six reachable,
   already-paid-for endpoints are unused.
2. **The earlier audit's "no ranking endpoint exists" was wrong.** It was scoped
   to `/signal` and never probed for a scanner. `/scan` and `/screener` both
   exist and work. Corrected here.
3. **`bars` vs `limit`** — the same silently-ignored-parameter class as
   `timeframe` vs `tf`. Anyone adding history using the published docs would
   silently get 200 candles and never notice.
4. **`origin_source` is not returned by any endpoint**, so the terms' preserve-
   provenance requirement cannot be met by passthrough. We must stamp our own
   provenance (endpoint, params, `generated_at`, fetch time) at ingest.
5. GEX being live means the `GEX_UNAVAILABLE` path built in #379/#380 is correct
   but no longer the only option — GEX can be *populated* without a vendor
   subscription, while remaining non-decisive.

## 9. Fixture inventory

13 new sanitized fixtures in `tests/fixtures/signa_api_v1/` (see its README),
alongside the 3 from the earlier capture. Arrays truncated with `_fixture_note`;
structure preserved for parser contract tests.

## 10. Security review

`request_id` and all UUIDs redacted. Scanned for API-key fragments, `Bearer`,
`cmts_`, `Authorization`, `api_key`, `secret`, `token` — **no matches**, confirmed
by an independent `grep` pass. Contents are public market data only. The API key
was never printed, logged, or written to any file. **No secrets saved.**

## 11. Recommended integration boundaries

**May do:** rank a universe *we* supply (`/scan`); enrich with signal/GEX/flow as
recorded observations; cross-check candles in interactive research.

**Must not do:** approve, reject, trigger, stop, target, size, or manage. Signa's
`entry`/`stop`/`target`/`rr` are comparison fields only. No broker, paper-trade,
alert-creation, or agent-routing surface is ever called. Signa history must not
become the official historical proof corpus — the system keeps its own
reproducible market-data corpus.

**Compliance:** stamp our own provenance at ingest since `origin_source` is
absent; journal observations for outcome comparison only; do not attempt to
reconstruct or substitute for Signa's signal engine.

## 12. Unproven

1. Everything MCP — connection blocked.
2. Whether market-wide discovery exists at all (MCP-only if so).
3. Options flow / dark pool / fundamentals payload shapes.
4. History adjusted-vs-unadjusted; RTH-vs-extended coverage.
5. Daily quota (only the per-minute limit was observed).
6. Rate-limit headers are inconsistent (4/37), so quota state is unobservable.
7. GEX accuracy — reachable ≠ correct. Requires a shadow scorecard before any use
   beyond display.
8. Whether `/api/v1/options-flow/:id` is options flow at all, or an unrelated
   record store.

## 13. Next smallest PR

**Add a read-only `SignaHistoryClient` for `/api/v1/history/:symbol`, with
`tf`/`limit` pinned by the captured fixtures, wired to nothing.**

Smallest useful unit, zero decision surface, and it pins the `bars`→`limit`
defect before anyone writes history code from the published docs. GEX ingestion
should follow as a separate PR, observation-only, behind the existing
shadow-scorecard requirement.

---

## Architecture conclusion

- Signa **may** provide candidate discovery and optional observations.
- Signa **does not** approve, reject, trigger, stop, target, size, or manage trades.
- Missing Signa **cannot** block the system.
- The independent options system **must continue functioning without Signa**.
- **Nothing was deployed.**
