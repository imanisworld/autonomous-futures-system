# C14 — Live/replay daily-identity divergence fence — 2026-09-17

**Status:** AUDIT RECORD for PR #646. Documents every runtime consumer of a mechanical
"18:00 ET == new day" key that this PR deliberately leaves untouched, what it can and cannot
affect, and the evidence restriction that applies until a separately authorized runtime PR
unifies it. No runtime, execution, risk, broker, strategy, env, deploy or merge change.

## 1. What #646 changes and what it does not

| layer | daily identity after #646 | source |
|---|---|---|
| Replay converters (`scripts/csv_to_replay.py`, `scripts/polygon_to_replay.py`) — VWAP reset, HOD/LOD, PDH/PDL/PDC, `resample_daily` | `cme_trading_day(ts, instrument)`: CME equity-index trade date for MES/MNQ/M2K (holiday-aware, proven vs Pine `time_tradingday`); mechanical key for every other product | changed by #646 |
| **Live signal path** — `state.previous_day.high/low`, `price_vs_pdh/pdl`, `vwap`, `hod/lod` used by `strategy/signal_engine.py`, `strategy/confluence_scorer.py`, `execution/post_fill_validation.py` | **TradingView Pine payload values** (`webhook/state_builder.py` reads `payload.previous_day_high` etc.). Pine already uses the exchange trade date, i.e. the identity #646 reproduces. | not derived in Python; **no divergence** |
| `context/location_context._trading_day` → `_day_ranges()` → `build_location_context()` | mechanical `(ET + 6h).date()` | **untouched — DIVERGES on holiday transitions** (§2) |
| `context/daily_22_swing_collector._trading_day` (paper-only Daily 2-2 lane) | mechanical, with the 17:00–18:00 halt returning `None` | untouched — diverges on holiday transitions (§3) |
| `research/structural_level_features.py` (imports `location_context._trading_day`), `scripts/structural_level_parity_check.py` | mechanical | untouched — **frozen R6 feature definitions** (operator ruling 2026-09-17: do not change) |
| `webhook/state_builder.detect_session`, `csv_to_replay.detect_session` | sub-session labels (asian/london/new_york/off_hours) only | no daily identity; unaffected |

## 2. `context/location_context._trading_day` — the only live-path consumer

**Where it runs.** `webhook/runner.py` ≈ L820–845: for `state.instrument in {"MES","MNQ"}` on
15m (non-5m-trigger) bars only, inside `try/except` ("evidence must never affect ingestion").
The result is stored as `state.location_context` and

- journaled under `context.location_context` (`runner.py` ≈ L3491);
- used by `_annotate_candidate_locations()` to attach a `location` field to every
  `candidate_audit` / `shadow_candidates` row (`runner.py` L1971, L2232, L2320).

**Authority.** None. `grep` over `strategy/`, `risk/`, `execution/`, `adaptive/` finds **no
reader** of `location_context`, `candidate_location` or the `location` field. The module docstring
and the runner comment both state "observation only … has no authority over decision/risk/routing"
(operator-approved 2026-07-16). `candidate_location()` reads only `loc["zones"]`, which come from
`aggregate()` (epoch-aligned 1h/4h buckets) — **not** from the trading-day key.

**Fields that CAN diverge from the fixed replay** (all inside the journaled `location_context`):

| field | how it is built | divergence on a holiday transition |
|---|---|---|
| `levels.pdh`, `levels.pdl`, `levels.prev_open`, `levels.prev_close` | previous mechanical day bucket | On the first trade date after a holiday (e.g. Tue 2026-09-08 bars; Mon 2026-06-22 / 2026-07-06 bars) live "previous day" = the holiday session bucket (Sun 18:00→Mon 13:00, or Thu 18:00→Fri 13:00); Pine/replay previous day = the last full trade date before the holiday (Fri 09-04 / Thu). |
| `levels.onh`, `levels.onl` | bars of the mechanical "today" bucket before 09:30 ET | After a Monday holiday, live overnight = Mon 18:00→Tue 09:30; Pine trade date 09-08 began Sun 18:00. (Replay does not compute ONH/ONL, so this is live-vs-Pine, not live-vs-replay.) |
| `levels.pmh`, `levels.pml` | 04:00–09:30 ET of the mechanical "today" | Same bucket dependence; identical values on the bars that exist, because the 04:00–09:30 window is inside both keys. Divergence limited to bucket membership. |
| `nearest_key_level` | argmin over `levels` | Inherits any of the above. |

**Fields that CANNOT diverge:** `zones` (1h/4h, epoch-aligned), `middle_of_range`, `impulse`,
`mtr_15m_points`, `regime_at_signal`, `other_instrument`, `regime_agreement`, and every
per-candidate `location` value (zone-based only).

**State, journal, decisions.** `location_context` is recomputed from bar history on every bar
(no carried state), never written back to any state file, never read by a gate, never sized or
routed on. A divergent value therefore changes **journal evidence only** — never signal
eligibility, context permission, bracket, fill, or the active book.

**Affected rows (identifiable causally).** A journal row is C14-tainted for `location_context.levels`
iff, for its instrument, `cme_trading_day(ts, instrument)` ≠ mechanical key for ANY bar of the row's
trade date — i.e. rows on the trade date that follows a Monday holiday (Tue) or a Friday holiday
(Mon), plus the holiday session bars themselves. In the live-journal window the P3 parity report
compared (2026-07-16 → 09-16) that is exactly the **2026-09-07/08 Labor Day span** (the P3 parity report already isolated these rows:
MES 09-08 `wall_context` 92/92 disagree with zero gap).

## 3. `context/daily_22_swing_collector._trading_day` — paper-only Daily 2-2 lane

Buckets the 5m context feed into "Daily" sessions to discover the Daily 2-2 continuation. On a
holiday transition its Daily bar differs from TradingView's Daily bar (it splits the holiday span
into two days). Consumers: `context/wide_stop_forward_router.py`, `context/daily_22_state_integrity.py`
— an isolated $5,000 hypothetical ledger, MNQ only, no broker path, no promotion path. Not part of
C14's replay scope; recorded here because it is a second mechanical key in runtime code. Existing
memory already carries the related roll-contamination note for this lane.

## 4. Execution restriction until parity is repaired

- Runtime is `SCHEDULE_MODE=always_on_shadow` + `EXIT_MODE=static` (paper/shadow, no orders) and the
  standing directive forbids runtime changes before 2026-09-30; nothing in this PR relaxes that.
- Live signal PDH/PDL/VWAP/HOD/LOD are Pine-sourced, so **no signal-path restriction is needed** for
  holiday rows — the divergence is confined to observation fields.
- **Evidence restriction (binding for reviews using `location_context.levels` / `nearest_key_level`):**
  exclude or flag rows whose trade date is the first after a CME holiday, and the holiday session
  itself, until the runtime helper is unified. The P3/structural-level parity tooling already reports
  disagreements by trading day and gap flag; `cme_trading_day()` is the causal predicate to compute
  the flag offline.
- `vwap_*` evidence remains `NOT_ADMITTED` / `LIVE_ONLY` per the prereg; #646 does not change that.

## 5. Follow-up (separate, explicitly authorized runtime PR)

Point `context/location_context._trading_day` at the same proven `cme_trading_day(ts, instrument)`
source (MES/MNQ only, which is the only place the runner calls it), with a fixture test on the
Labor Day journal span. The frozen R6 feature definitions in `research/structural_level_features.py`
must NOT be re-pointed retroactively; a rerun would be a new feature version by definition.
