# Advisory Options Scanner

Runs a separate FastAPI microservice on port `8010` alongside the futures paper-trading webhook on port `8000`.

This service is advisory-only. It reads market data from a configured provider, scores option setups, logs each scan to SQLite, and optionally sends Discord alerts for high-scoring setups. It does not submit orders, connect to order endpoints, or mutate the futures engine.

## Setup

Add these values to `.env`:

```env
OPTIONS_MARKET_DATA_PROVIDER=public
TASTYTRADE_USERNAME=
TASTYTRADE_PASSWORD=
PUBLIC_API_KEY=
PUBLIC_BASE_URL=https://api.public.com
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_DATA_BASE_URL=https://data.alpaca.markets
OPTIONS_SCANNER_PORT=8010
OPTIONS_SCANNER_DISCORD_WEBHOOK_URL=
OPTIONS_SCANNER_WATCHLIST=AAPL,MSFT,NVDA,TSLA,SPY,QQQ
OPTIONS_SCANNER_INTERVAL_MINUTES=5
```

Supported `OPTIONS_MARKET_DATA_PROVIDER` values:

- `public` — preferred provider target; fails soft until the exact Public API shape is configured.
- `tastytrade` — read-only metrics fallback adapter.
- `alpaca` — read-only Alpaca market-data adapter; no account or order client is created.

Install dependencies:

```bash
pip install -r requirements.txt
```

## Run

```bash
python -m alert_ranker
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
