# Autonomous Futures Paper-Trading System

A local, paper-only autonomous trading system for a limited futures universe. Designed for disciplined, low-frequency trading with strict risk enforcement, session filters, and full decision journaling.

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
| London | 03:00 – 08:30 |
| New York | 09:30 – 12:00 |

**Asian session is disabled.** Trading outside allowed sessions = NO_TRADE.

---

## Risk Rules Summary

- Max **3 trades/day**
- Stop after **2 consecutive losses**
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
├── AGENT_CONTEXT.md
├── FUTURES_SYSTEM_RULEBOOK.md
├── LIMITED_AUTONOMOUS_FUTURES_SPEC.md
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
├── tests/
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
- Phase 4: Tradovate simulation connection only
- Later: Performance analytics and strategy backtesting

Live broker execution remains out of scope.
