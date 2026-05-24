# Changelog

All notable changes to the Autonomous Futures Paper-Trading System are documented here.

Format: `[Version] YYYY-MM-DD — Type: Description`

Types: `Added`, `Changed`, `Fixed`, `Removed`, `Security`, `Rulebook`

---

## [1.4.0] 2026-05-24

### Added
- TradingView webhook receiver for live-data ingestion into the paper-only engine.
- TradingView payload builder that normalizes futures symbols, detects sessions, and maps alerts into market state.
- Webhook shared-secret support through the `secret` query string and `WEBHOOK_SECRET`.
- Optional, disabled-by-default Discord notifications for paper webhook decisions.
- Local Discord notification smoke-test CLI via `python -m notifications --dry-run`.
- Read-only RiskSentinel dashboard at `/` with trade count, loss streak, open-position state, realized paper P/L, recent journal entries, and top `NO_TRADE` reasons.
- `/status/today` and `/status/history` now expose dashboard-ready read-only state.
- `/status/latest-webhook` exposes the latest raw TradingView payload, derived market context, and paper-engine result.
- `/status/strategy` exposes enabled concepts, decision counts, market-condition counts, and strategy counts without mutating journals.
- `/status/review` exposes read-only morning and end-of-day journal review reports.
- The Strat classifier now supports candle typing and simple `strat_212`, `strat_122`, inside-break, and outside-bar follow-through context; classified `strat_212`/`strat_122` setups can generate paper setups when enabled and can veto opposing structural setups.
- TradingView alert message templates are available under `tradingview/`.
- Tests covering payload parsing, session detection, paper decisions, open-position resolution, webhook health, and webhook auth.

### Security
- Webhook live-data ingestion remains paper-only and does not add broker APIs, broker SDKs, credentials, or live order execution.
- Webhook errors return generic internal-error responses instead of exposing exception details.
- Discord notifications are read-only and cannot alter decisions, risk checks, broker behavior, or paper fills.

### Fixed
- Replay runs now clear prior generated replay artifacts for each replay date before running, so repeated manifest runs do not double-count trades or P/L.

## [1.3.0] 2026-05-24

### Added
- Replay manifest support for curated multi-day replay suites.
- Replay hardening checks for duplicate timestamps, unsorted candles, malformed OHLC, malformed ORB levels, invalid volume, and mixed instruments.
- Replay report metrics: expectancy, win rate, average win/loss, profit factor, max drawdown, and trades per day.
- CLI support for manifest-driven replay runs.

### Changed
- Multi-day replay reports now aggregate performance metrics across the full replay suite.
- Replay CLI now rejects ambiguous input when both `--manifest` and `--candles` are provided.

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
