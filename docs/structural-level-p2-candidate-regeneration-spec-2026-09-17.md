# Structural-Level Prereg — P2: Candidate-Regeneration Specification + Family Compatibility Matrix (2026-09-17)

**Status:** RESEARCH SPECIFICATION ONLY (docs). Nothing here was run. No outcome was read,
no candidate was scored, no parameter was tuned, no P&L was computed, no runtime path was
wired. Prereg under execution: `docs/prereg-dynamic-structural-level-attribution-2026-09-16.md`
**v1.3** (P3 closed, `docs/structural-level-parity-p3-2026-09-16.md`). This document fills
prereg §14 **P2** (regeneration spec) and **P5** (family compatibility matrix, §2.3) from code
inspection at `df18532` and a candidate-only census of the frozen P-LIVE snapshot.
**Verdict at the end: P2 SPEC COMPLETE — NEEDS CORPUS REBUILD before the regeneration run.**

---

## 1. What "the candidate population" is (and is not)

The prereg population is the **shadow-candidate population** — the same one the closed
context-permission study joined (7,563 rows). It is produced on every 15m decision bar by one
module on both paths:

| Path | Call | Bars handed to the detectors | Resolver |
|---|---|---|---|
| Live (`webhook/runner.py:987`) | `evaluate_shadow_setups(state, recent_bars, cfg)` | `BarHistory.recent(inst, 8)` — the last 8 recorded 15m bars, current bar included (recorded at `:780` before the call) | `strategy/shadow_resolver.py` on later live bars (same-day forward window, `SHADOW_OUTCOME` rows) |
| Replay (`replay/replay_engine.py:546`) | `evaluate_shadow_setups(state, list(self._research_bars[inst]))` | `deque(maxlen=8)` of the corpus candles, current candle included | `resolve_shadow_candidate(cand, forward_bars=[(h,l) of the rest of the day's candles])` inline, written as `shadow_candidates[].outcome` |

Both call `strategy/shadow_setups.evaluate_shadow_setups` (sha256 `4e535d70…`) and both
resolve with `strategy/shadow_setups.resolve_shadow_candidate` (resting entry, pessimistic
both-hit, fill-bar target-only ignored, same-day window). **Bracket-formula parity is
therefore structural for every shadow family** — the same function computes entry/stop/
target from the same `MarketState` fields. What can differ is the *inputs* to those fields
(Pine vs corpus-derived), which is what the matrix below tracks.

**Not a population source in tranche 1:**

- `candidate_audit` (DecisionEngine executable families `orb_breakout`, `orb_reclaim`,
  `vwap_*`, `pdh_/pdl_reclaim`, `strat_*`): produced only by `_iter_enabled_setups`, i.e.
  gated by `risk_rules.yaml enabled_concepts` (currently `orb_breakout` only) and by the
  regime/ranked-mode stages; 357 live rows in the window. It is a *selected* population
  shaped by config and by the unvalidated confluence score (prereg C12), not an ungated
  detector census. Excluded; the prereg §2.3 "expected both" list for these families is
  superseded by this matrix.
- `range_signal` (`range_break_close`): bracket built from `wall_context` (needs Pine S/D;
  carries C1), never built in replay → `LIVE_ONLY` (P-LIVE strata only).
- 5m-native lanes and the S2 PaperBroker lanes (`mnq_strat_22_reversal`,
  `mes_trend_consolidation_break`): provenance strata, not candidate generators.

## 2. Family compatibility matrix (P5)

Live counts = shadow candidates on 15m MNQ/MES decision rows, `context.timestamp` in
2026-07-16 → 2026-09-14T22:00Z (exclusive), from the frozen snapshot — **candidates only, no
outcome field was read**. "Formula" = the bracket arithmetic in `shadow_setups.py`; "identical
code path" is true for every row below. "Input divergence" lists the `MarketState` inputs whose
*source* differs between Pine-fed live and corpus-derived replay.

