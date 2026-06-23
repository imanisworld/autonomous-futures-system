# Runbook — Autonomous Futures Paper-Trading System

Operational procedures for running, debugging, and maintaining the system.

---

## 1. Starting the System

### Prerequisites

```bash
# Python 3.10+ required
python --version

# Install dependencies
pip install -r requirements.txt

# Copy environment file
cp .env.example .env
# Review .env — confirm LIVE_TRADING_ENABLED=false
```

### Run a Single Evaluation Cycle

```bash
python main.py --market-state data/sample_market_state.json
```

### Run Against a Custom Market State

```bash
python main.py --market-state /path/to/your_market_state.json
```

### Dry Run (validate config and market state only, no decision)

```bash
python main.py --market-state data/sample_market_state.json --dry-run
```

### Start TradingView Webhook Receiver

This receives live TradingView alerts and still runs paper-only. It does not
place broker orders.

```bash
python -m webhook
```

Check local health:

```bash
curl http://127.0.0.1:8000/health
```

Run the terminal doctor when something feels stuck:

```bash
python3 scripts/doctor.py
python3 scripts/doctor.py --json
```

Open the read-only dashboard:

```text
http://127.0.0.1:8000/
```

Read status JSON:

```bash
curl http://127.0.0.1:8000/status/today
curl 'http://127.0.0.1:8000/status/history?days=7'
curl http://127.0.0.1:8000/status/latest-webhook
curl http://127.0.0.1:8000/status/strategy
curl 'http://127.0.0.1:8000/status/review?date=2026-05-23&mode=eod'
```

The review endpoint supports `mode=morning` or `mode=eod` and does not write
review artifacts. Dates must use exact `YYYY-MM-DD` format. Use the daily
summary CLI when you want files written. CLI review artifacts are written by
atomic replacement with unique temp files under a `.daily_review.lock` to
reduce partial-file and interleaving risk.
The CLI exits with a usage error instead of a traceback for invalid dates,
missing risk config, or live-trading block errors.

### Runtime Evidence Freeze

For live/demo-paper evidence on the active Hetzner box, use only these sources:

```text
/root/autonomous-futures-system/logs/journal_YYYY-MM-DD.jsonl
/root/autonomous-futures-system/logs/errors.log
/status/today
/status/broker-account
```

Do not use local ignored `logs/`, replay folders, screenshots alone, Discord
messages alone, or Tradovate P&L alone as proof of an end-to-end system trade.
The next proof window is the next 30 resolved MNQ live/demo-paper trades from
the active box journal after config freeze.

### Optional Discord Output

Discord notifications are read-only and disabled by default. To test them
locally, add a Discord webhook URL to `.env` and set:

```env
DISCORD_NOTIFICATIONS_ENABLED=true
DISCORD_NOTIFY_DECISIONS=TRADE,RISK_REJECTED,BLOCKED_MAX_TRADES,BLOCKED_LOSS_LOCKOUT
```

Do not commit the real Discord webhook URL. Notification failures are logged but
do not block TradingView ingestion or paper-risk checks.

Preview the message without sending:

```bash
python -m notifications --dry-run
```

Send a smoke-test message only after `.env` has a local Discord webhook URL and
notifications are enabled:

```bash
python -m notifications
```

### Signa API Placeholder

`SIGNA_API_KEY` is reserved for future signal or market-data work. The key must
remain local, and the current system only exposes boolean readiness in
`/health`. Do not add network calls until the Signa payload is mapped into the
market-state schema and tested in shadow mode.

TradingView cannot call `localhost`; expose port `8000` with a public HTTPS
tunnel and paste this shape into the TradingView webhook URL field:

```text
https://YOUR-PUBLIC-TUNNEL/webhook/alert?secret=YOUR_LOCAL_SECRET
```

Set `WEBHOOK_SECRET` in `.env` to require the secret query string. Leave it
blank only for local testing.

Paste-ready alert message templates:

```text
tradingview/smoke_test_alert_message.json.tpl
tradingview/full_context_alert_message.json.tpl
```

TradingView smoke-test alert message:

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

Start with `market_condition: "CHOPPY"` for the first live-data smoke test so
the expected output is `NO_TRADE`. After the webhook path is verified, replace
that with real indicator context.

TradingView full-context alert message:

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

Replace the placeholder `0` and classification values with real values from a
Pine indicator. Omit unknown context fields until they are computed. Classified
`strat_212` and `strat_122` context may create paper setups only when enabled
and still must pass bracket and risk checks.

---

## 2. Running Tests

```bash
# All tests
pytest tests/ -v

# Specific test file
pytest tests/test_risk_engine.py -v

# Test that live trading is blocked
pytest tests/test_live_trading_blocked.py -v

# With coverage
pytest tests/ --cov=. --cov-report=term-missing
```

