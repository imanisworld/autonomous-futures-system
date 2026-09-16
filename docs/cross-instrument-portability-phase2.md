# Cross-instrument portability layer — Phase 2 (metadata consumers)

Scope: remove every duplicated / fallback tick-size, tick-value and point-value
table from the **active** strategy, risk, runtime, replay and observation paths
and resolve them through `config/futures_contracts.py`. No instrument,
population, broker route, strategy rule, risk threshold or deployment is added.
Base: `964099c` (#582). **No proof, no run.**

## Contract metadata (unchanged values, one source)

| Root | Tick | Tick $ | $/point (derived) |
|---|---|---|---|
| MNQ | 0.25 | 0.50 | 2 |
| MES | 0.25 | 1.25 | 5 |
| M2K | 0.10 | 0.50 | 5 |
| MGC | 0.10 | 1.00 | 10 |
| MCL | 0.01 | 1.00 | 100 |
| MBT | 5.00 | 0.50 | 0.10 |
| ES / NQ | 0.25 | 12.50 / 5.00 | 50 / 20 (retained) |

`POINT_VALUE` is computed as `TICK_VALUE / TICK_SIZE`; there is no second table.
New helpers: `contract_root` (exact match, keeps the digit in `M2K`, strips
exchange prefix / `1!` / month+year suffix), `tick_size`, `tick_value`,
`point_value`, `symbol_economics`, `round_to_tick` (all raise
`UnsupportedContractError` on unknown roots) and `optional_tick_size` (returns
`None`) for observation paths. Naming a contract grants nothing: the HTTP
ingestion allowlist, `allowed_instruments`, campaign populations and the real
book are untouched.

## Duplicate economic tables / fallbacks found and what happened to them

| Path | Before | After |
|---|---|---|
| `strategy/signal_engine.py` DecisionEngine | own `TICK_SIZE` (4 roots), 15× `.get(instrument, 0.25)` | `TICK_SIZE` is the central mapping; `_tick_size()` raises; `evaluate` returns an explicit NO_TRADE for instruments without metadata |
| `strategy/signal_engine.py` ORB policy | `MAX_ORB_STOP_TICKS.get(instrument, 80)`, `MIN_STOP_TICKS.get(instrument, 4)` | no entry → no ORB setup / condition fails closed as CHOPPY. **No values added** for M2K/MGC/MCL/MBT. `orb_stop_ticks` default 8 is reachable only for instruments that already have a cap (MNQ/MES/MGC/MCL), i.e. unchanged |
| `risk/risk_engine.py` | `_POINT_VALUES` (MES/MNQ/ES/NQ, default $1), inline 4-root tick dict default 0.25 | `point_value()` / `tick_size()`; unknown root → `REJECTED` `contract_metadata_missing`. Thresholds unchanged |
| `strategy/stop_sizing.py` (live + replay stop rounding) | own 6-root table, default 0.25, `rstrip` root that mangles `M2K` | delegates to canonical `round_to_tick`; unknown root raises in both paths |
| `strategy/shadow_setups.py` | own 6-root table, default 0.25; `_FOURHR_MAX_STOP_TICKS.get(.., 80)` | central mapping; `_tick()` raises; `evaluate_shadow_setups` returns `[]` without metadata; 4HR observer requires an explicit cap |
| `webhook/runner.py` | `_TICK_VALUES` (default $1.25), `_TICK_SIZE_BY_ROOT` (default 0.25), literal `tick_size = 0.25` in stale-position force-close, `rstrip` roots | `_tick_value_for` / `_tick_size_for` / `_round_to_tick` wrap the canonical helpers and raise |
| `replay/replay_engine.py`, `main.py` | `EXEC_TICK_SIZE.get(.., 0.25)`, `EXEC_TICK_VALUE.get(.., 1.25)` | `contract_economics(state.instrument)` |
| `context/five_min_feed.py` | own 4-root table, default 0.25 | `optional_tick_size`; `None` → retest skipped with a warning (no fabricated trigger) |
| `context/structural_regime.py` | own 2-root table, default 0.25 | `optional_tick_size`; `None` → `INSUFFICIENT_DATA` "no contract metadata" |
| `execution/day_only_exit.py` | own MNQ/MES tables (defaults 0.25 / $1), `startswith` root | `contract_economics(root)` raises; `instrument_root` uses `contract_root` |

Already central since #582: `execution/paper_broker.py`,
`execution/post_fill_validation.py`, `execution/forward_evidence_campaign.py`,
`execution/evidence_identity.py`, `ops/forward_campaign_report.py`.

### Intentionally NOT changed

- `execution/tradovate_broker.py` — five `_TICK_SIZE/_TICK_VALUE .get(.., 0.25 / 1.25)`
  sites. Out of scope (Tradovate routing / real book). The real book is MNQ-only
  and every site is reached only after the MNQ-only allowlist. **Remaining
  blocker before any non-MNQ demo/live route**, not before observation.
- `scripts/*` and `research/*` historical study tools with private tables. Not
  active runtime; left for a separate cleanup so historical results stay reproducible.
- `execution/mnq_strat_evidence.py`, `execution/mes_trend_consolidation_break_evidence.py`
  index the central table by fixed key (`TICK_SIZE["MNQ"]` / `["MES"]`) — no fallback.
- Strategy policy tables (`MIN_STOP_TICKS`, `MAX_ORB_STOP_TICKS`, `orb_stop_ticks`,
  `max_stop_ticks`, `max_contracts_per_instrument`, `_FOURHR_MAX_STOP_TICKS`,
  `STRAT_122_MAX_STOP_TICKS`, entry tolerance, commissions, session filters):
  **no entries added** for M2K/MGC/MCL/MBT.

## Strategy scope preserved

60M 3-2-2 First Live, Daily 2-2 and inverse ORB remain MNQ-only; 4HR Re-Trigger's
documented universe is unchanged. The 2-1-2 / 1-2-2 detector remains
instrument-generic: it receives the instrument tick from the central table and
places entry exactly one tick beyond the boundary (tested for all six micros).

## Session / calendar finding (report only — NOT changed)

`context/futures_session.py::futures_session_active` models only the weekend
close (Fri 17:00 ET → Sun 18:00 ET) and the daily 17:00–18:00 ET maintenance
break. CME also halts the equity-index micros (MNQ/MES/M2K) 16:15–16:30 ET;
that pause is **not modelled**. Callers: `webhook/app.py`,
`execution/tradovate_supervisor.py`, `scripts/feed_watchdog.py`,
`adaptive/ops_monitor.py` — all use it for feed-staleness / heartbeat
expectations, so the gap can only produce a false "stale feed" during the
15-minute halt; it cannot open a trade. MGC/MCL/MBT follow the ordinary
17:00–18:00 ET break, which is modelled. No failing regression isolates a
one-line fix, so per the brief this is documented as the **next blocker**, not
bundled here.

## Historical roll-support matrix (`sources/polygon_client.py`)

| Root | Continuous front-contract roll | Specific dated contract read |
|---|---|---|
| MNQ, MES, ES, NQ | yes (quarterly, roll 8 days pre-3rd-Friday) | yes |
| M2K | yes (already in `QUARTERLY_SYMBOLS`) | yes |
| MGC | **no** (`PolygonError: no roll schedule`) — monthly-style listings | yes |
| MCL | **no** — monthly listings | yes |
| MBT | **no** — monthly listings | yes |

No roll schedules were added for MGC/MCL/MBT. Continuous-history proof for
those three remains **UNPROVEN**. Live feed continuity across a roll is a
separate open item (see the 2026-09-15 roll-contamination ruling).

## Verification

Focused (new file, 91 tests):

```sh
python3 -m pytest -q tests/test_contract_metadata_portability.py
```

Focused futures set (454 passed before the new file was added):

```sh
python3 -m pytest -q tests/test_risk_engine.py tests/test_stop_sizing.py tests/test_structural_regime.py tests/test_five_min_feed.py tests/test_strat_212_122.py tests/test_four_hr_retrigger_executable.py tests/test_replay_engine.py tests/test_paper_broker.py tests/test_post_fill_validation.py tests/test_cross_instrument_foundation.py tests/test_evidence_population_identity.py tests/test_forward_evidence_campaign.py tests/test_mes_122_paper_lane.py tests/test_runner_outcome_notifications.py
```

Full repository: `python3 -m pytest -q` — see PR description for the count.

Protected-file diff proof (must be empty):

```sh
git diff --exit-code origin/main -- risk_rules.yaml config/forward_evidence_campaign.json webhook/app.py execution/tradovate_broker.py context/mes_122_paper_lane.py context/wide_stop_execution.py tradingview/ deploy/
```

## Remaining blockers before any population-creation PR

1. `execution/tradovate_broker.py` fallbacks (real-book route only; MNQ-only today).
2. 16:15–16:30 ET equity-index halt not modelled in `context/futures_session.py`.
3. No continuous roll schedule for MGC/MCL/MBT; live-feed roll continuity unproven for all roots.
4. Commission / slippage proof per instrument (#582 requires explicit costs for non-MNQ outcomes; none recorded).
5. Feed availability: TradingView alerts exist only for MNQ/MES 15m; nothing demonstrated for M2K/MGC/MCL/MBT.
6. Per-instrument strategy policy (stop caps, min stops, ORB offsets) does not exist for the new roots and must be decided, not defaulted.
