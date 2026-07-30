# Signa API v1 contract fixtures

Captured live from `GET https://app.getsigna.ai/api/v1/signal` on 2026-07-29
(`api_version v1`, `engine_version v3.1`), HTTP 200, symbol `SPY`, at
`tf=1d`, `tf=4h`, `tf=1h`.

These are **contract** fixtures: their job is to pin the real response *shape*
so the parser cannot drift back to a shape the API never returned. They contain
public market data only — no credentials, no account identifiers, no PII.

## What these fixtures prove

**The request parameter is `tf`, not `timeframe`.** `timeframe`, `interval`,
`resolution`, and `symbol` are all silently ignored by the server, which then
falls back to `1d`. Verified `tf` values: `1d 4h 1h 30m 15m 5m 1w 1m 1mo`
(`daily` and `D` alias to `1d`).

**Three independent surfaces, which routinely disagree.** In `signa_spy_1d.json`:

| Surface | Grade | Direction | Per `meta.signal_sources` |
|---|---|---|---|
| `engine` | `B` | `BULLISH` | nightly 30+ model consensus, matches in-app Action Card |
| `signa`  | `C` | `HOLD` (action) | undocumented |
| `data`   | —   | `WAIT` | live single-pass technical analysis |

**`engine` is timeframe-invariant.** Across all three fixtures `engine` is
identical (`B` / `BULLISH` / score 81) because it is a once-nightly consensus.
Only `signa` and `data` vary by timeframe. Any code that reads `engine` and
labels the result "4H" or "1H" is reporting a daily value under a false label.

**`signal_timestamp` timestamps the engine, not the live data.** It is identical
across all three fixtures.

## Rules these fixtures exist to enforce

- Never collapse `engine.score`, `engine.confidence`, `data.confidence`, and
  `signa.conviction` into one number. They are four different measurements.
- Never pick a winner between `engine.grade` and `signa.grade`. Record both,
  plus `signa_grade_conflict`, and leave the system decision untouched.
- Preserve `A+` verbatim. Never truncate a grade to its first character.
- Signa is **observational metadata only**. Nothing in these fixtures may
  approve, reject, block, or alter a setup, entry, stop, target, or status.

---

## Second capture — 2026-07-29, full entitlement audit

Read-only audit of what this account's key can actually reach. 37 probe calls,
zero writes, zero broker/paper/alert surfaces touched.

### Files from this capture

| File | Endpoint | HTTP |
|---|---|---|
| `health.json` | `GET /api/v1/health` | 200 |
| `quote_spy.json` | `GET /api/v1/quote/:symbol` | 200 |
| `signal_nvda_1d.json` | `GET /api/v1/signal?sym&tf` | 200 |
| `history_spy_{5m,1d,1w}.json` | `GET /api/v1/history/:symbol?tf&limit` | 200 |
| `gex_{spy,nvda}.json` | `GET /api/v1/gex/:symbol` | 200 |
| `scan_multi.json` | `POST /api/v1/scan` | 200 |
| `screener_symbols.json` | `POST /api/v1/screener` | 200 |
| `analysis_spy.json` | `GET /api/v1/analysis?sym` | 200 |
| `earnings_spy.json` | `GET /api/v1/earnings?symbol` | 200 |
| `error_screener_no_symbols.json` | `POST /api/v1/screener` (universe only) | **400** |

Large arrays are truncated (`_fixture_note` records it): `strikes` to 8,
`candles` to 5, `results` to 5. Structure is preserved for parser contract tests.

### Sanitization

`request_id` replaced with `<redacted-request-id>`; all UUIDs replaced with
`<redacted-uuid>`. Scanned for API-key fragments, `Bearer`, `cmts_`,
`Authorization`, `api_key`, `secret`, `token` — **no matches**. Contents are
public market data only.

### What these fixtures pin

**`limit`, not `bars`.** The published reference documents `bars`; the server
**silently ignores** it and returns 200 candles. `limit` is the real parameter
and honours values up to at least 1000. This is the same class of defect as the
`timeframe` vs `tf` bug — a third silently-ignored parameter.

**Seven timeframes work on history:** `5m 15m 30m 1h 4h 1d 1w`. Candles are
`{t,o,h,l,c,v}`, `t` is epoch milliseconds, ordering is **ascending**.

**`/scan` and `/screener` both REQUIRE an explicit `symbols` array.** `universe`
is not honoured — `{"universe":"sp500"}` returns HTTP 400 "No valid symbols
provided". There is **no market-wide discovery over REST**; the caller must
supply the universe. (The MCP `scan_symbols` tool advertises `direction/
min_score/limit` with no `symbols` param, so MCP may differ. Unverified — MCP is
not connected here.)

**`origin_source` is NOT returned by any endpoint.** Checked on signal, gex,
scan, quote, history. Only `gex` carries `meta.source = "Signa Gamma Exposure"`.
Provenance must therefore be synthesized and stamped by us at ingest.

**GEX is reachable and expiry-scopable:** default `expiry_days: 14` → 135 SPY
strikes; explicit `expiry=2026-08-21` → 190 strikes; NVDA → 33 strikes. Levels
returned: `gammaFlipLevel`, `flipLevel`, `callWall`, `putWall`,
`maxGammaStrike`, `regimeAboveFlip`, plus strike-level `netGex`.

### NOT captured, and why

`options_flow`, `dark_pool`, `fundamentals` — **no working REST route found.**
Every guessed path returns an HTML 404. One route, `/api/v1/options-flow/:id`,
exists but returned a 500 with a Postgres error indicating it expects a UUID,
not a ticker; probing stopped there rather than fishing for internal IDs. These
three are MCP-only as far as REST is concerned.

`/api/v1/a2a/route` (Enterprise agent routing) was **deliberately not called** —
unknown side effects, and the audit was read-only by constraint.
