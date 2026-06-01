# Advisory Options Scanner

Runs a separate FastAPI microservice on port `8010` alongside the futures paper-trading webhook on port `8000`.

This service is advisory-only. It authenticates to tastytrade for read-only market metrics, scores option setups, logs each scan to SQLite, and optionally sends Discord alerts for high-scoring setups. It does not submit orders, connect to order endpoints, or mutate the futures engine.

## Setup

Add these values to `.env`:

```env
TASTYTRADE_USERNAME=
TASTYTRADE_PASSWORD=
OPTIONS_SCANNER_PORT=8010
OPTIONS_SCANNER_DISCORD_WEBHOOK_URL=
OPTIONS_SCANNER_WATCHLIST=AAPL,MSFT,NVDA,TSLA,SPY,QQQ
OPTIONS_SCANNER_INTERVAL_MINUTES=5
```

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

`GET /terminal` returns one compact read-only object for a dashboard: scanner config, watchlist rows, latest scores, options risk settings, and Alpaca options lane health. It does not create broker clients or submit orders.