| Family (`strategy` id) | Detector | Inputs it reads | Live source | Replay source (`polygon_to_replay` / `_state_from_candle`) | Input divergence | Live n (MNQ / MES) | Sessions live | Verdict |
|---|---|---|---|---|---|---|---|---|
| `strat_22_continuation_observed` | `_missing_strat_family` | `strat.strat_sequence/direction`, raw `previous_bar_high/low`, tick | Pine bar types → `classify_sequence` | corpus bar types (`classify_htf_bar`, "matches Pine `classify_bar()`" per builder doc) → `classify_sequence`; `raw = candle` | bar-type classification source | 1,325 / 1,212 | all three | **BOTH** — bar-type parity to be measured on the parity corpus (§4) |
| `strat_22_reversal_observed` | same | same | same | same | same | 603 / 619 | all three | **BOTH** (same condition) |
| `strat_312_observed` | same | same | same | same | same | 66 / 65 | all three | **BOTH** (same condition) |
| `strat_322_reversal_observed` | same | same | same | same | same | 82 / 65 | all three | **BOTH** (same condition) |
| `strat_122_observed` | `_strat_122_pullback` | `strat_122` sequence, `ohlc`, `STRAT_122_MAX_STOP_TICKS` | Pine bar types | corpus bar types | bar-type source | 29 / 127 | all three | **BOTH** (same condition) |
| `strat_122_pullback` | same | same | same | same | same | 67 / 7 | all three | **BOTH**; MES n=7 → `NOT_TESTABLE` on P-LIVE |
| `strat_4hr_retrigger_observed` | `_strat_4hr_retrigger_observed` | NY early window, `trend`, `volume.relative`, `ohlc` | `trend` = `classify_trend(close, Pine EMA9/21/55)`; `relative` = Pine volume / Pine SMA20 | `classify_trend` on corpus EMAs (SMA-seeded); `relative` = corpus volume / SMA20 | EMA and avg-volume source | 16 / 29 | new_york only | **BOTH — input-divergent** (EMA); small n |
| `orb_false_break_fade` | `_orb_false_break_fade` | `orb.status ∈ {rejected_high, rejected_low}`, `orb.high/low`, `ohlc` | Pine NY/London ORB routed by `state_builder` | NY ORB from candle (masked outside NY); **London ORB only if the candle carries `london_orb_*`** | ORB source — **the 2024-07..2026-06 15m corpora predate `london_orb_*`** → London rows absent in replay until the corpus is rebuilt | 173 / 190 (asian 62, london 178, new_york 123) | asian*, london, new_york | **BOTH after corpus rebuild** (§5 R1). *Asian rows are `STALE_ORB` (pre-09-04 leak of the previous NY ORB, C1) → **excluded from P-LIVE**; replay masks them by construction |
| `ema_pullback_trend` | `_ema_pullback_trend` | `key_levels.ema_9/21/55`, `ohlc` | Pine EMAs | corpus EMAs (standard EMA seeded with SMA) | EMA source — firing depends on ≤ 1-tick touches of EMA values, so small EMA differences change *which bars fire* | 609 / 671 | all three | **BOTH — input-divergent** at census; **R4 result: firing 0.995 PASS, bracket 93.88 % FAIL → `REPLAY_ONLY` + `LIVE_ONLY` strata, never pooled (prereg v1.4 Ruling 2)** |
| `impulse_first_pullback_observed` | `_impulse_first_pullback` | last 4 of the 8 recent bars, `trend.direction`, tick | BarHistory bars + Pine-EMA trend | corpus candles + corpus-EMA trend | trend source (EMA) | 555 / 522 | all three | **BOTH — input-divergent** (trend only) |
| `trend_consolidation_break_observed` | `_trend_consolidation_break` | last 5 recent bars, `trend.direction` | same | same | trend source (EMA) | 192 / 222 | all three | **BOTH — input-divergent** (trend only) |
| `transition_failed_breakdown_reclaim` | `_failed_breakdown_reclaim` | `market_condition ∈ {RANGE_BOUND, CHOPPY, TRANSITION}`, 8 bars incl. current, volume | Pine `market_condition` (**MES top-level label `None` since 07-28 — C8**) | corpus `reconstructed_market_condition` (Pine formula reconstruction) | regime label source; MES live blind | 6 / 6 | all three | **NOT_TESTABLE** on P-LIVE (n=12); replay-only population possible but MES live-vs-replay not comparable (C8) |
| `vwap_hold_observed` / `vwap_rejection_observed` | `canonical_observers` (re-runs DecisionEngine `_try_vwap_hold/_rejection`) | `FORWARD_EVIDENCE_CAMPAIGN` env, MNQ only, VWAP, trend, bar type | campaign ON on the box | env unset in replay → **absent** | campaign gate + VWAP source (C14) | 155 / 0 (hold); rejection 0 | new_york | **LIVE_ONLY**; and **not promotion-grade** on holiday weeks (C14). Kept OFF in P-REPLAY by manifest |
| `ovn_high/low_sweep_reclaim` | `_overnight_sweep_reclaim` | raw `overnight_high/low` | payload has no such field | candle has no such field | — | 0 / 0 | — | **DEAD** on both paths (never fires) |
| `gap_fill` | `_gap_fill` | raw `rth_open` / `session_open` | payload has no such field | candle has no such field | — | 0 / 0 | — | **DEAD** on both paths |
| `range_break_close` | `context/range_signal` via `wall_context` | Pine S/D walls, ORB status | live only (`range_observe_enabled`) | not built | — | ~1,558 (closed study) | all three | **LIVE_ONLY** |

