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

## Direct pull route

`POST /signa/context/pull` is a read-only ingestion route for proven Signa endpoints. It accepts `symbols`/`tickers`, optional `timeframe`, and optional `include` values.

Supported direct sources:

- `scan`
- `action_card`
- `enhanced_signal`
- `options_flow`
- `dark_pool`
- `market_tide`
- `signal_index`
- `congress_flow`
- `gex` as an explicit unresolved/error context row

The route writes raw Signa responses first to the shared `signa_snapshots` table, then writes options-specific interpretation rows to `options_signa_context` with a `snapshot_id` reference. It does not write to `scans`, `options_shadow_journal`, selector evidence, contract selection, or any execution/risk table. Every row remains observation-only and `trade_authority=false`.

Example request:

```json
{
  "symbols": ["SPY", "QQQ"],
  "timeframe": "1d",
  "include": ["scan", "action_card", "options_flow", "dark_pool", "market_tide"]
}
```

Use this route to create a richer evidence inbox, not to approve trades. Signa-originated rows still require separate Strat setup, trigger, invalidation, target, contract quality, and risk validation before anything can become actionable.
## Shared raw snapshot cache / dedupe policy

Raw Signa responses are now stored in the shared `signa_snapshots` table before options-specific context rows are written. Do not pull duplicate Signa data separately for options and futures. Options and future futures-context consumers should reuse the same `snapshot_id` when they reference the same endpoint/symbol/timeframe/bucket.

Shared proxy symbols:

- SPY for options market context and MES/ES futures proxy.
- QQQ for options market context and MNQ/NQ futures proxy.
- IWM for options market context and M2K/RTY futures proxy.
- DIA for options market context and MYM/YM futures proxy.
- VIX, SVXY, UVXY for volatility regime.
- TLT for rates/risk context.
- GLD for gold/metals context.
- USO and XLE for crude/energy context.

The raw snapshot dedupe identity is provider/source based, not lane based:

```text
source + endpoint + symbol + timeframe + params_hash + snapshot_bucket
```

Options-specific rows retain their own `candidate_key`, but now also preserve `snapshot_id` and `snapshot_ref` so futures can later point to the same raw Signa evidence without pulling it again.

Rows are tagged with consumers:

- ordinary symbols: `options`
- shared proxy symbols: `options`, `shared_proxy`, `futures`

`POST /signa/context/pull` can include the shared proxy list by setting:

```json
{
  "include_shared_proxies": true
}
```

This remains observation-only and does not grant trade, risk, broker, order, or execution authority. The `futures` consumer tag is metadata only; no futures runtime is wired by this branch.


## Read-only pull entrypoint

`scripts/options_signa_context_pull.py` is the scheduled-pull entrypoint, but this branch does not install a timer or start a daemon.

Behavior:

- exits with `skipped=market_closed` outside NYSE regular market hours unless `--force` is supplied;
- defaults to the options watchlist plus the shared proxy symbol set;
- writes raw responses to `signa_snapshots` before options-specific rows go to `options_signa_context`;
- keeps every row `observation_only=true` and `trade_authority=false`;
- reports endpoint status, cache/backoff flags, requested rows, raw `snapshot_ids`, and stored unique options context row IDs;
- does not touch scanner candidates, `options_shadow_journal`, risk, contract selection, broker, order, or execution paths.

Example manual run:

```bash
python3 scripts/options_signa_context_pull.py \
  --symbols SPY,QQQ,NVDA \
  --timeframe 1d
```

Use `--include-gex` only when you intentionally want explicit unresolved-GEX context rows recorded. No standalone GEX endpoint is proven yet.

## Context board/report

`GET /signa/context/board` returns a grouped context-only board from the shared Signa cache.

It groups latest rows by ticker and source, then shows:

- source status and direction;
- endpoint;
- timeframe;
- `data_as_of` / provider timestamp;
- candidate key;
- consumers such as `options`, `shared_proxy`, and `futures`.

The response is marked:

```text
context_only=true
observation_only=true
trade_authority=false
```

The board is for review and research only. It does not create alerts, approve setups, alter scanner status, or write to risk/order/execution tables.

## Setup report context display

`GET /shadow-journal` and `GET /shadow-journal/{shadow_id}` now attach a `signa_context` array to each setup row.

The attached context is pulled from `options_signa_context` by ticker and latest source. It is marked on both the response and row summaries as:

```text
context_only=true
observation_only=true
trade_authority=false
```

Dashboard impact:

- the shadow ledger table includes a `Signa Context` column;
- missing context renders as `Context only: none`;
- available context is summarized as source plus non-authoritative fields such as direction, score, sentiment, call/put premium, flip, or gamma wall;
- no context field can change setup status, risk status, contract choice, alert eligibility, or execution reachability.

## Disabled scheduled pull wiring

The app now has optional scheduler wiring for the same read-only Signa context pull path. It is disabled by default and requires an explicit flag:

```bash
OPTIONS_SIGNA_CONTEXT_PULL_ENABLED=true
```

Optional controls:

```bash
OPTIONS_SIGNA_CONTEXT_PULL_INTERVAL_MINUTES=15
OPTIONS_SIGNA_CONTEXT_PULL_TIMEFRAME=1d
OPTIONS_SIGNA_CONTEXT_PULL_SYMBOLS=SPY,QQQ,NVDA
OPTIONS_SIGNA_CONTEXT_PULL_INCLUDE=scan,action_card,enhanced_signal,options_flow,dark_pool,market_tide,signal_index,congress_flow
OPTIONS_SIGNA_CONTEXT_PULL_INCLUDE_SHARED_PROXIES=true
OPTIONS_SIGNA_CONTEXT_PULL_SYMBOL_LIMIT=50
```

Behavior:

- if disabled, no Signa pull job is registered;
- if enabled, the job still skips outside NYSE regular market hours;
- it writes raw responses to `signa_snapshots` before options-specific rows go to `options_signa_context`;
- rows remain `context_only`, `observation_only`, and `trade_authority=false`;
- it does not create Discord alerts, setup candidates, contract selections, risk approvals, orders, or executions.

## Partial failure and stale context display

Signa endpoint failures are now represented explicitly instead of being hidden by older rows.

When a pull fails for a ticker/source, the context row is stored with:

```text
status=SIGNA_CONTEXT_ERROR
request_ok=false
error=<provider or HTTP error>
trade_authority=false
```

The board/report keeps the latest error visible. If an older successful row exists for the same ticker/source, the display may carry its fields as a stale fallback, but it marks:

```text
healthy=false
stale_fallback=true
```

Grouped board rows now also expose:

```text
expected_sources
missing_sources
error_sources
stale_sources
```

This prevents a ticker from looking complete when, for example, `dark_pool` failed or `enhanced_signal` timed out. These labels are presentation/evidence only; they do not affect scanner score, alert eligibility, contract choice, risk, or execution.
