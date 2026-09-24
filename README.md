# Autonomous Futures Paper-Trading System

[![CI](https://github.com/imanisworld/autonomous-futures-system/actions/workflows/ci.yml/badge.svg)](https://github.com/imanisworld/autonomous-futures-system/actions/workflows/ci.yml)
[![Live blocked](https://img.shields.io/badge/live%20trading-blocked-blue)](#paper-and-demo-only)
[![License](https://img.shields.io/badge/license-all%20rights%20reserved-lightgrey)](LICENSE)

A local futures automation engine for a limited universe. Live trading is blocked at config load. The default path is paper simulation, and a gated Tradovate demo route exists. Decisions are journaled.

---

## Repository

This repository is **private**. It is for educational review and paper/demo
demonstration only. It is not financial advice, does not promise profitability,
and does not provide live-trading support.

All rights are reserved. Viewing and personal evaluation are allowed, but
reuse, redistribution, commercial use, and publication of derivative works
require written permission. See [LICENSE](LICENSE).

Bug reports and documentation feedback are welcome. Unsolicited pull requests
are not currently accepted. See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Learning Package

Shareable course and learning documents live in
[`share/learning/`](share/learning/COURSE_README.md).

Private strategy doctrine, production configuration, operational notes, and
credentials are not stored in this repository. Access to the repository is
private.

---

## Paper and demo only

**Live trading is blocked at config load.** `config/settings.py` raises
`LiveTradingBlockedError` if `risk_rules.yaml` sets
`trading_mode.live_trading_enabled` or if `LIVE_TRADING_ENABLED` is true.
`risk_rules.yaml` v1.2.2 keeps `live_trading_enabled: false`.

The default order path is local paper simulation. A gated Tradovate **DEMO**
route also exists for the wide-stop evidence lane (`tradovate_demo` in
`context/wide_stop_execution.py`). It can place demo orders only when the
route, its proof pin, the lane arming pins, `BROKER=tradovate`, and
`TRADOVATE_ENV=demo` all agree, and it refuses a live broker. That route is
not live trading. It does not mean this process never talks to a broker.

---

## Instruments

`risk_rules.yaml` v1.2.2 `instruments.allowed` is **MNQ only**. MES, MGC, and
MCL are commented out of that list. Do not read the table below as the live
allow-list.

| Symbol | Name | In `instruments.allowed` (v1.2.2) |
|--------|------|-----------------------------------|
| MNQ | Micro E-mini NASDAQ-100 | yes |
| MES | Micro E-mini S&P 500 | no |
| MGC | Micro Gold | no |
| MCL | Micro Crude Oil | no |

## Sessions

v1.2.2 is a 24-hour session set. Asian, London, and New York are all allowed.
`session_windows` and `session_cutoffs_et` are empty, so there is no extra
time-window gate and no session cutoff.

| Session | `session_hours_et` |
|---------|--------------------|
| Asian | 18:00 – 03:00 |
| London | 03:00 – 09:30 |
| New York | 09:30 – 17:00 |

Trading outside the allowed sessions is NO_TRADE. There is no separate
"disable Asian before NY-only paper" switch in the current file.

---

## Risk Rules Summary

- Max **3 trades/day** (`max_trades_per_day: 3`, a provisional cap)
- Consecutive-loss stop is **off** (`max_consecutive_losses: 9999`). The circuit breaker is also off (`circuit_breaker_losses: 0`). Every entry still needs a fresh valid signal.
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
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
├── LICENSE
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
the same engine. Live orders are blocked at config load. The default broker is
paper simulation. If `BROKER=tradovate` and the wide-stop DEMO route is armed
and proof-pinned, that lane can send orders to a Tradovate demo account. It
cannot send live orders.

```bash
python -m webhook
```

In production this runs on the Hetzner VPS under systemd (service
`futures-bot`, uvicorn on `:8000`); `systemctl restart futures-bot` to reload.

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

A gated Tradovate DEMO route now exists, as described above. Live broker
execution stays blocked at config load.
