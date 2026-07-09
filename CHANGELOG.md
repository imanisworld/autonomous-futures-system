# Changelog

All notable changes to the Autonomous Futures Paper-Trading System are documented here.

Format: `[Version] YYYY-MM-DD — Type: Description`

Types: `Added`, `Changed`, `Fixed`, `Removed`, `Security`, `Rulebook`

---

## [1.6.1] 2026-07-09

### Rulebook
- Risk rules version `1.0.1`: accept `TRANSITION` as a market-condition label
  but keep it non-tradable while transition/reclaim candidates collect
  observe-only evidence.

### Added
- Shadow-only `transition_failed_breakdown_reclaim` candidate and CSV missed-move
  report harness for measuring failed breakdown reclaim setups before any paper
  or live promotion.

## [1.6.0] 2026-06-04

Live paper pipeline closed end-to-end for the first time: a TradingView 15m
alert produced an entry that **resolved to a booked WIN (MNQ SHORT, +$30.00)**.
Several gating/resolution bugs surfaced and were fixed, plus a `/status` perf pass.

### Added
- Per-instrument latest webhook: `/status/today` returns `latest_webhooks`
  `{MES, MNQ}`, persisted as `latest_webhook_<INST>.json`. Powers a dashboard
  that shows a dedicated MES side and MNQ side instead of one slot that flipped
  every bar. (`10ab8fa`)
- `/status/today` exposes `expected_timeframe_minutes` and `instrument_universe`. (`ce3e9b7`)
- 15m timeframe guard: off-timeframe alerts are journaled as
  `CONFIG_BLOCKED / TIMEFRAME_MISMATCH` (distinct from `NO_TRADE`) and surfaced
  via `alert_validation` + an operator banner. (`658f5aa`)

### Fixed
- **Paper positions never resolved.** Entry and resolution selected the broker
  from the `BROKER` env var, ignoring paper mode; with `BROKER=tradovate` +
  `PAPER_MODE=true`, resolution called `TradovateBroker.resolve_position()`,
  which has no surviving order IDs across webhook calls → returned `None` every
  bar → the position stayed open forever, and the paper next-bar safety net was
  gated `broker_type == "paper"` so it never ran. Paper mode now simulates both
  entry and resolution via `PaperBroker` (next-bar OHLC) regardless of `BROKER`.
  Added an instrument guard: a position is only resolved against bars of its OWN
  instrument (a MES bar must not resolve/force-close an MNQ position at MES-scale
  prices). (`80dd1c5`)
- **Every 15m bar false-blocked as stale.** Bar age was measured from the bar's
  open timestamp (TradingView stamps bars at open), so a freshly-closed 15m bar
  read ~900s old and tripped the 600s `max_staleness_seconds` cap
  (`BLOCKED_DATA_QUALITY`). Now measured from bar close, making the cap a clean,
  timeframe-agnostic delivery-lag budget. (`1faca03`)
- **"LIVE ALERT MISCONFIGURED" banner and OPS:FAIL stuck all day.** Both lit if
  *any* timeframe mismatch occurred that day, so a night of off-timeframe (5m)
  alerts kept them red even after the alert was recreated on 15m. Both now report
  misconfigured only when a mismatch is newer than the last on-timeframe bar,
  via a shared `_timeframe_mismatch_state()` helper (single source of truth so
  the banner and OPS diagnostic can't drift). (`5e78e34`, `6ef4f45`)

### Changed
- **Performance — `/status` endpoints.** `_read_entries` re-locked and re-parsed
  the day's JSONL ~8× per `/status/today` request (every 30s per open tab);
  under a growing journal it crawled to 15–30s and flapped the dashboard to
  "offline". Parsed entries are now cached per file `(mtime_ns, size)` — a
  webhook append invalidates automatically — bringing `/status/today` to ~0.3s. (`808c731`)
- **Performance — `/status/quote`.** Added a 60s TTL cache (longer than the 15s
  client poll, so most polls hit instantly instead of paying the ~8.5s upstream
  fetch) and reused a single `TradovateBroker` instance so quotes no longer
  re-authenticate per call (also protects Tradovate's 5-req/hr auth limit).
  (`b650046`, `df2462c`, `100d146`)
- **Trend definition unified** on the scale-free EMA stack across live and replay
  (`context/trend.py`); Pine emits raw EMAs and the backend classifies. Realistic
  paper fills (1-tick slippage, pessimistic both-hit). `PRIMARY_DECISION_TF=15`.
  (`0a19385`, `dc3610e`)

### Dashboard (companion repo `vibecode-mobile` → `/var/www/rsntl`)
- Futures tab split into independent MES and MNQ blocks (decision, freshness,
  reference price, latest-webhook detail, journal) driven by `latest_webhooks`.
- `useMonitor` requires 2 consecutive failed polls before showing "Backend
  offline", so a ~15s deploy restart no longer flips the dashboard.
- Journal summary label `"N dec"` → `"N decisions"` (read as a December date).

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
