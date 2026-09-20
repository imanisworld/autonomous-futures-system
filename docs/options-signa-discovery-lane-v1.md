# Options Signa Discovery Lane v1

Status: read-only / research-only / no trade authority.

This lane uses Signa as an upstream candidate and context source for options research. It does not change scanner scoring, setup rules, contract selection, risk, broker routing, alerts, or deployment state.

## Purpose

The current options scanner only attaches Signa context after the system already selected a ticker. That underuses Signa. v1 creates a normalized candidate shape so Signa scan/action-card/flow/GEX data can feed an evidence inbox before the existing options validator decides whether anything is actionable.

Correct division of labor:

- Signa: discovery, context, level/component telemetry.
- Options scanner: Strat proof, trigger/invalidation/target validation.
- Options selector: contract feasibility.
- Risk layer: position/risk permission.

## Candidate status

Every normalized row is `SIGNA_CANDIDATE` and includes:

- `observation_only=true`
- `trade_authority=false`
- stable `candidate_key`
- source endpoint and source agent when present

A Signa row must not become `ACTIVE`, `TRIGGERED`, `OPTIONS_PASS`, or any executable state by itself.

## Sources normalized in v1

Implemented as pure/read-only helpers in `sources/signa_discovery.py`:

- Action Card / signal payloads
- Scan-style candidate lists via `/api/v1/scan?symbols=...`
- market sentiment via `/api/v1/signal-index`
- generic dataset tags for options flow, dark pool, and GEX

The client wrapper has TTL caching and HTTP 429 backoff support so later discovery probes do not repeatedly burn quota.

## Not included in v1

v1 does not:

- wire into the production options scanner;
- call Signa from the live scanner;
- change Signa score authority;
- change Discord alert authority;
- create orders;
- submit broker requests;
- promote candidates into the shadow journal.

## Next safe step

Run an API capability probe against `/api/v1/me`, `/api/v1/scan`, `/api/v1/enhanced-signal`, `/api/v1/signals/{symbol}`, options-flow, dark-pool and GEX using the read-only client. Capture response shape and quota behavior before any runtime integration is considered.

## Capability probe result — 2026-09-20

VPS probe using the existing Signa key confirmed:

- `/api/v1/me` works and reports Founding Member plan.
- Scopes include `market_data`, `signals`, `analysis`, `options_flow`, `dark_pool`, `broker_connect`, `sms_alerts`, and `custom_agents`.
- Entitlements include `options_flow`, `dark_pool`, `custom_agents`, and `webhooks`.
- Quota reports `1000 calls/day` and `60 req/min`.
- `/api/v1/scan` works only when `symbols=` is supplied.
- `/api/v1/signals/{symbol}` works for Action Card payloads.
- `/api/v1/enhanced-signal` works.
- `/api/v1/signal-index` works.
- Guessed REST paths for GEX, options flow, and dark pool returned 404 despite entitlements. Treat those as endpoint-unresolved, not unavailable. They may be exposed through MCP/tool routes or differently named REST routes.

No raw payloads or credentials are committed.

## Confirmed capability probe — 2026-09-20

The VPS key is configured and the account reports Founding Member access with scopes for `market_data`, `signals`, `analysis`, `options_flow`, `dark_pool`, `broker_connect`, `sms_alerts`, and `custom_agents`. Daily API quota observed: 1000 calls/day with 60 requests/minute burst.

Confirmed available read-only endpoints:

- `/api/v1/me`
- `/api/v1/health`
- `/api/v1/quote/{symbol}`
- `/api/v1/signals/{symbol}?timeframe=1d`
- `/api/v1/signal?sym={symbol}&timeframe=1d`
- `/api/v1/analysis?sym={symbol}`
- `/api/v1/earnings?symbol={symbol}`
- `/api/v1/scan?symbols=SPY,QQQ`
- `/api/v1/enhanced-signal?symbol={symbol}`
- `/api/v1/signal-index`
- `/api/options-flow/{symbol}`
- `/api/options-flow/darkpool/{symbol}`
- `/api/options-flow/tide`
- `/api/v1/political-trades?ticker={symbol}`
- `/api/congress/trades?ticker={symbol}`
- `/api/options-flow/congress?ticker={symbol}`

Standalone GEX remains unresolved. Do not guess a production endpoint for it. GEX-style levels may enter v1 only through manual context paste/import until a read-only endpoint is proven.

## Manual Discord/paste context format

Until every UI/MCP source has a proven REST endpoint, manual context may be pasted as key/value blocks. Each block becomes `SIGNA_CONTEXT`, never trade authority.

Example:

```text
 ticker: SPY
 source: options_flow
 direction: bullish
 count: 4
 callPremium: $1,250,000
 notes: calls dominant

 ticker: QQQ
 source: gex
 gamma_wall: 490
 flip: 485
```

The parser keeps the row observational with `trade_authority=false` and preserves useful fields such as call/put premium, gamma wall, flip, support/resistance, and notes.

## Proven API capability probe — 2026-09-20

The VPS key proved these read-only surfaces:

- `/api/v1/me`
- `/api/v1/health`
- `/api/v1/quote/{symbol}`
- `/api/v1/scan?symbols=SPY,QQQ`
- `/api/v1/signals/{symbol}`
- `/api/v1/signal`
- `/api/v1/enhanced-signal`
- `/api/v1/analysis`
- `/api/v1/earnings`
- `/api/v1/signal-index`
- `/api/options-flow/{symbol}`
- `/api/options-flow/darkpool/{symbol}`
- `/api/options-flow/tide`
- `/api/v1/political-trades`
- `/api/congress/trades`
- `/api/options-flow/congress`

Standalone GEX remains endpoint-unresolved. GEX context is supported through manual ingest until a proven endpoint exists.

## Manual context ingest

`POST /signa/context/ingest` accepts key/value paste blocks from Discord or the UI. It writes only to `options_signa_context`, not to `scans` or `options_shadow_journal`.

Example body:

```json
{
  "source": "manual_discord",
  "text": "ticker: SPY\nsource: gex\nflip: 485\ngamma_wall: 490"
}
```

Rows are stored as:

- `status=SIGNA_CONTEXT`
- `observation_only=true`
- `trade_authority=false`

`GET /signa/context/recent` returns recent context rows with optional `ticker`, `source`, and `limit` filters.

## Storage boundary

`alert_ranker/signa_context_store.py` owns the append-only SQLite table `options_signa_context`.

This table is an evidence inbox only. It must not be used to mark a setup `TRIGGERED`, `ACTIVE`, `OPTIONS_PASS`, or `RISK_PASS` without a separate approved validator step and a new evidence cohort.