---

## 3. Reviewing Decisions

All decisions are logged to `logs/journal_YYYY-MM-DD.jsonl`.

```bash
# View today's journal
cat logs/journal_$(date +%Y-%m-%d).jsonl | python -m json.tool

# Count decisions by type
grep -o '"decision": "[^"]*"' logs/journal_$(date +%Y-%m-%d).jsonl | sort | uniq -c

# View only TRADE decisions
grep '"decision": "TRADE"' logs/journal_$(date +%Y-%m-%d).jsonl

# View only rejections with reason
grep '"decision": "NO_TRADE"' logs/journal_$(date +%Y-%m-%d).jsonl
```

### Generate Morning And End-Of-Day Reviews

The review layer is read-only. It reads the journal and writes review artifacts
under `logs/`.

```bash
python -m agent.daily_summary --date $(date +%Y-%m-%d) --mode morning
python -m agent.daily_summary --date $(date +%Y-%m-%d) --mode eod
```

---

## 4. Daily State Reset

The system tracks trade count and loss streak per calendar day.

- **Automatic**: The system reads the current day's journal to compute daily state at startup.
- **Manual reset**: Delete or archive today's journal file. The system starts fresh with 0 trades, 0 losses.

```bash
# Archive today's journal
mv logs/journal_$(date +%Y-%m-%d).jsonl logs/archive/
```

---

## 5. Adding a New Market State File

Market state files must conform to `market_state.schema.json`.

```bash
# Validate a market state file
python -c "
from context.market_context import MarketStateLoader
loader = MarketStateLoader('risk_rules.yaml')
state = loader.load('data/your_file.json')
print('Valid:', state)
"
```

---

## 6. Common Errors

### `LiveTradingBlockedError`

**Cause**: `LIVE_TRADING_ENABLED=true` in config or environment.  
**Fix**: Set `LIVE_TRADING_ENABLED=false` in `.env` and `risk_rules.yaml`.

```bash
grep LIVE_TRADING .env
grep live_trading risk_rules.yaml
```

### `DataQualityError: Stale data`

**Cause**: Market state timestamp is older than 5 minutes.  
**Fix**: Provide a fresh market state file. Check the `timestamp` field.

### `DataQualityError: Missing required field`

**Cause**: A required field in market state JSON is null or absent.  
**Fix**: Review `market_state.schema.json` for all required fields. Populate them.

### `RiskRejection: R:R below minimum`

**Cause**: Calculated R:R < 2.0.  
**Fix**: This is expected behavior. The setup is correctly rejected. Adjust the setup or accept NO_TRADE.

### `SchemaValidationError`

**Cause**: Market state JSON does not conform to schema.  
**Fix**: Validate against `market_state.schema.json`. Check enums (instrument, session, etc.)

---

## 7. Checking System Health

```bash
# Verify config loads correctly
python -c "from config.settings import load_config; c = load_config(); print('Config OK:', c)"

# Verify risk rules load
python -c "from risk.risk_engine import RiskEngine; r = RiskEngine(); print('RiskEngine OK')"

# Verify paper broker initializes
python -c "from execution.paper_broker import PaperBroker; b = PaperBroker(); print('PaperBroker OK, is_live:', b.is_live)"
```

---

## 8. Log Locations

| Log | Path | Format |
|-----|------|--------|
| Decision journal | `logs/journal_YYYY-MM-DD.jsonl` | JSONL |
| Error log | `logs/errors.log` | Plain text |
| Risk rejections | Embedded in journal | JSONL |
| Daily review | `logs/daily_review_YYYY-MM-DD.md` | Markdown |
| Trade grades | `logs/trade_grades_YYYY-MM-DD.csv` | CSV |
| Review payload | `logs/review_YYYY-MM-DD.json` | JSON |
| Webhook server | stdout plus `logs/system.log` when engine path runs | Text |

---

## 9. What To Do If Something Looks Wrong

1. Check `logs/errors.log` first
2. Re-run with `--dry-run` to isolate config vs data issues
3. Run `pytest tests/ -v` to confirm rules haven't been violated
4. Check that `LIVE_TRADING_ENABLED=false` — this is the first thing to verify
5. Review the most recent journal entry for the rejection reason

---

## 10. Escalation: Seeing Unexpected TRADE Decisions

If the system is generating trades that seem wrong:

1. **Do not manually edit the journal.** It is append-only by design.
2. Check `risk_rules.yaml` — confirm values match your reviewed local policy
3. Run `pytest tests/test_risk_engine.py -v` — all rules must pass
4. Review the market state file used — confirm it accurately represents conditions
5. Add a test case that covers the unexpected scenario before changing any logic
