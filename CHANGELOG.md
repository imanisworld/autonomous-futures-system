# Changelog

All notable changes to the Autonomous Futures Paper-Trading System are documented here.

Format: `[Version] YYYY-MM-DD — Type: Description`

Types: `Added`, `Changed`, `Fixed`, `Removed`, `Security`, `Rulebook`

---

## [1.0.0] 2026-05-23

### Added
- Phase 0: Foundation files
  - `README.md` — project overview and quickstart
  - `AGENT_CONTEXT.md` — agent operating rules and decision flow
  - `FUTURES_SYSTEM_RULEBOOK.md` — authoritative trading rules v1.0.0
  - `LIMITED_AUTONOMOUS_FUTURES_SPEC.md` — component technical specification
  - `risk_rules.yaml` — runtime risk configuration
  - `market_state.schema.json` — JSON schema for market state input
  - `decision_output.schema.json` — JSON schema for decision output
  - `RUNBOOK.md` — operational procedures
  - `CHANGELOG.md` — this file
  - `.env.example` — environment variable template

- Phase 1: Core Engine
  - `config/settings.py` — config loader with live trading hard block
  - `context/market_context.py` — market state loader and validator
  - `strategy/signal_engine.py` — decision and signal generation engine
  - `risk/risk_engine.py` — deterministic risk rule enforcement
  - `execution/broker_interface.py` — abstract broker interface
  - `execution/paper_broker.py` — paper trading simulator
  - `execution/tradovate_broker_stub.py` — future live broker placeholder (disabled)
  - `journal/journal_logger.py` — append-only JSONL decision journal
  - `main.py` — main orchestration loop
  - `data/sample_market_state.json` — example market state fixtures
  - `tests/` — pytest test suite covering all rules and edge cases

### Rulebook
- Established v1.0.0 of `FUTURES_SYSTEM_RULEBOOK.md`
- `LIVE_TRADING_ENABLED=false` set as immutable default in Phase 1

---

## Future Entries

New entries should be prepended to this file (newest at top) in the format above.