Shared inputs with *no* source divergence: `session` (`detect_session` both), tick sizes
(`config/futures_contracts`), the 8-bar recent window (both include the current bar), the
resolver, and the ORB routing rules once the corpus carries London fields.

**What the matrix means for the confirmatory family.** Only `BOTH` families can reach a
pooled P-REPLAY + P-LIVE statement. `BOTH — input-divergent` families are admitted to
P-REPLAY on their own and to the pooled statement only if the §4 firing/bracket parity
thresholds pass on the parity corpus; otherwise they are reported as `REPLAY_ONLY` +
`LIVE_ONLY` strata. `LIVE_ONLY`, `NOT_TESTABLE` and `DEAD` families never enter the pooled
confirmatory statistic.

## 3. Regeneration specification for P-REPLAY (not run here)

### 3.1 Pins (frozen when the run is authorized; recorded in the run manifest)

| Item | Value / rule |
|---|---|
| Code SHA | `origin/main` at the moment of the go (this spec written at `df18532`); the manifest records `git rev-parse HEAD` and the sha256 of `strategy/shadow_setups.py` (`4e535d70…`), `strategy/shadow_resolver.py` (`d200ae0d…`), `replay/replay_engine.py` (`9ab1f72b…` after PR #621 `4f07ea0`, the day-file history fix; was `8de84e13…`), `scripts/polygon_to_replay.py` (`a42e1cae…`), `scripts/csv_to_replay.py` (`332e48a9…`), `research/structural_level_features.py` (`4686ddf9…`), `risk_rules.yaml` (`6c7e3f13…`) |
| Corpus | **R1 (§5) — rebuilt** 15m Polygon corpora `data/replay_polygon_v2/{MNQ,MES}`, **2024-10-01 → 2026-06-26** (prereg v1.4 Ruling 1 — the provider retains ~2 years, so the v1.0 start 2024-07-01 is not fetchable; warm-up 2024-09-17 → 09-30 via `scripts/structural_level_corpus_build.py --start 2024-10-01 --warmup-days 14`), produced by the pinned `polygon_to_replay.py` (which emits `london_orb_*`, `reconstructed_market_condition`, `legacy_market_condition`); `MANIFEST.json` per corpus with contract segments and roll rule (as `MES_ext_boxroll` already has). The existing `data/replay_polygon/*` files predate the London ORB fields and **must not be used** |
| Config | `risk_rules.yaml` byte-pinned by hash; `selection_mode: ranked` and `enabled_concepts` left exactly as pinned (they do not affect `shadow_candidates`; they are recorded so the DecisionEngine side of the journal is reproducible) |
| Environment | `FORWARD_EVIDENCE_CAMPAIGN` **unset** (canonical VWAP observers OFF → `vwap_*_observed` absent, matching the `LIVE_ONLY` verdict); `htf_direction_source` as pinned in config; no other env |
| Command | `python3 scripts/run_replay_batch.py --candles data/replay_polygon_v2/<INST> --log-dir logs/replay_p2/<INST> --fresh`, one process per instrument, sequential days (the engine carries positions and balance across days; irrelevant to shadow rows but recorded) |
| Timeframe | 15m only; no 5m corpus; no 5m-native lanes |

### 3.2 Outputs and the outcome seal

Replay writes `logs/replay_p2/<INST>/journal_<day>.jsonl`; every decision row carries
`shadow_candidates[]` with `strategy, direction, entry, stop, target, rr_ratio, risk_tier,
size_multiplier, notes, outcome{result, entry_filled, exit_reason, exit_price, pnl_ticks,
bars_to_fill, bars_to_exit, fill_bar_target_ambiguous_ignored}` and `bar_ts`. Because the
engine resolves outcomes inline, the extraction step (**P2-X**, a read-only script, not yet
written) must split each row into two files:

1. `candidates.jsonl` — `candidate_key` (= `shadow_setups|inst|bar_ts|strategy|direction|entry`,
   the resolver identity, byte-identical to `strategy.shadow_resolver._candidate_key`),
   instrument, `bar_ts`, session, family, direction, entry, stop, target, rr, and the
   corpus-derived context needed for strata (`reconstructed_market_condition`). **No outcome
   field.**
2. `outcomes.sealed.jsonl` — `candidate_key` + the `outcome` block. Its sha256 is written to
   the manifest at extraction time and the file is not opened until the structural feature
   table (P1 `label_candidate` over the candidates file) is complete, frozen and hashed.

Both files, the manifest and the P3-style integrity report are the P2 run deliverable. The
analysis (a later step with its own go) joins on `candidate_key` only.

### 3.3 Integrity checks the run manifest must pass (prereg §9; fail-closed)

- **Population source:** every candidate row traceable to a decision row in the run journal;
  candidates per family per instrument reported.
- **Duplicate keys:** byte-identical duplicates collapse and are counted; conflicting
  duplicates → `CONFLICTING_DUPLICATE`, population × family BLOCKED (prereg §9.1).
- **Roll ledger (P6):** session-open gaps at the 8-days-pre-expiry roll dates extracted from
  the corpus (must reproduce the known 2026-06-11 +277.5 MNQ gap); per-feature contamination
  windows (§9.5) attached to candidate rows as flags.
- **Gap ledger:** 15m-grid gaps inside CME hours (expected near-empty); per-level
  `gap_contaminated` from P1 attached to every candidate row.
- **Session census:** `orb_false_break_fade` must have **zero** asian-session rows in replay
  (ORB masked outside NY/London) — a non-zero count is a build defect, not data.
- **ORB availability:** `NOT_AVAILABLE` session-days (missing 09:30 / 03:00 candle) counted
  per instrument.
- **Family census vs matrix:** `ovn_*`, `gap_fill`, `vwap_*_observed`, `range_break_close`
  must be absent; any presence is a manifest/env error → stop.
- **Determinism:** two consecutive runs of one day file produce byte-identical
  `shadow_candidates` (outcomes included) — the run is not accepted otherwise.

## 4. Firing / bracket parity on a parity corpus (P2-P, frozen thresholds; not run here)

The P-REPLAY corpus ends 2026-06-26 and P-LIVE starts 2026-07-16, so replay-vs-live candidate
parity cannot be measured on P-REPLAY itself. A **parity corpus** is built for exactly the
P-LIVE window — 15m Polygon, both instruments, 2026-07-16 → 2026-09-14T22:00Z, U6 contract
only (stop at the roll), pinned builder — used **only** to regenerate candidates and compare
them with the live journal's `shadow_candidates`. No outcome is read from either side.

Per family, on bars present in both sources:

- **Firing agreement** = |live ∩ replay| / |live ∪ replay| over (instrument, `bar_ts`,
  strategy, direction). Threshold **≥ 0.90** → family stays `BOTH`; else it is split into
  `LIVE_ONLY` + `REPLAY_ONLY` strata for the pooled statement (still analysed per stratum).
- **Bracket agreement** on co-fired candidates: entry, stop and target each within **one
  tick** on ≥ 98% of co-fired rows; a failure is a definition conflict (the formula is the
  same function, so a bracket miss means an input miss) and is reported, not tolerated.
- Feed-gap days in the live bar history (P3 gap ledger) are excluded from the firing
  denominator and counted (the live side could not have fired on a missing bar).

These thresholds are frozen now, before any candidate is regenerated.

## 5. Prerequisites and order (none executed in this task)

| # | Prerequisite | Output | Touches runtime? |
|---|---|---|---|
| R1 | Rebuild P-REPLAY 15m corpora with the pinned `polygon_to_replay.py` (London ORB fields, reconstructed regime, `MANIFEST.json` with contract segments) for MNQ and MES **2024-10-01 → 2026-06-26** (v1.4 window; see `docs/structural-level-p2-parity-corpus-r4-2026-09-17.md` §3) | `data/replay_polygon_v2/{MNQ,MES}` + manifests | no (data fetch) |
| R2 | Build the parity corpus 2026-07-16 → 2026-09-14T22:00Z (U6) for MNQ and MES | `data/replay_polygon_parity_2026_07_16_09_14/{MNQ,MES}` | no |
| R3 | Write **P2-X** (read-only extractor: candidates / sealed outcomes / manifest hashes / integrity report) and **P2-P** (firing-bracket parity tool). Both import nothing from webhook/execution/broker | two scripts + tests | no |
| R4 | Run P2-P on R2 against the P-LIVE snapshot → family verdicts finalised (§2 "BOTH — input-divergent" rows resolved) | parity report | no |
| R5 | Run the regeneration (§3) on R1; run P2-X; seal outcomes; integrity report | candidates.jsonl, outcomes.sealed.jsonl, manifest | no |
| R6 | P1 feature table over candidates.jsonl (levels + events + labels); freeze + hash | features.jsonl | no |
| R7 | Independent spot-check (prereg P8) of ≥ 3 seeded candidate rows: bracket from bars, features from bars | attestation | no |

Only after R7 may the sealed outcomes be opened — under a separate go.

## 6. Population exclusions discovered by this census (P-LIVE, binding)

- `orb_false_break_fade` **asian-session rows (62)**: built on the previous day's NY ORB that
  leaked into Asian payloads before the 2026-09-04 fix (C1). By §3.6 no ORB exists in Asian;
  rows flagged `STALE_ORB_CONTAMINATED` and excluded, counted.
- `transition_failed_breakdown_reclaim` (n = 12) → `NOT_TESTABLE` family on P-LIVE.
- `strat_122_pullback` MES (n = 7) → `NOT_TESTABLE` cell.
- `vwap_hold_observed` (MNQ, NY, 155): `LIVE_ONLY`; additionally not promotion-grade on
  holiday weeks (C14).
- `range_break_close`: `LIVE_ONLY`.
- All rows with `bar_ts ≥ 2026-09-14T22:00Z` (Z6) excluded (roll cut), as in P3.

## 6a. v1.5 addendum — M2K (2026-09-17)

Prereg v1.5 adds M2K. For the regeneration and parity machinery this means:

- **P-REPLAY corpus:** `data/replay_polygon_v2/M2K` (same window, pinned builder, quarterly
  scheduler; 40,878 rows / 543 files; manifest `1f1e54f5…`) was built but is **NOT ADMITTED**:
  **X0 (#622 §3 / #625)** re-established contract identity from the provider for every segment
  (40,878/40,878) but none of its seven `roll_days=8` UTC-midnight seams is independently
  proven (no live feed in the window; provider volume crossed 3–4 days after every seam) →
  `ROLL_PROVENANCE_UNKNOWN` — `docs/structural-level-v15-m2k-2026-09-17-x0-v2.json`. The §3.1
  regeneration command is therefore **not run for M2K** (no `--candles data/replay_polygon_v2/M2K`
  step until an M2K roll rule is proven or seam-free per-contract windows are admitted under a
  separate go); the MNQ/MES v1.4 corpora are unchanged (prereg v1.5 C23, #622 §1.2).
- **Live population source for M2K:** the cross-instrument observation lane
  (`logs/cross_instrument_observation_v1.jsonl` CANDIDATE + SIGNAL rows, `logs/bars_M2K_*.jsonl`),
  loaded by `research.structural_level_p2.iter_observation_rows`; P2-P takes
  `--live-source observation`. No P-LIVE history exists before the epoch `2026-09-16T12:17:19Z`.
- **Parity corpus for M2K:** `data/replay_polygon_parity_m2kz6_2026_09_16/M2K` — **single dated
  contract `M2KZ6`** (builder `--contract M2KZ6`, 2026-09-01 →, no seam; manifest `1e3ac8f2…`;
  X0 `PROVEN`, live bars identified Z6 27/27), extended as the observation window grows and
  seam-free until the December roll. The first-draft stitched corpus
  `data/replay_polygon_parity_m2k_2026_09_16/M2K` (`roll_days=3`, seam 2026-09-15T00:00Z;
  manifest `78d4127d…`) is **`ROLL_PROVENANCE_UNKNOWN` / NOT ADMITTED** — M2K's live feed
  switch was never observed, so its seam cannot be reconciled (X0 report
  `…-x0-parity-stitched.json`); do not use it for M2K parity.
- **Family matrix additions (C21):** `strat_212`, `strat_122` = `LANE_ONLY` (observation-lane
  canonical detector), must be absent from replay (manifest check). Not configured for M2K by
  the lane: `strat_122_pullback`, `strat_4hr_retrigger_observed`, `vwap_*`.
- **Extra prerequisites:** X0-M2K per corpus (`scripts/structural_level_x0_roll_proof.py`),
  P3-M2K (bar-source levels parity), P5-M2K (lane vs replay parity), P-R (resolver equivalence,
  synthetic). Evidence: `docs/structural-level-v15-m2k-2026-09-17.md`.

## 7. What P2 deliberately does not do

No corpus was fetched, no replay was run, no candidate was regenerated, no outcome opened,
no threshold beyond §4's frozen parity gates was chosen, no family was reweighted, no runtime
or config file changed, and the prereg's confirmatory family, level definitions and event
definitions are untouched (P3 stays closed; no contradiction in an admitted level definition
was found — the only new defect is a **corpus-content** gap, London ORB fields missing from
the pre-existing corpora, which is R1).

---

**Verdict: P2 SPEC COMPLETE — NEEDS CORPUS REBUILD.** The regeneration is fully specified
and every family is classified, but it cannot be run against the existing
`data/replay_polygon/*` files: they predate the London ORB fields the frozen §3.6 definition
requires, so `orb_false_break_fade` (London) and the London-session ORB events of every family
would be silently absent. R1/R2 are data fetches with the pinned builder — no runtime change.

**Safe Next Step:** R1 + R2 (corpus rebuilds with `MANIFEST.json`) and R3 (the two read-only
tools with tests) — then stop and report the parity-corpus family verdicts (R4) before any
regeneration run.
