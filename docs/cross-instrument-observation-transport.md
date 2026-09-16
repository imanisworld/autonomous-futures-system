# Cross-instrument observation transport (`cross_instrument_observation_v1`)

Observation-only evidence across **MNQ, MES, M2K, MGC, MCL, MBT**. Nothing in
this design grants ingestion, strategy, risk, broker or execution eligibility.
Base: `b4cb614` (#584). Rule: **No proof, no run.** Default: **OFF**.

## Hard boundaries (all tested in `tests/test_cross_instrument_observation_transport.py`)

| Boundary | Mechanism |
|---|---|
| Separate campaign | `execution/cross_instrument_observation.py`, own evidence file `cross_instrument_observation_v1.jsonl` + own state file. Never reads or writes `forward_ab_2026_08_v1` (config, populations, epoch untouched; asserted). |
| M2K/MGC/MCL/MBT never reach DecisionEngine / RiskEngine / PaperBroker / Tradovate | (1) `webhook/app.py::_route_for_ticker` sends collection-only roots to `webhook/observation_transport.py`, never to `process_alert`. (2) `process_alert` has a first-line backstop returning `OBSERVATION_ONLY` for any collection-only root without touching journal/state. (3) The transport imports none of those modules (subprocess import-graph test). (4) End-to-end test monkeypatches `DecisionEngine.evaluate`, `RiskEngine.validate`, `PaperBroker.execute_bracket/resolve_position` to raise; evidence is still produced. |
| Observation before trade-capacity gates | MNQ/MES leg runs inside `process_alert` after the bar claim and **before** `BLOCKED_MAX_TRADES` / `BLOCKED_LOSS_LOCKOUT` / `BLOCKED_OPEN_POSITION`. Collection-only roots never read daily state at all. Tested with capacity exhausted and with an open position seeded. |
| M2K 5m normalization | `context/five_min_feed.py::_root` now uses the canonical `contract_root` (keeps the digit); legacy regex only for unknown roots. |
| Pine advisory ignored | `strip_pine_advisory` clears `entry/stop/target/signal_strategy/signal_direction` before the market state is built; the ignored values are recorded on every evidence row as `pine_advisory_ignored`. Collection-only roots never call the engine that adopts Pine brackets anyway. |
| M2K/MBT ingestion only into observation | Accepted by the webhook **only while the campaign is armed**, and only on the observation route. `_INGEST_FUTURES_ROOTS` (trading path) is unchanged. MGC/MCL, already in the ingest allowlist, are now diverted to the observation route unconditionally. |
| Per-instrument feed freshness | `webhook/app.py::observation_feed_status` + `GET /status/observation-feeds`; `scripts/feed_watchdog.py::check_instruments` judges each of the six `latest_webhook_<ROOT>.json` files on its own product calendar and alerts per instrument (a healthy MNQ cannot mask a dead M2K/MBT). Never-reported instruments are not treated as outages — bar-arrival proof is the activation step. |
| Product-aware sessions | `context/futures_session.py::product_session_active(root, now)`: equity index (MNQ/MES/M2K: Globex week + 17:00–18:00 ET + **16:15–16:30 ET halt**), metals/energy (MGC/MCL: Globex week + 17:00–18:00 ET), crypto (MBT: **24/7**, maintenance Mon–Fri 16:00–16:02 CT and Sat 02:00–04:00 CT). Unknown root → `None` (never "expected idle"). The legacy `futures_session_active()` is unchanged for its existing callers. |
| Identity | `campaign × strategy × instrument × variant × evidence_epoch`; `population_key` refuses missing fields; candidate ids hash the full identity, so two epochs never share an id. |
| Zero-count visibility | `configured_populations()` enumerates every population from `config/cross_instrument_observation.json`; `build_report` / `ops/cross_instrument_observation_report.py` list all of them (89) even at zero, with `NOT ARMED` before activation. |
| No pooling | Report status is per population against the unchanged 30-terminal / 10-day gate; rows from another epoch are visible under `unconfigured_rows` and never review-eligible. |
| Not activated | `CROSS_INSTRUMENT_OBSERVATION` and `CROSS_INSTRUMENT_OBSERVATION_EPOCH` are unset by default; with either missing the campaign writes nothing and M2K/MBT stay `IGNORED`. No deploy, restart, VPS, Tradovate, risk-rule, or strategy change. |

## Collection modes (from the operator matrix)

- **structural_outcome** — bar-derived bracket is resolved forward on the
  instrument's own recorded bars: fill touch, MAE/MFE (points and R),
  pessimistic same-bar handling (both-hit = LOSS; target on the fill bar is
  never credited), WIN/LOSS/NO_FILL/EXPIRED at trading-date rollover. Gross
  geometry only; `commission_assumption_dollars` / `slippage_assumption_ticks`
  are `None` because no cost proof exists for these instruments.
  Populations: canonical `strat_212` / `strat_122` (pure tick-size state
  machine, all six), Strat 2-2 / 3-1-2 / 3-2-2 observers, impulse first
  pullback, trend consolidation break, failed-breakdown reclaim; `strat_122_pullback`
  MNQ/MES only (needs a stop-cap policy that does not exist elsewhere).
- **signal_metrics** — occurrence, direction and raw geometry only;
  `bracket_authoritative: false`; never resolved into a simulated trade.
  Populations: ORB false-break fade, EMA pullback (all six); gap fill and
  overnight sweeps (not MBT: no session semantics); VWAP hold / rejection
  observers and 4HR observer **MNQ/MES only** (they re-run DecisionEngine
  builders or are current-scope-only).

Collection-only roots run `strategy/shadow_setups.evaluate_shadow_setups(..., include_canonical_observers=False)`;
that module no longer imports the risk engine (`_reward_to_risk` mirrors
`RiskEngine.calculate_rr` exactly, tested).

## Activation (NOT done here — separate operator step)

1. Prove bar arrival per instrument (15m and, where used, 5m) with
   `GET /status/observation-feeds` after TradingView alerts exist for each root.
2. Set `CROSS_INSTRUMENT_OBSERVATION=cross_instrument_observation_v1` and
   `CROSS_INSTRUMENT_OBSERVATION_EPOCH=<release sha>+<arm time>` on the box; restart.
3. Watch `ops/cross_instrument_observation_report.py --log-dir …` — every
   population must appear, most at zero on day one.

## Deferred (unchanged)

MGC/MCL/MBT historical roll schedules; per-instrument commissions/slippage;
per-instrument trading rules; `webhook/payload.py` price-sanity parser (M2K/MBT
have no range yet, so unknown roots pass unchecked — noted for later);
Tradovate adapter fallbacks (real book, MNQ-only).

## Verification

```sh
python3 -m pytest -q tests/test_cross_instrument_observation_transport.py
python3 -m pytest -q
git diff --exit-code b4cb614 -- risk_rules.yaml config/forward_evidence_campaign.json execution/tradovate_broker.py execution/paper_broker.py execution/forward_evidence_campaign.py execution/evidence_identity.py strategy/signal_engine.py risk/risk_engine.py context/mes_122_paper_lane.py context/wide_stop_execution.py tradingview/ deploy/
```
