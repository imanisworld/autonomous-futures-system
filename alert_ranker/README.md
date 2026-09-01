# Advisory Options Scanner

Runs a separate FastAPI microservice on port `8010` alongside the futures paper-trading webhook on port `8000`.

This service is test-only and advisory-only. It reads market data from a configured provider, scores option setups, logs each scan to SQLite, and optionally sends Discord alerts for high-scoring setups. It never submits or authorizes orders, discovers account data, connects to trading endpoints, or mutates the futures engine. Enabling it does not enable options or futures trading.

## Setup

Add these values to `.env`:

```env
OPTIONS_SCANNER_ENABLED=false
OPTIONS_MARKET_DATA_PROVIDER=public
PUBLIC_API_SECRET_KEY=
PUBLIC_ACCOUNT_ID=
PUBLIC_BASE_URL=https://api.public.com
PUBLIC_TOKEN_VALIDITY_MINUTES=1440
PUBLIC_STALE_QUOTE_SECONDS=900
OPTIONS_SCANNER_PORT=8010
OPTIONS_SCANNER_SQLITE_PATH=logs/options_scanner.sqlite
OPTIONS_SCANNER_DISCORD_WEBHOOK_URL=
OPTIONS_SCANNER_WATCHLIST=AAPL,MSFT,NVDA,TSLA,SPY,QQQ
OPTIONS_SCANNER_INTERVAL_MINUTES=5
```

### Causal bar context (PR C, off by default)

Supplies the scheduled scanner with real structure — session VWAP, EMA20,
Strat candle classification and prior candle high/low — from consolidated
equity bars, under a strict no-future-leakage policy. Advisory and shadow
only; it adds no execution path.

```env
OPTIONS_BAR_CONTEXT_ENABLED=false      # the single switch
OPTIONS_BAR_CONTEXT_FEED=sip
OPTIONS_BAR_CONTEXT_TIMEFRAME=30Min
OPTIONS_BAR_CONTEXT_LOOKBACK_DAYS=10
OPTIONS_SIP_DELAY_BUFFER_SECONDS=960   # 16 min
```

Left disabled, or with Alpaca credentials absent, the scanner behaves exactly
as before. See `docs/options-causal-bar-context-pr-c.md` for the data policy,
the provider failure modes it defends against, and the first-live-session
acceptance checklist.

Keep `OPTIONS_SCANNER_ENABLED=false` unless intentionally running this separate local advisory service. For the `public` provider, `PUBLIC_API_SECRET_KEY` and `PUBLIC_ACCOUNT_ID` are required. The account number is an explicit configuration pin used only to construct account-scoped market-data URLs; the scanner never calls account endpoints to discover it. `PUBLIC_API_KEY` remains a legacy secret-name fallback, but new scanner configuration should use `PUBLIC_API_SECRET_KEY`.

The Public.com flow exchanges the long-lived secret at `POST /userapiauthservice/personal/access-tokens`, then uses the returned bearer token only with `POST /userapigateway/marketdata/{accountId}/quotes`, `option-expirations`, and `option-chain`. The client allowlist rejects account, position, balance, transaction, trading, and order paths before a request can be sent.

Supported `OPTIONS_MARKET_DATA_PROVIDER` values:

- `public` — preferred provider; requires the Public secret plus pinned account number and uses only auth-token and account-scoped market-data paths.
- `tastytrade` — read-only metrics fallback adapter.
- `alpaca` — read-only Alpaca market-data adapter; no account or order client is created.

Install dependencies:

```bash
pip install -r requirements.txt
```

## Run

First run the offline preflight. It prints only redacted configuration state, makes no network request, and exits non-zero with a `missing_configuration` list when the scanner flag, secret, or account pin is absent:

```bash
python3 -m alert_ranker.preflight
```

For a credential-free structural demonstration, use obvious local placeholders. They are treated only as presence checks and are never sent anywhere:

```bash
OPTIONS_SCANNER_ENABLED=true \
PUBLIC_API_SECRET_KEY=redacted-local-placeholder \
PUBLIC_ACCOUNT_ID=redacted-account-pin \
python3 -m alert_ranker.preflight
```

The successful report must show `network_called: false`, `trading_account_order_paths: blocked`, and only the auth-token plus redacted account-scoped market-data families under `reachable_path_families`. This does not validate a real credential or contact Public.com.

To start the separate local advisory service after supplying authorized read-only credentials:

```bash
OPTIONS_SCANNER_ENABLED=true python3 -m alert_ranker
```

The service listens on `http://127.0.0.1:8010` by default.

## Endpoints

- `GET /health`
- `GET /status`
- `GET /watchlist`
- `GET /terminal` — compact Bloomberg-style options terminal state
- `GET /shadow-journal` — latest hypothetical setup rows, filterable by `ticker` and `status`
- `GET /shadow-journal/summary` — compact hypothetical win/loss and P&L summary
- `PATCH /shadow-journal/{shadow_id}/outcome` — update hypothetical outcome fields
- `POST /webhook/alert`

`POST /webhook/alert` accepts TradingView-style context. Useful fields include `ticker`, `pattern`, `price`, `vwap`, `ema20`, `volume`, `average_volume`, `volume_ratio`, and `iv_rank`.

## Persistence

Every scheduled or webhook-triggered scan is stored in:

```text
logs/options_scanner.sqlite
```

The scanner skips scheduled scans outside regular equity market hours: Monday-Friday, 9:30 AM to 4:00 PM ET.

## Alerting

Discord alerts are sent only when score is `>= 7`. Duplicate alerts for the same ticker, direction, and pattern are suppressed for 30 minutes.

Footer text is always: `Advisory only - not financial advice`.


## Terminal State

`GET /terminal` returns one compact read-only object for a dashboard: scanner config, watchlist rows, latest scores, provider capabilities, and the latest shadow journal rows. It does not create broker clients or submit orders.

## Shadow Journal

Every advisory scan also records a hypothetical setup row in SQLite. The row captures setup inputs, provider snapshot/error state, selected contract context when present, and blank outcome fields for later shadow backtesting. This is not live execution.

Outcome updates are also advisory-only. Valid shadow statuses are `OPEN`, `WIN`, `LOSS`, `BREAKEVEN`, `CANCELLED`, and `EXPIRED`. When an outcome includes an exit mark, the service derives hypothetical `pnl_percent` and `pnl_dollars` from the original option mark.

`GET /shadow-journal/summary` reports total/open/closed rows, win/loss counts, win rate, aggregate hypothetical dollars, and average hypothetical percent. It can be filtered with `ticker`.
