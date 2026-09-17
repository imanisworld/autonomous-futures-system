# Structural-Level Prereg v1.5 — M2K Added; R4 Rerun on the #621 Replay Fix (2026-09-17)

**Mode:** RESEARCH ONLY / PAPER ONLY. No runtime, deployment, strategy, risk, config, `.env`,
Pine, collector or gate change. Box only read (a tar-over-ssh copy of `logs/bars_M2K_*.jsonl`
and `logs/cross_instrument_observation_v1.jsonl`, taken 2026-09-17T02:15:04Z). **No outcome
was read**: OUTCOME records are skipped by `record_type` before any other field is looked at;
the resolver-equivalence proof runs on synthetic bars only. **R5 not run.**

**Operator instructions covered:** (a) "add M2K first as a v1.5 amendment … add each to
everything we have done" (MGC / MCL / MBT deferred to tranche-2 amendments — they need
product-specific level definitions and non-quarterly roll schedules); (b) "rerun R4 parity with
[the #621] fix included and confirm those two affected families improve without breaking
anything else".

Code SHA: `4f07ea0` (origin/main with PR #621). Machine artifacts in this PR:
`…-v15-m2k-2026-09-17-parity.json` (P5-M2K), `…-bar-source-parity.json` (P3-M2K),
`…-resolver-equivalence.json` (P-R), `…-p2-parity-corpus-r4-2026-09-17-results-post621.json`
(R4 rerun), manifests `replay_polygon_v2_M2K_MANIFEST.json` and
`replay_polygon_parity_m2k_2026_09_16_M2K_MANIFEST.json`.

---

## 1. What "adding M2K" means (and what changed in the prereg)

| Layer | MNQ / MES (v1.4) | M2K (v1.5) |
|---|---|---|
| Level / event definitions (§3, §5) | frozen v1.3 | **unchanged** — equity-index micro on the same Globex session map, 18:00 ET day roll, 09:30 ET RTH/NY ORB, 03:00 ET London ORB; tick 0.10 from `config/futures_contracts` |
| P-REPLAY | `data/replay_polygon_v2/{MNQ,MES}` 2024-10-01 → 2026-06-26 | `data/replay_polygon_v2/M2K`, same window, same pinned builder, quarterly roll (§2) |
| P-LIVE (calibration) | runner journal 2026-07-16 → 09-14T22Z | **none** — no M2K history before the observation epoch |
| P-OOS-PROSPECTIVE | runner journal from 2026-09-17 | cross-instrument observation lane from epoch `2026-09-16T12:17:19Z` |
| Live candidate source | runner `shadow_candidates` | `observe_collection_only_alert` → **same `evaluate_shadow_setups`** (canonical VWAP observers off, Pine advisory brackets stripped) → CANDIDATE (`structural_outcome`, bracket authoritative) + SIGNAL (`signal_metrics`, bracket not authoritative, never resolved by the lane) rows |
| Live resolver | `resolve_shadow_candidate` | lane's `_resolve_one` — a second implementation (**C20**), proven equivalent (§4) |
| Extra families | — | `strat_212`, `strat_122` from `advance_strat_212_122` → **`LANE_ONLY`** (**C21**); `strat_122_pullback`, `strat_4hr_retrigger_observed`, `vwap_*` not configured for M2K |
| Levels parity source | journaled `location_context` (P3) | none journaled (**C22**) → **P3-M2K** = bar-source parity (live bars vs Polygon bars → OHLC + admitted levels) |
| Strata (§7) | instrument ∈ {MNQ, MES} | ∈ {MNQ, MES, M2K} |

Prereg header records all of this as the v1.3 → v1.4 → **v1.5** changelog; `PREREG_VERSION = "1.5"`.

## 2. Corpora (local, gitignored; manifests committed)

| Corpus | Range | Rows / files | Checks |
|---|---|---|---|
| `data/replay_polygon_v2/M2K` (R1-M2K) | 2024-10-01T00:00Z → 2026-06-26T20:45Z (warm-up 2024-09-17 →) | **40,878 / 543** (29 fewer bars than MNQ/MES — thin M2K slots, all in the gap ledger) | `ema_200` + `previous_day_*` populated from row 1; all required fields; timestamps strictly increasing; manifest per-file sha256 = disk (543/543); 7 rolls (H5 +26.8 … U6 +20.5); 57 gap runs, every run ≥ 4 h an exchange holiday/early close; manifest `1f1e54f5…` |
| `data/replay_polygon_parity_m2k_2026_09_16/M2K` | 2026-09-01T00:00Z → 2026-09-16T18:15Z (`roll_days=3`: U6 → Z6 seam 09-15, +22.1 pts; Polygon had not yet published 09-16 18:30Z → 09-17 at fetch time) | 1,058 / 14 | manifest hashes = disk; gap = Labor Day early close only; manifest `78d4127d…` |

## 3. Tooling added (all read-only; 22 tests in `tests/test_structural_level_p2.py`, full suite green)

- `research/structural_level_p2.py`: `iter_observation_rows()` (evaluated bar = every 15m bar in
  `bars_M2K_*.jsonl`, which only the observation transport writes; candidates = CANDIDATE +
  SIGNAL at that bar; OUTCOME rows skipped unread), `OBSERVATION_LANE_ONLY_FAMILIES` →
  `LANE_ONLY` classification with a `MANIFEST_ERROR` if such a family ever appears in replay.
- `scripts/structural_level_p2_parity.py --live-source observation --observation-evidence … --bars-root …`.
- `scripts/structural_level_bar_source_parity.py` (P3-M2K): OHLC parity per common bar and
  admitted-level parity from `build_levels` on each source at every common B0 (one tick, ≥ 98 %
  eligible, §9.4 denominator; one-sided `NOT_AVAILABLE` counted, not scored).
- `scripts/structural_level_resolver_equivalence.py` (P-R): synthetic-only proof
  `resolve_shadow_candidate` ≡ `_resolve_one`.

## 4. M2K evidence (preliminary — 25 co-evaluated bars, 2026-09-16 12:15 → 18:15Z)

**P5-M2K — observation lane vs replay (fixed engine `4f07ea0`), frozen §4 gates:**

| Family | Live firings (51 lane bars) | Replay firings (co-evaluated) | Jaccard | Bracket (all 3 legs ≤ 1 tick = 0.10) | Class |
|---|---|---|---|---|---|
| `strat_22_continuation_observed` | 17 | 7 / 7 | **1.000** | 7/7 **1.000** | BOTH |
| `strat_22_reversal_observed` | 6 | 4 / 4 | 1.000 | 1.000 | BOTH |
| `ema_pullback_trend` (SIGNAL rows) | 10 | 9 / 9 | 1.000 | 1.000 | BOTH (n = 9; Ruling 2 stance kept until adequate n) |
| `orb_false_break_fade` (SIGNAL rows) | 5 | 5 / 5 | 1.000 | 1.000 | BOTH (0 asian rows on either side) |
| `impulse_first_pullback_observed` | 4 | 2 / 2 | 1.000 | 1.000 | BOTH |
| `trend_consolidation_break_observed` | 3 | 1 / 1 | 1.000 | 1.000 | BOTH |
| `strat_212` | 3 | 0 (never in replay) | — | — | **LANE_ONLY** (C21) |
| `strat_312` / `strat_322` / `transition` | 0 | 0 | — | — | NOT_TESTABLE at this n |
| `strat_122_pullback`, `strat_4hr_retrigger`, `vwap_*` | 0 | 0 | — | — | absent by lane config |

28 co-fired candidates, **zero** firing or bracket disagreements, no gate failure, no
`MANIFEST_ERROR`. Bar census: 51 lane bars, 25 in the corpus (Polygon lag: the 26 later bars
were not yet served), 0 corpus bars skipped by replay.

**P3-M2K — bar-source parity:** OHLC 24/25 within one tick — one bar (2026-09-16T13:45Z) has
the **open two ticks apart** (live 2908.1 vs Polygon 2908.3; H/L/C identical) → **96.0 %, not a
pass at n = 25**. Levels: `NY_ORB_H/L` **19/19 = 100 %**; every other admitted level is
`NOT_AVAILABLE` on the live side (51 bars of history: no prior day, week, overnight session,
London ORB or zone yet) → counted, not scored. **Verdict: PRELIMINARY — rerun when ≥ 5 sessions
of M2K live history exist.** The single open discrepancy is the same feed-vs-Polygon class seen
on MNQ/MES (8 revised bars there); it does not touch any level built from highs/lows/closes.

**P-R — resolver equivalence (synthetic, seed 17, n = 30,000 over MNQ/MES/M2K ticks):** results
{LOSS 23,324; WIN 4,216; NO_FILL 1,726; OPEN 734}, 3,270 ambiguous fill-bar-target cases, **0
disagreements** on result, exit price and exit bar → **EQUIVALENT**. (Shadow `OPEN` ≡ lane
"still pending"; the lane's `EXPIRED` label at the day roll is the same state.)

## 5. R4 rerun on the #621 fix (MNQ/MES parity corpus, live snapshot; same inputs as v1.4)

Engine `replay/replay_engine.py` sha256 `9ab1f72b…` (was `8de84e13…`); everything else pinned
as before. Determinism on the fixed engine: `MNQ_2026-08-12` twice → 92 rows byte-identical.
P2-X `--integrity-only`: 7,841 rows, 8,029 candidates (was 7,960 — the extra 69 are the
recent-bars families now firing on bars 0–6 of each UTC day), 0 duplicates, forbidden and
lane-only families absent, asian ORB-fade 0, status PASS.

| Family | Jaccard before → after | Bracket before → after | Live-only firings (on warm-up bars) before → after | Replay-only before → after |
|---|---|---|---|---|
| `impulse_first_pullback_observed` | **0.937 → 0.972** | 0.999 → 0.999 | 55 (40) → **16 (1)** | 13 → 15 |
| `trend_consolidation_break_observed` | **0.938 → 0.974** | 1.000 → 1.000 | 21 (15) → **6 (0)** | 5 → 5 |
| `transition_failed_breakdown_reclaim` (NOT_TESTABLE) | 0.101 → 0.102 | 1.000 → 0.900 (9 → 10 pairs, one pair off) | 3 (1) → 2 (0) | 77 → 86 |
| `ema_pullback_trend` | 0.995 → 0.995 | **0.939 → 0.939** (unchanged; Ruling 2 stands) | 3 → 3 | 3 → 3 |
| all `strat_*`, `strat_4hr_retrigger_observed`, `orb_false_break_fade` | identical to v1.4 (0.992–1.000) | identical (0.9996–1.000) | identical | identical |
| `vwap_*`, `range_break_close`, `ovn_*`, `gap_fill` | unchanged (LIVE_ONLY / DEAD) | — | — | — |

Both affected families improved and every other family is numerically identical. The
remaining misses in the two recent-bars families are no longer warm-up bars (1 and 0), i.e.
they are now genuine trend-source (EMA) differences. C19 is closed in the prereg (§15).

## 6. Not done / open

- MGC, MCL, MBT: not added — need a tranche-2 amendment (RTH open ≠ 09:30 ET for MGC/MCL,
  24/7 MBT with a different halt; `polygon_client.front_contract` has no even-month/monthly roll
  schedules). The observation-lane loader and both new tools already accept any root once
  those definitions and corpora exist.
- P3-M2K and P5-M2K are preliminary (n = 25 bars); re-run after ≥ 5 sessions.
- R5 remains **HOLD** (operator). No candidate regenerated on P-REPLAY, no outcome opened.

---

**Verdict: v1.5 AMENDMENT WRITTEN (M2K added to P-REPLAY + P-OOS-PROSPECTIVE; definitions
unchanged); R1-M2K + parity corpus BUILT with clean manifests; P-R EQUIVALENT; P5-M2K 1.000/1.000
and P3-M2K NY-ORB 100 % / OHLC 24-of-25 — both PRELIMINARY at n = 25; R4 RERUN ON #621 CONFIRMS
the two recent-bars families improve (0.937 → 0.972, 0.938 → 0.974) with every other family
identical. R5 still HOLD.**

**Safe next step:** operator review of this PR; then either (a) the tranche-2 amendment for
MGC/MCL/MBT (definitions first), or (b) the R5 go on `data/replay_polygon_v2/{MNQ,MES,M2K}`
with P2-X sealing outcomes — each with its own explicit go.
