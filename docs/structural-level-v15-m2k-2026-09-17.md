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

Code SHA: `03b1a5c` (origin/main with #621, #622, #625 and #626; first draft was written at
`4f07ea0`, repaired after the first audit — §2a, §5 — and again after the follow-up audit,
which held the long stitched historical corpus to #625: §2a item 6, §6). Machine artifacts in this PR:
`…-v15-m2k-2026-09-17-x0-{v2,parity-stitched,parity-z6}.json` (X0 roll proofs),
`…-parity.json` / `…-bar-source-parity.json` (P5-M2K / P3-M2K on the **admitted** `M2KZ6`
corpus; the first-draft runs on the stitched corpus are kept as `…-parity-stitched-nonconfirmatory.json`
/ `…-bar-source-parity-stitched-nonconfirmatory.json`), `…-resolver-equivalence.json` (P-R),
`…-p2-parity-corpus-r4-2026-09-17-results-post621.json` (R4 rerun), manifests
`replay_polygon_v2_M2K_MANIFEST.json`, `replay_polygon_parity_m2k_2026_09_16_M2K_MANIFEST.json`
(stitched, NOT admitted) and `replay_polygon_parity_m2kz6_2026_09_16_M2K_MANIFEST.json` (admitted).

---

## 1. What "adding M2K" means (and what changed in the prereg)

| Layer | MNQ / MES (v1.4) | M2K (v1.5) |
|---|---|---|
| Level / event definitions (§3, §5) | frozen v1.3 | **unchanged** — equity-index micro on the same Globex session map, 18:00 ET day roll, 09:30 ET RTH/NY ORB, 03:00 ET London ORB; tick 0.10 from `config/futures_contracts` |
| P-REPLAY | `data/replay_polygon_v2/{MNQ,MES}` 2024-10-01 → 2026-06-26 | `data/replay_polygon_v2/M2K` built (same window, same pinned builder, quarterly scheduler) but **NOT ADMITTED** — X0 `ROLL_PROVENANCE_UNKNOWN` (§2a); no M2K P-REPLAY population until a roll rule is proven |
| P-LIVE (calibration) | runner journal 2026-07-16 → 09-14T22Z | **none** — no M2K history before the observation epoch |
| P-OOS-PROSPECTIVE | runner journal from 2026-09-17 | cross-instrument observation lane from epoch `2026-09-16T12:17:19Z` |
| Live candidate source | runner `shadow_candidates` | `observe_collection_only_alert` → **same `evaluate_shadow_setups`** (canonical VWAP observers off, Pine advisory brackets stripped) → CANDIDATE (`structural_outcome`, bracket authoritative) + SIGNAL (`signal_metrics`, bracket not authoritative, never resolved by the lane) rows |
| Live resolver | `resolve_shadow_candidate` | lane's `_resolve_one` — a second implementation (**C20**), proven equivalent (§4) |
| Extra families | — | `strat_212`, `strat_122` from `advance_strat_212_122` → **`LANE_ONLY`** (**C21**); `strat_122_pullback`, `strat_4hr_retrigger_observed`, `vwap_*` not configured for M2K |
| Levels parity source | journaled `location_context` (P3) | none journaled (**C22**) → **P3-M2K** = bar-source parity (live bars vs Polygon bars → OHLC + admitted levels) |
| Strata (§7) | instrument ∈ {MNQ, MES} | ∈ {MNQ, MES, M2K} |

Prereg header records all of this as the v1.3 → v1.4 → **v1.5** changelog; `PREREG_VERSION = "1.5"`.

## 2. Corpora (local, gitignored; manifests committed)

| Corpus | Range | Rows / files | Checks | X0 (§2a) |
|---|---|---|---|---|
| `data/replay_polygon_v2/M2K` (R1-M2K, stitched, **NOT ADMITTED** as P-REPLAY) | 2024-10-01T00:00Z → 2026-06-26T20:45Z (warm-up 2024-09-17 →) | **40,878 / 543** (29 fewer bars than MNQ/MES — thin M2K slots, all in the gap ledger) | `ema_200` + `previous_day_*` populated from row 1; all required fields; timestamps strictly increasing; manifest per-file sha256 = disk (543/543); 7 rolls (H5 +26.8 … U6 +20.5); 57 gap runs, every run ≥ 4 h an exchange holiday/early close; manifest `1f1e54f5…` | identity PROVEN 40,878/40,878; 7 scheduler seams, none independently proven → **`ROLL_PROVENANCE_UNKNOWN`**, non-confirmatory |
| `data/replay_polygon_parity_m2k_2026_09_16/M2K` (stitched, first draft) | 2026-09-01T00:00Z → 2026-09-16T18:15Z (`roll_days=3`: U6 → Z6 seam 2026-09-15T00:00Z, +22.1 pts) | 1,058 / 14 | manifest hashes = disk; gap = Labor Day early close only; manifest `78d4127d…` | identity PROVEN; seam **`ROLL_PROVENANCE_UNKNOWN`** → **NOT ADMITTED** |
| `data/replay_polygon_parity_m2kz6_2026_09_16/M2K` (**admitted**, single dated contract) | 2026-09-01T00:00Z → 2026-09-16T18:45Z, `--contract M2KZ6` (fetch 2026-08-18 → 09-17, 1,454 raw bars), **no seam** | 894 / 14 | manifest hashes = disk; `ema_200` + `previous_day_*` populated from row 1; 98 gap runs / 194 missing slots, all 2026-09-01 → 09-11 (Z6 was the back month and traded thinly before the roll — recorded, not fabricated; per §9.5 those days are gap-contaminated for the affected windows); 09-12 → 09-16 complete; manifest `1e3ac8f2…` | identity PROVEN 894/894; `FIXED_DATED_CONTRACT`; live bars identified Z6 27/27 served → **`PROVEN`** |

### 2a. X0 — dated-contract identity and roll-seam provenance (audit repair; #622 §3, #625)

Tool: `scripts/structural_level_x0_roll_proof.py` (read-only; provider GETs only). For every
corpus it (1) re-fetches each manifest segment **as its dated contract** (request form
`GET /futures/v1/aggs/{DATED_TICKER}?resolution=15min&window_start.gte=…&window_start.lt=…`,
the provider's per-row `ticker` recorded) and requires every corpus bar to exist in that
contract's own series; (2) fetches both contracts around each seam for a census; (3) identifies
the dated contract behind each live `bars_M2K` bar (`source_ticker` is the continuous `M2K1!`,
which proves nothing) by nearest OHLC within 4 ticks with the runner-up ≥ 20 ticks away
(U6/Z6 differ by ~200 ticks, so identity is unambiguous; within-one-tick agreement is tallied
separately). Nothing in it moves a seam.

**Historical corpus `replay_polygon_v2/M2K`** (`…-x0-v2.json`):

| Item (operator A1 list) | Result |
|---|---|
| 1. exact dated ticker per segment | `M2KZ4` 2024-10-01→12-11 (4,749 rows) · `M2KH5` →2025-03-12 (5,697) · `M2KM5` →06-11 (5,871) · `M2KU5` →09-10 (5,917) · `M2KZ5` →12-10 (5,885) · `M2KH6` →2026-03-11 (5,746) · `M2KM6` →06-10 (5,933) · `M2KU6` →06-26 (1,080) |
| 2. provider request that establishes identity | each segment re-fetched by dated ticker: **40,878 / 40,878 bars found in their declared contract, 0 missing, 0 OHLCV differences** vs the corpus fetch of 02:19Z (no provider revision in the 40 min between fetches); provider `ticker` on every row = the segment ticker |
| 3. seam timestamp | 7 seams, each at **UTC midnight** of the scheduler date (2024-12-12, 2025-03-13, 06-12, 09-11, 12-11, 2026-03-12, 06-11) — i.e. 2 h into the Globex trading day that opened 22:00Z, so the seam trading day holds 8 bars of the old contract and the rest of the new one (roll-contaminated per prereg §9.5 item 5) |
| 4. old final / new first bar | old last bar 23:45Z, new first bar 00:00Z at every seam; open − prior close = +26.8, +17.6, +17.3, +16.8, +19.0, +16.6, +20.5 pts (the contract spread, not a data hole) |
| 5. overlap / gap / conflict census (±8 days) | both contracts have bars on 985–1,089 common timestamps at every seam; 0 old-only / 0 new-only timestamps; old-contract bars after the seam and new-contract bars before it exist on the provider (not used); **0 corpus bars outside their declared contract; 0 duplicate timestamps** |
| 6. seam represents | **the local scheduler convention only** (`roll_days=8` → Thursday of the week before expiry week, UTC-date granularity). Provider volume crossed to the new contract **3–4 calendar days after every seam** (crossover UTC days 2024-12-15, 2025-03-17, 06-15, 09-15, 12-14, 2026-03-15, 06-14), so the scheduler's seam disagrees with the one independent signal available at every one of the seven seams; no M2K live feed existed in the window, so there is no continuous-feed provenance to reconcile (`feed_reconciliation = NOT_APPLICABLE`). Under #625 the generic quarterly scheduler is only a candidate chain, and exact identity (item 2) proves the bars belong to their declared contracts, not that the declared seam is the correct continuous-series seam. **`roll_provenance = ROLL_PROVENANCE_UNKNOWN`, `admission = NOT_ADMITTED`** (follow-up audit; the first repair had graded this `SCHEDULER_CONVENTION_ONLY` and kept the corpus usable — that grade is withdrawn from the tool and the prereg; the comparison with the MNQ/MES v1.4 convention is not an argument for admission and is dropped). Consequence: no M2K P-REPLAY population is admitted; the corpus and its manifest stay on disk / in git as a non-confirmatory record. The historical seams could be proven later only from an authoritative continuous/roll source (not from this scheduler and not from volume alone); alternatively seam-free per-contract windows (`--contract`) could be built — neither is done here, and no roll rule was changed to make the seams pass |
| 7. September disagreement | not applicable to this window; see the parity corpus |

**September parity corpora** (`…-x0-parity-stitched.json`, `…-x0-parity-z6.json`):

| Item | Stitched (`roll_days=3`) — first draft | Single contract `M2KZ6` — admitted |
|---|---|---|
| 1–2. identity | `M2KU6` 892/892 and `M2KZ6` 166/166 found in their dated contracts, 0 revised | `M2KZ6` 894/894, 0 revised |
| 3–4. seam | 2026-09-15T00:00Z; last U6 bar 09-14T23:45Z close 2895.0 → first Z6 bar 09-15T00:00Z open 2917.1 (+22.1) | **none** |
| 5. census (±3 d) | 268 common timestamps; 168 U6 bars after / 100 Z6 bars before the seam exist; 0 one-sided; 0 wrong-contract; 0 duplicates | n/a |
| 6. seam represents | scheduler convention. Provider volume crossover U6→Z6 = **2026-09-13** (2 days before the seam) | no seam: identity is exact by construction (#586's "one stable dated contract" case) |
| 7. September disagreement | **Correction 2026-09-18:** MNQ/MES were observed as U6 before an intraday evidence gap and as Z6 from **2026-09-14T22:00Z** onward; the actual U6→Z6 switch boundary is **NOT_OBSERVABLE** because both 15m and 5m streams are discontinuous across that transition. The older statement that the switch itself was observed at 22:00Z is superseded. The `roll_days=3` seam is 2 h later; the `roll_days=8` seam would have been 09-10. **M2K's own switch was never observed**: the first M2K live bar on the box is 2026-09-16T12:15Z and there is no earlier M2K payload — the MNQ/MES observation is not M2K evidence. Live bars: 27 served by the provider identified as **`M2KZ6` 27/27** (26/27 within one tick; one open 2 ticks off), 24 not yet served, 0 U6, 0 ambiguous; `feed_switch_observed = False` → seam **`NOT_OBSERVABLE`** → **`ROLL_PROVENANCE_UNKNOWN`**. Consequence (operator rule): the stitched corpus is **not admitted**; its P3-M2K / P5-M2K runs are **non-confirmatory** and kept only as `…-stitched-nonconfirmatory.json`. Its warm-up state for 09-16 (previous-day levels from the 09-15 trading day, EMAs) mixes 8 U6 bars that the live feed may or may not have seen | Live feed identified as `M2KZ6` on every served bar in the compared window; the roll is outside the window, not re-ruled. **`PROVEN`**. Cost: Z6 traded thinly before 09-12, so 194 missing slots on 09-01 → 09-11 sit in the warm-up/early window (gap-contaminated windows excluded per §9.5) |

No new roll rule was invented; the `--contract` builder option only pins one dated contract.

## 3. Tooling added (all read-only; 25 tests in `tests/test_structural_level_p2.py`, full suite green)

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
- `scripts/structural_level_x0_roll_proof.py` (X0): dated-contract identity, seam census, live-feed
  contract identification (§2a); `scripts/structural_level_corpus_build.py --contract <TICKER>`:
  single fixed dated contract, no seam (manifest `roll_rule = fixed dated contract …`).

## 4. M2K evidence (preliminary — 27 co-evaluated bars, 2026-09-16 12:15 → 18:45Z, on the admitted `M2KZ6` corpus)

Rerun after X0 on `data/replay_polygon_parity_m2kz6_2026_09_16/M2K` (replay engine `9ab1f72b…`,
#621 included). The first-draft numbers on the stitched corpus (25 bars, identical family
counts, Jaccard/bracket 1.000, NY ORB 19/19, OHLC 24/25) are non-confirmatory (§2a) and are not
cited as evidence below.

**P5-M2K — observation lane vs replay, frozen §4 gates:**

| Family | Live firings (51 lane bars) | Replay firings (co-evaluated, 27 bars) | Jaccard | Bracket (all 3 legs ≤ 1 tick = 0.10) | Class |
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
`MANIFEST_ERROR`. Bar census: 51 lane bars, 27 in the corpus (Polygon lag: the 24 later bars
were not yet served), 0 corpus bars skipped by replay.

**P3-M2K — bar-source parity:** OHLC 26/27 within one tick — one bar (2026-09-16T13:45Z) has
the **open two ticks apart** (live 2908.1 vs Polygon 2908.3; H/L/C identical) → **96.3 %, not a
pass at n = 27**. Levels: `NY_ORB_H/L` **21/21 = 100 %**; every other admitted level is
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

Stated precisely (an earlier draft of this section said "every other family is numerically
identical" — that was not literally true and is withdrawn): the two intended recent-bars
families improved materially; every previously admitted / testable family (`strat_*`,
`strat_4hr_retrigger_observed`, `orb_false_break_fade`) is numerically identical and keeps
its prior classification; `ema_pullback_trend` is unchanged and **remains bracket-conflicted**
(Ruling 2); `transition_failed_breakdown_reclaim` **changed numerically** (Jaccard 0.101 →
0.102, bracket 1.000 → 0.900 on 9 → 10 pairs, live-only 3 → 2, replay-only 77 → 86) and
**remains `NOT_TESTABLE`** — the change is visible in the table above and is not normalised
away. The remaining misses in the two recent-bars families are no longer warm-up bars (1 and
0), i.e. they are now genuine trend-source (EMA) differences. C19 is closed in the prereg (§15).

## 6. Not done / open

- MGC, MCL, MBT: not added — tranche-2 prereg/spec is a separate PR (definitions + X0 source/roll
  proof first; `polygon_client.front_contract` has no even-month/monthly roll schedules).
- **M2K has no admitted P-REPLAY population.** `data/replay_polygon_v2/M2K` is
  `ROLL_PROVENANCE_UNKNOWN` (§2a item 6) and non-confirmatory; the only admitted M2K corpus is
  the seam-free `M2KZ6` September window. Admitting an M2K historical population needs either an
  independent proof of each historical seam from an authoritative continuous/roll source or
  seam-free per-dated-contract windows built with `--contract` under their own prereg step —
  both require a separate go. Any future R5 go on P-REPLAY therefore covers `{MNQ, MES}` only.
- P3-M2K and P5-M2K are preliminary (n = 27 bars); re-run after ≥ 5 sessions on a corpus whose
  X0 is `PROVEN` (a Z6-only window stays seam-free until the December roll).
- The stitched September corpus stays on disk with its manifest committed as a record of the
  non-admitted first draft; it must not be used for M2K parity.
- R5 remains **HOLD** (operator). No candidate regenerated on P-REPLAY, no outcome opened.

---

**Verdict: v1.5 AMENDMENT WRITTEN (M2K added to P-OOS-PROSPECTIVE; M2K P-REPLAY population
NOT ADMITTED; definitions unchanged; C23 added). X0: R1-M2K identity PROVEN 40,878/40,878 but its
seven scheduler seams are not independently proven → `ROLL_PROVENANCE_UNKNOWN`, non-confirmatory
(#625); stitched September corpus `ROLL_PROVENANCE_UNKNOWN` → NOT ADMITTED; single-contract
`M2KZ6` corpus `PROVEN` (live feed identified Z6 27/27) — the only admitted M2K corpus. P-R EQUIVALENT.
On the admitted corpus: P5-M2K 1.000/1.000 (28 co-fired) and P3-M2K NY-ORB 21/21, OHLC 26/27 —
both PRELIMINARY at n = 27. R4 RERUN ON #621: the two recent-bars families improve (0.937 →
0.972, 0.938 → 0.974); admitted/testable families identical; `ema_pullback_trend` still
bracket-conflicted; `transition_failed_breakdown_reclaim` changed numerically and remains
NOT_TESTABLE. R5 still HOLD.**

**Safe next step:** operator review of this PR; the tranche-2 definitions/X0 PR for MGC/MCL/MBT
is separate; the R5 go on `data/replay_polygon_v2/{MNQ,MES}` needs its own explicit go (M2K
has no admitted P-REPLAY population).
