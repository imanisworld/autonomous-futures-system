# Autonomous Futures Paper-Trading System

A local, paper-only autonomous trading system for a limited futures universe. Designed for disciplined, low-frequency trading with strict risk enforcement, session filters, and full decision journaling.

---

## Learning Package

Shareable course and learning documents live in
[`share/learning/`](share/learning/COURSE_README.md).

Private strategy doctrine, production configuration, operational notes, and
credentials are intentionally not part of this public repository.

---

## Paper-Only System

**Live trading is disabled and cannot be activated without explicit future safeguards.**
`LIVE_TRADING_ENABLED` defaults to `false` in every config file and environment. Any attempt to enable live trading in Phase 1 raises a hard error.

---

## Allowed Instruments

| Symbol | Name |
|--------|------|
| MNQ | Micro E-mini NASDAQ-100 |
| MES | Micro E-mini S&P 500 |
| MGC | Micro Gold |
| MCL | Micro Crude Oil |

## Allowed Sessions

| Session | Active Hours (ET) |
|---------|-------------------|
| Asian | 19:00 - 03:00 |
| London | 03:00 – 08:30 |
| New York | 09:30 – 12:00 |

**Asian session is currently enabled for testing.** Disable it in
`risk_rules.yaml` before NY-only paper trading if you do not want overnight
signals using the daily trade budget. Trading outside allowed sessions =
NO_TRADE.

---

## Risk Rules Summary

- Max **3 trades/day**
- Stop after **2 consecutive losses**
- Future real-capital planning assumes **$500-$1k** starting capital
- Future per-trade risk defaults to **1%** of account value
- **One open position** at a time
- **Bracket orders only** (entry + stop + target required)
- Minimum **R:R = 2.0**
- `NO_TRADE` is always a valid outcome
- Missing, stale, or contradictory data = **NO_TRADE**

---

## Project Layout

```
.
├── README.md
├── SECURITY.md
├── RUNBOOK.md
├── CHANGELOG.md
├── risk_rules.yaml
├── market_state.schema.json
├── decision_output.schema.json
├── .env.example
├── main.py
├── agent/
├── config/
├── context/
├── data/
├── execution/
├── journal/
├── risk/
├── sources/
├── strategy/
├── share/learning/
├── tests/
├── webhook/
└── logs/
```

---

## Quickstart

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Copy and review environment
cp .env.example .env

# 3. Run paper engine with a sample market state
python main.py --market-state data/sample_market_state.json

