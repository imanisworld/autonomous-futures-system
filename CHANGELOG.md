# Changelog

All notable changes to the Autonomous Futures Paper-Trading System are documented here.

Format: `[Version] YYYY-MM-DD — Type: Description`

Types: `Added`, `Changed`, `Fixed`, `Removed`, `Security`, `Rulebook`

---

## [1.5.0] 2026-05-24

### Added
- London session opening range: Pine indicator tracks a London ORB (03:00–08:30 ET)
  in parallel with the NY ORB using the same configurable window. `london_orb_high`,
  `london_orb_low`, `london_orb_status` added to the alert payload and chart overlays.
- `strat_inside_break` signal handler: inside-bar compression breakout with trend and
  VWAP alignment filter. Phase 2 classified sequence only — no noisy proxy.
- `strat_outside_continuation` signal handler: outside-bar follow-through with volume
  >= 0.8× average required to reject trap moves. Phase 2 only.
- Both new Strat patterns enabled in `risk_rules.yaml` and documented in `strat_definitions.md`.

### Fixed
- `strat_4hr_retrigger` was unreachable dead code: `orb_reclaim` fired first on identical
  conditions. Fixed by moving it before `orb_reclaim` in the evaluation list, adding a
  9:30–11:00 ET time gate, and requiring STRONG (not just UP) trend. `orb_reclaim` now
  acts as the natural fallback for the same bar outside the window or on MODERATE trend.
- `pdh_reclaim` and `pdl_reclaim` were permanently dead: both checked `price_vs_pdh == "reclaimed"`
  but Pine only emits `"above"/"below"/"at"`. Fixed to use `"above"`/`"below"` with trend
  and VWAP alignment as functional confirmation.
- `continuation_pullback` VWAP proximity check was a no-op: the `holding` flag is True
  whenever price is above or below VWAP (i.e. almost always), making the OR condition
  always pass. Replaced with a tick-distance gate — fires only when close is within 6
  ticks of VWAP on the correct side.
- `state_builder` `price_vs_pdh` used `>=` instead of `>`, mapping `close == PDH` to
  `"above"` rather than `"at"`. Fixed to strict `>` for symmetry with Pine.

### Changed
- `full_context_alert_message.json.tpl` updated with realistic MNQ example values and
  all seven fields that were previously missing (London ORB, bar-history floats).

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
- Daily review agents now support preview methods that do not write artifacts.
- Review dates are validated as exact `YYYY-MM-DD` before reading or writing files.
- Daily review artifacts now use unique same-directory atomic replacement writes.
- Daily review CLI artifact bundles are serialized with `.daily_review.lock`.
- Daily review CLI reports invalid dates as usage errors instead of tracebacks.
- Daily review CLI reports config and live-trading block errors without tracebacks.
- Signa API key readiness is detected without storing or exposing the key value.
- Added a Signa API integration plan for future read-only data-source work.
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