# 4. Run tests
pytest tests/ -v
```

## Daily Reviews

The review layer is read-only. It reads the JSONL journal and writes morning or
end-of-day reports without placing trades or touching broker code.

```bash
python -m agent.daily_summary --date 2026-05-23 --mode morning
python -m agent.daily_summary --date 2026-05-23 --mode eod
```

## TradingView Webhook

The webhook layer accepts TradingView bar-close alerts and routes them through
the same paper-only engine. It does not connect to a broker or place live
orders.

```bash
python -m webhook
```

For local process supervision:

```bash
venv/bin/supervisord -c supervisord.conf
venv/bin/supervisorctl -c supervisord.conf status
```

For Hetzner-style deployment, the included `Procfile` starts:

```bash
HOST=0.0.0.0 python -m webhook
```

The server respects the platform `PORT` environment variable and defaults to
`8000` locally.

TradingView needs a public HTTPS URL, so expose local port `8000` with a tunnel
and paste the resulting URL plus `/webhook/alert` into TradingView's webhook
field.

Example URL:

```text
https://your-public-tunnel.example/webhook/alert?secret=your-local-secret
```

Paste-ready alert message templates live in `tradingview/`:

```text
tradingview/smoke_test_alert_message.json.tpl
tradingview/full_context_alert_message.json.tpl
```

Smoke-test alert JSON:

```json
{
  "ticker": "{{ticker}}",
  "timestamp": "{{time}}",
  "open": {{open}},
  "high": {{high}},
  "low": {{low}},
  "close": {{close}},
  "volume": {{volume}},
  "timeframe": "{{interval}}",
  "market_condition": "CHOPPY"
}
```

Full-context alert JSON:

```json
{
  "ticker": "{{ticker}}",
  "timestamp": "{{time}}",
  "open": {{open}},
  "high": {{high}},
  "low": {{low}},
  "close": {{close}},
  "volume": {{volume}},
  "timeframe": "{{interval}}",
  "avg_volume": 1,
  "vwap": 0,
  "orb_high": 0,
  "orb_low": 0,
  "orb_status": "inside",
  "market_condition": "CHOPPY",
  "trend_direction": "SIDEWAYS",
  "trend_strength": "WEAK",
  "previous_day_high": 0,
  "previous_day_low": 0,
  "previous_day_close": 0,
  "price_vs_pdh": "below",
  "price_vs_pdl": "above",
  "current_bar_type": "two_up",
  "previous_bar_type": "inside_bar",
  "two_bars_back_type": "two_up",
  "strat_sequence": "strat_212",
  "strat_trigger": "continuation",
  "strat_direction": "LONG"
}
```

Replace the `0` and classification values with real values from a Pine
indicator. If a context value is not available yet, omit that field instead of
sending fake numbers. Classified `strat_212` and `strat_122` context may create
paper setups only when enabled and still must pass bracket and risk checks.

## Local Dashboard

When the webhook server is running, open the read-only dashboard:

```text
http://127.0.0.1:8000/
```

Status APIs:

```text
http://127.0.0.1:8000/status/today
http://127.0.0.1:8000/status/history?days=7
http://127.0.0.1:8000/status/latest-webhook
http://127.0.0.1:8000/status/strategy
http://127.0.0.1:8000/status/review?date=2026-05-23&mode=eod
```

The dashboard shows trade count, loss streak, open-position state, realized
paper P/L, recent journal entries, top `NO_TRADE` reasons, and the latest
received webhook context. It also surfaces enabled strategy concepts and
journal-derived strategy counts. It has no manual entry controls. Emergency
`CLOSE_ALL` is tucked behind a collapsed safety drawer and requires the webhook
secret plus an explicit confirmation checkbox.

The review endpoint previews the same read-only morning or end-of-day trade
grading reports produced by `python -m agent.daily_summary`, without writing
review artifacts. Review dates must use exact `YYYY-MM-DD` format.

## Discord Notifications

Discord output is optional and disabled by default. When enabled, it only posts
paper-engine decisions after the webhook has processed the alert; it does not
change decisions, place orders, or touch broker code.

```env
DISCORD_NOTIFICATIONS_ENABLED=false
DISCORD_WEBHOOK_URL=
DISCORD_NOTIFY_DECISIONS=TRADE,RISK_REJECTED,BLOCKED_MAX_TRADES,BLOCKED_LOSS_LOCKOUT
```

Keep real Discord webhook URLs in local `.env` only.

Preview the exact message without sending anything:

```bash
python -m notifications --dry-run
```

## Signa API Planning

Keep `SIGNA_API_KEY` and all other provider credentials in local `.env` only.
Provider integrations must remain read-only unless a separately reviewed
paper-execution phase explicitly requires otherwise.

---

## Architecture Principles

1. **Config is law.** All risk parameters live in `risk_rules.yaml`. Code never overrides them.
2. **Logs are truth.** Every decision, trade, and rejection is journaled with timestamp and reason.
3. **LLM classifies, code validates.** The signal engine may reason about setups; the risk engine enforces rules deterministically before any order is simulated.
4. **NO_TRADE is the default.** Any ambiguity, missing data, or rule violation resolves to NO_TRADE.

---

## Future Roadmap

- Phase 2: Replay engine with historical candles
- Phase 3: Live market data only
- Phase 4: Tradovate simulation connection only, with small-account readiness checks
- Later: IBKR paper adapter for future options/stocks/futures expansion
- Later: Performance analytics and strategy backtesting

Live broker execution remains out of scope.
