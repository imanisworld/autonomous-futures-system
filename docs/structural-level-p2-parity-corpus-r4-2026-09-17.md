# Structural-Level Prereg — P2 R1–R4: Corpus Rebuilds, Read-Only Tooling, Parity-Corpus Family Verdicts (2026-09-17)

**Mode:** RESEARCH ONLY / PAPER ONLY. No runtime, deployment, strategy, risk, config, `.env`,
Pine, collector or gate change. Box untouched. **No outcome was read** — every `outcome`
block is split off at parse time by the tooling and discarded (parity) or never written
(`--integrity-only`). No candidate regeneration on P-REPLAY was run (R5 not started).

**Authorization:** operator verdict "APPROVE R1 + R2 corpus rebuilds and R3 read-only parity
extraction … Report, per family … Then stop before any candidate regeneration/outcome run."
Prereg v1.3 (`df18532`), P2 spec (#618 → `b7bade8`). Code SHA for this run: `b7bade8`
(pinned file hashes unchanged from the spec: `shadow_setups` `4e535d70…`, `shadow_resolver`
`d200ae0d…`, `replay_engine` `8de84e13…`, `polygon_to_replay` `a42e1cae…`, `csv_to_replay`
`332e48a9…`, `risk_rules.yaml` `6c7e3f13…`).

**Rulings applied (2026-09-17, second pass on this PR, before R5 / any outcome):** Ruling 1 —
P-REPLAY window amended to **2024-10-01 → 2026-06-26** (option A; prereg **v1.4**); R1 rebuilt
under it (§3.1). Ruling 2 — **`ema_pullback_trend` fails pooled parity**: `REPLAY_ONLY` +
`LIVE_ONLY` strata, never pooled; the `BRACKET_CONFLICT` finding is preserved unchanged (§4.2).

Machine artifacts (this PR): `docs/structural-level-p2-parity-corpus-r4-2026-09-17-results.json`
(P2-P report), `…-integrity.json` (P2-X integrity-only manifest of the parity run, determinism
check, old-vs-rebuilt corpus OHLCV comparison), and the four corpus `MANIFEST.json` copies under
`docs/structural-level-corpus-manifests/`. Every number below is copied from those files.

---

## 1. What was built (R3 — read-only tooling, tests)

| Tool | Purpose | Imports from runtime? |
|---|---|---|
| `scripts/structural_level_corpus_build.py` (`slc-build-v1.4`) | R1/R2 corpus build: calls the pinned `polygon_to_replay.derive_candles` + the same day-file split as `polygon_to_replay.main`; adds `--roll-days`, `--end-ts-exclusive` (roll cut), a fail-closed schema check (`london_orb_*` etc. must be present) and `MANIFEST.json` (git HEAD, builder file hashes, contract segments + roll rule, per-file sha256/rows/first/last, 15m-grid gap ledger inside CME hours, roll ledger with the session-open gap at every seam) | no (Polygon client + builder only) |
| `research/structural_level_p2.py` (`slp2-v1.4`) | Library: journal loaders (live = 15m decision rows, `context.timestamp` as bar time; replay = `bar_ts`), `split_outcome`, `candidate_key` = `shadow_resolver._candidate_key("shadow_setups", …)` byte-identical, `compute_parity` (spec §4 gates frozen: Jaccard ≥ 0.90; each bracket leg within one tick on ≥ 98 % of co-fired), `extract` (P2-X), `determinism_check` | no |
| `scripts/structural_level_p2_extract.py` (P2-X) | replay journals → `candidates.jsonl` (no outcome field) + `outcomes.sealed.jsonl` (hashed, not opened) + `manifest.json` with the §3.3 integrity report; `--integrity-only` never writes outcomes; `--determinism-log-dir` byte-compares two runs | no |
| `scripts/structural_level_p2_parity.py` (P2-P) | live vs replay firing/bracket parity per family with the operator's six-column report and final classification | no |
| `tests/test_structural_level_p2.py` | 16 synthetic tests: key identity vs resolver, outcome seal (no `outcome`/`result` string anywhere in candidates), integrity-only never writes outcomes, exact/conflicting duplicates → BLOCKED, asian ORB-fade and forbidden-family fail-closed, roll-cut exclusivity, Jaccard/bracket/bar-census arithmetic, BRACKET_CONFLICT when firing agrees but a leg is two ticks off, classifier states, LIVE_ONLY-in-replay gate failure, live loader (`context.timestamp`, roll cut), determinism, ORB NOT_AVAILABLE days, corpus build with a fake client (warm-up drop, end cut, required fields, roll ledger reproduces a 277.5 gap, manifest hashes), gap ledger, no-runtime-import guard | — |

Full suite in the worktree: **5,685 passed, 7 skipped**.

## 2. R2 — parity corpus `data/replay_polygon_parity_2026_07_16_09_14/{MNQ,MES}` (built)

| | MNQ | MES |
|---|---|---|
| Contract | `MNQU6` only (`roll_days=3`, single segment 2026-07-06 → 09-14) | `MESU6` only |
| Range | 2026-07-16T00:00Z → 2026-09-14T20:45Z; 8 candles dropped at the 22:00Z roll cut | same |
| Rows / files | 3,921 / 52 | 3,920 / 52 |
| Gap ledger (CME hours) | 09-07 17:00–20:45Z (240 min, Labor Day early close); 09-11 17:00–18:30Z (105 min) + 20:00–20:45Z (60 min) (the known Polygon hole) | 09-07 (240); 09-11 (120 + 60) |
| ORB availability (from corpus) | 43 NY session-days, **0** NOT_AVAILABLE; 43 London, **0** NOT_AVAILABLE | same |
| Manifest sha256 | `939725d7…` | `46e99ea5…` |

## 3. R1 — P-REPLAY corpus `data/replay_polygon_v2/{MNQ,MES}` (first build: window shortfall → Ruling 1 → rebuilt, §3.1)

**First build (superseded, kept for the record):** pinned builder, `roll_days=8`, requested
2024-07-01 → 2026-06-26 with the builder's 10-day warm-up → 41,773 rows / 555 day files per
instrument, 2024-09-17T13:30Z → 2026-06-26T20:45Z, schema check passed. Those files and their
manifests (sha256 MNQ `9e60b4c0…`, MES `a9826475…`) were replaced by the §3.1 rebuild.

**Finding R1-A — Polygon retention.** Polygon returns **zero bars for the U4 contracts**
(`MNQU4`, `MESU4`, probed directly) and `MNQZ4` data begins 2024-09-17T00:00Z — exactly two
years before today's fetch. The provider's futures history is a rolling ~2-year window. The
preregistered P-REPLAY start (2024-07-01, prereg §2) **cannot be rebuilt from the provider
today**; the rebuilt corpus starts 2024-09-17 and has **no warm-up** (the warm-up days were also
outside retention: the first day file holds only the 38 bars from the first NY ORB onward,
and `ema_200` is `None` for the first 145 bars, through 2024-09-19). The `MNQU4 → MNQZ4` seam is `NOT_IN_CORPUS`; the remaining seven
rolls are in the ledger and **the known 2026-06-11 `+277.5` MNQ gap is reproduced** (MES
`+61.25`; others 214.75 → 268.75 MNQ / 50.75 → 66.5 MES).

**Finding R1-B — the preserved June-2026 fetch is not byte-stable with today's.** On the
2024-09-17 → 2026-06-26 overlap (41,635 common bars per instrument), the old
`data/replay_polygon/*` OHLCV differs on **8 bars** (e.g. 2025-02-13T19:45Z MNQ high 22050.0 →
22075.5, volume 2,835 → 22,951 — provider revisions) and the new fetch carries **138 bars the
old one lacked** (2025-02-20T21:15Z onward — backfilled). The old files hold **5,105 bars per
instrument for 2024-07-01 → 2024-09-17T13:15Z** that no longer exist at the provider. All
bars are listed in `…-integrity.json` → `old_vs_rebuilt_corpus_ohlcv`.

**Options (operator decision, not taken here):**
- **(A) Amend prereg §2**: P-REPLAY = 2024-10-01 → 2026-06-26 (rebuild with `--start 2024-10-01`
  so the 09-17 → 09-30 bars serve as warm-up). Loses ~3 months (~11 % of the in-sample window);
  everything stays single-source and reproducible from the provider.
- **(B) Hybrid raw input**: feed the preserved June-2026 OHLCV for 2024-07-01T13:30Z → 2024-09-16
  (from `data/replay_polygon/*`, same provider, verified identical on the overlap except the 8
  revised bars; the preserved files themselves start at the first NY ORB of 2024-07-01, their
  warm-up bars were never kept) into the pinned `derive_candles` together with today's fetch, and
  record that provenance in the manifest. Preserves the preregistered window except ~2 days of
  EMA-200 warm-up at the very start; the derived fields are still produced by the pinned builder;
  the raw bars for that stretch are no longer re-fetchable, so reproducibility rests on the
  preserved files' hashes.

**Ruling 1 (operator, 2026-09-17): option A.** Single provider vintage, freshly reproducible,
no data-vintage seam; the ~11 % shorter in-sample window is accepted for documented
provider-retention reasons before any outcome exists. Option B rejected (it would mix the
June-2026 and September-2026 vintages that already differ by 8 revised + 138 backfilled bars).
Recorded as prereg **v1.4** (§2.4 P-REPLAY row + changelog) and in the P2 spec §3.1/§5.

### 3.1 R1 rebuilt under Ruling 1 (prereg v1.4 window)

`scripts/structural_level_corpus_build.py --symbol <INST> --start 2024-10-01 --end 2026-06-26
--warmup-days 14 --out data/replay_polygon_v2 --fresh` (warm-up fetch from 2024-09-17 = the
provider's retention start; `--fresh` replaces the superseded build only after a successful
fetch + derivation). Builder `slc-build-v1.4`, pinned file hashes unchanged
(`polygon_to_replay` `a42e1cae…`, `csv_to_replay` `332e48a9…`, `pine_market_condition`
`df04b39e…`, `polygon_client` `3959cc1b…`, `context/trend` `24100349…`).

| | MNQ | MES |
|---|---|---|
| Raw bars fetched (with warm-up) | 41,827 (identical count to the first fetch 15 min earlier) | 41,827 |
| Rows / day files in window | **40,907 / 543** | **40,907 / 543** |
| Range | **2024-10-01T00:00Z → 2026-06-26T20:45Z** | same |
| Warm-up check | `ema_200` populated on row 1 (20232.03), `previous_day_*` populated on row 1; **0 rows** with `ema_200` or `previous_day_high` = `None` | row 1 `ema_200` 5794.71; 0 `None` rows |
| Required fields (`london_orb_*`, `reconstructed_market_condition`, `legacy_market_condition`, …) | present on every row | same |
| Timestamps | strictly increasing, no duplicates | same |
| Manifest per-file sha256 vs disk | 543/543 match | 543/543 match |
| Contract segments | Z4 (2024-09-17 warm-up) → H5 12-12 → M5 03-13 → U5 06-12 → Z5 09-11 → H6 12-11 → M6 03-12 → U6 06-11 | same |
| Roll ledger (session-open gap, pts) | H5 +268.75, M5 +214.75, U5 +225.5, Z5 +230.75, H6 +258.0, M6 +217.0, **U6 +277.5 (the known 2026-06-11 gap reproduced)** | +66.5, +52.0, +53.0, +54.25, +58.75, +50.75, +61.25 |
| Gap ledger (CME hours) | 35 runs, 12,795 min; every run ≥ 4 h is an exchange holiday/early close (Thanksgiving ×2, Christmas Eve/NYE ×2, MLK, Presidents, Good Friday 2025/2026, Memorial, Juneteenth, July 4, Labor Day, 2025-01-09 national day of mourning) plus one 645-min hole 2025-11-28T02:45Z (Black Friday) — listed in the manifest | identical runs |
| Manifest sha256 | `1f16b81b…` | `ca448150…` |

Old (retired `data/replay_polygon`, June-2026 fetch) vs rebuilt on the 2024-10-01 → 2026-06-26
overlap: 40,769 common bars per instrument, the same **8** provider-revised bars and **138**
backfilled bars as before (all listed in `…-integrity.json`); 5,971 old-only bars =
2024-07-01 → 2024-09-30 (outside the v1.4 window). **R1 proof complete; nothing downstream run.**

## 4. R4 — parity run on R2 (replay engine, pinned) and P2-P vs the live snapshot

**Replay run:** `python3 scripts/run_replay_batch.py --candles <parity>/<INST> --log-dir <scratch> --fresh`
from a worktree at `b7bade8` with **no `.env`** (yaml config only: `schedule.mode: current`,
`enabled_concepts: [orb_breakout]`, `htf_direction_source: payload`, `expected_timeframe_minutes: 15`),
`FORWARD_EVIDENCE_CAMPAIGN` unset. Logs stay in the scratchpad (not committed; they contain
inline shadow outcomes, which were never read). Both instruments, 52 day files each, no errors.

**P2-X `--integrity-only` on that run** (no outcomes written): 7,841 decision rows, 7,960
candidates, 7,960 unique keys, **0 exact duplicates, 0 conflicting duplicates**,
`orb_false_break_fade` asian rows **= 0** (london 199, new_york 132), forbidden families
(`ovn_*`, `gap_fill`, `vwap_*`, `range_break_close`) **absent**, every corpus bar evaluated
(bars in corpus not evaluated by replay = 0 for both instruments — the DecisionEngine never
held a position, so no bar was skipped), status **PASS**.

**Determinism (spec §3.3):** `MNQ_2026-08-12.jsonl` run twice → 92 rows, **byte-identical**
`shadow_candidates` (outcome bytes included in the hash, not read).

**Bar census (live 15m decision rows 07-16 → 09-14T22:00Z exclusive, `context.timestamp`;
replay `bar_ts`):**

| | live bars | replay bars | both evaluated | live-only | replay-only (live feed gap) |
|---|---|---|---|---|---|
| MNQ | 3,629 | 3,921 | **3,617** | 12 (all absent from the corpus: 09-11 Polygon hole ×11, 07-15 ×1) | 304 — 07-16 (35), 07-17 (9), 07-21 (32), 07-22 (92), 07-23 (55), 07-28 (42), 08-13 (3), 08-14 (16), 09-02 (2), 09-03 (1), 09-14 (17) |
| MES | 3,640 | 3,920 | **3,628** | 12 (09-11 hole) | 292 — same dates (07-16: 22; 08-04: 1; 08-14: 17; 09-14: 18) |

Live rows = 7,271 (same population as P3); 2 duplicate live bars, first-seen-wins; **62 live
asian `orb_false_break_fade` rows excluded as `STALE_ORB_CONTAMINATED`** (spec §6). Firing
sets are compared on the both-evaluated bars only; feed-gap bars are counted, not scored.

### 4.1 Per-family report (the operator's six columns; gates: Jaccard ≥ 0.90, bracket one tick ≥ 98 %)

| Family | Live firings (all live bars) | Replay firings (all replay bars) | Live / replay on both-evaluated bars | Overlap ∩ | Firing Jaccard | Bracket: pairs · all-3-within-1-tick · entry/stop/target | Live-only / replay-only firings (of which on replay day-file warm-up bars) | Input-source mismatch reason | **Final classification** |
|---|---|---|---|---|---|---|---|---|---|
| `strat_22_continuation_observed` | 2,536 | 2,751 | 2,530 / 2,527 | 2,527 | **0.9988** | 2,527 · **0.9996** · 0.9996/0.9996/0.9996 | 3 (1) / 0 | bar-type source (Pine `classify_bar` vs corpus `classify_htf_bar`) — measured: negligible | **BOTH** |
| `strat_22_reversal_observed` | 1,222 | 1,304 | 1,220 / 1,218 | 1,218 | **0.9984** | 1,218 · **1.000** | 2 (0) / 0 | bar-type source — negligible | **BOTH** |
| `strat_312_observed` | 131 | 141 | 130 / 131 | 130 | **0.9924** | 130 · **1.000** | 0 / 1 | bar-type source — negligible | **BOTH** |
| `strat_322_reversal_observed` | 147 | 160 | 147 / 147 | 147 | **1.000** | 147 · **1.000** | 0 / 0 | — | **BOTH** |
| `strat_122_observed` | 156 | 163 | 154 / 155 | 154 | **0.9935** | 154 · **1.000** | 0 / 1 | bar-type source — negligible | **BOTH** |
| `strat_122_pullback` | 74 | 79 | 74 / 74 | 74 | **1.000** | 74 · **1.000** | 0 / 0 | — (MES cell n = 7 stays `NOT_TESTABLE` on P-LIVE per spec §6) | **BOTH** |
| `strat_4hr_retrigger_observed` | 44 | 47 | 44 / 44 | 44 | **1.000** | 44 · **1.000** | 0 / 0 | EMA + avg-volume source — measured: none at this n | **BOTH** (was "input-divergent" in the matrix; resolved) |
| `orb_false_break_fade` | 301 (after excluding 62 stale asian) | 331 | 299 / 298 | 298 | **0.9967** | 298 · **1.000** | 1 (0) / 0 | ORB source (Pine vs corpus NY/London ORB) — measured: none; replay asian = 0 by construction | **BOTH** (corpus rebuild confirmed London rows) |
| `ema_pullback_trend` | 1,279 | 1,349 | 1,278 / 1,278 | 1,275 | **0.9953** | 1,275 · **0.9388** · entry 0.9945 / stop 0.9992 / **target 0.9388** | 3 (0) / 3 (0) | EMA source (Pine vs SMA-seeded corpus EMA) and feed close: `entry = close`, `stop = f(EMA21)`, `target = entry ± 2.2 × risk` | **BOTH — BRACKET_CONFLICT** (reported, not tolerated; see §4.2) |
| `impulse_first_pullback_observed` | 1,077 | 1,109 | 1,074 / 1,032 | 1,019 | **0.9374** | 1,019 · **0.999** · 1.0/0.999/0.999 | 55 (**40** on warm-up) / 13 (2) | trend source (EMA) + **replay clears the 8-bar research deque at every day-file boundary** (`replay_engine.run` → `_research_bars.clear()`), live's BarHistory spans days | **BOTH** (gate passes; 73 % of live-only misses are the replay warm-up, a replay-side artefact of UTC day files, not a definition difference) |
| `trend_consolidation_break_observed` | 414 | 434 | 411 / 395 | 390 | **0.9375** | 390 · **1.000** | 21 (**15** warm-up) / 5 (1) | same as above | **BOTH** (same caveat) |
| `transition_failed_breakdown_reclaim` | 12 | 92 | 12 / 86 | 9 | 0.1011 | 9 · 1.000 | 3 (1) / **77** | regime label source: live Pine `market_condition` (MES top-level label `None` since 07-28, C8) vs corpus `reconstructed_market_condition` — replay fires 7× more | **NOT_TESTABLE** (spec §6; the 77 replay-only firings quantify C8/regime-source divergence — do not pool) |
| `vwap_hold_observed` | 155 (MNQ, NY) | 0 | 155 / 0 | 0 | 0.0 | — | 155 / 0 | `FORWARD_EVIDENCE_CAMPAIGN` unset in replay (by manifest); C14 blocks promotion | **LIVE_ONLY** |
| `vwap_rejection_observed` | 0 | 0 | — | — | — | — | — | as above | **LIVE_ONLY** (never fired live either) |
| `range_break_close` | 0 (not a `shadow_candidates` lane) | 0 | — | — | — | — | — | live-only `range_signal` lane | **LIVE_ONLY** |
| `ovn_high_sweep_reclaim`, `ovn_low_sweep_reclaim`, `gap_fill` | 0 | 0 | — | — | — | — | — | payload never carries `overnight_high/low` / `rth_open` | **DEAD** |

Gate failures reported by the tool: **one** — `ema_pullback_trend: BOTH — BRACKET_CONFLICT`.
No `MANIFEST_ERROR` (no DEAD family fired; no LIVE_ONLY family appeared in replay).

### 4.2 `ema_pullback_trend` bracket conflict — decomposition (no tolerance change)

Of 1,275 co-fired pairs, 78 have the **target** more than one tick apart (entry and stop pass
at 99.45 % / 99.92 %). Decomposition (computed from the same candidate rows, in
`…-results.json` examples and re-derived for this table):

| Cause | Pairs |
|---|---|
| entry and stop both within one tick, target off — i.e. `target = entry ± 2.2 × (entry − stop)` **amplifies** a one-tick close difference (live Pine close vs Polygon close) and sub-tick EMA-21 differences to ≥ 2.2 ticks | **70** |
| entry itself 2–3 ticks apart (feed close differs by more than one tick on that bar) | 8 |
| stop beyond one tick | 0 |

Largest target difference: 2.27 pts (MNQ 2026-08-27T10:00Z; entries 29598.25 vs 29599.0). This
is an **input miss** (feed close + EMA source), exactly the case §4 anticipated ("the formula is
the same function, so a bracket miss means an input miss"). It is reported, not tolerated: the
family fails the frozen bracket gate as written.

**Ruling 2 (operator, 2026-09-17): `ema_pullback_trend` fails pooled parity** → analysed as
`REPLAY_ONLY` + `LIVE_ONLY` strata, **never** in the pooled live/replay confirmatory statistic.
No tolerance widening, no change to the 2.2× target, no rounding change, no row removal, and
93.9 % is not "close enough". The P2-P tool keeps reporting the raw `BOTH — BRACKET_CONFLICT`
classification and now attaches the ruled disposition beside it (`ruled_disposition`,
`RULED_SPLIT_STRATA` in `research/structural_level_p2.py`); recorded in prereg v1.4 §2.3 and
the P2 spec §2 matrix.

### 4.3 Resolution of the spec §2 "BOTH — input-divergent" rows

- `strat_4hr_retrigger_observed`, `impulse_first_pullback_observed`, `trend_consolidation_break_observed`
  → **BOTH** (Jaccard 1.000 / 0.937 / 0.938, bracket 1.000 / 0.999 / 1.000).
- `ema_pullback_trend` → firing **BOTH** (0.995) but **bracket gate fails** → `BOTH — BRACKET_CONFLICT`
  → **Ruling 2: `REPLAY_ONLY` + `LIVE_ONLY` strata, not pooled.**
- All `strat_*` families and `orb_false_break_fade` → **BOTH** as predicted; bar-type and ORB
  routing sources are effectively identical on this window.

### 4.4 Post-#621 rerun (2026-09-17, later the same day)

The replay day-file history defect (C19) was fixed offline by the operator in PR #621
(`4f07ea0`). R4 was rerun on the fixed engine with identical inputs:
`impulse_first_pullback_observed` 0.937 → **0.972**, `trend_consolidation_break_observed`
0.938 → **0.974**, every other family numerically identical, `ema_pullback_trend` still
`BRACKET_CONFLICT` (Ruling 2). Full before/after table and artifacts:
`docs/structural-level-v15-m2k-2026-09-17.md` §5 and
`docs/structural-level-p2-parity-corpus-r4-2026-09-17-results-post621.json`.

## 5. Conflicts / findings to carry forward

- **C17 — Polygon rolling retention (R1-A):** P-REPLAY as preregistered in v1.0–v1.3 is no
  longer fetchable before 2024-09-17; a rebuilt corpus fetched on date D starts at ≈ D − 2 years.
  **Resolved by Ruling 1 (v1.4 window 2024-10-01 →).** Any reproduction after ~2026-10-01 will
  again lose the start of the window from the provider and must rely on the preserved files +
  manifest hashes (the retention edge moves one day per day).
- **C18 — provider revisions (R1-B):** 8 revised bars + 138 backfilled bars between the June-2026
  and September-2026 fetches of the same contracts. The manifests pin *this* fetch.
- **C19 — replay research-deque reset at UTC day-file boundaries:** the first 7 bars of every
  replay day file (00:00–01:30Z, asian) see fewer than 8 recent bars; accounts for 58 of the 94
  firing misses across the two recent-bars families. Replay-side only; live is unaffected. A fix would
  be a replay-engine change → post-09-30 queue, not now.
- C8 quantified: `transition_failed_breakdown_reclaim` replay fires 86 vs live 12 on the same
  bars (regime-label source).
- Live feed gaps in P-LIVE (replay-only bars): 07-16, 07-17, 07-21, **07-22 (full day)**, 07-23,
  07-28, 08-13/14, 09-02/03, 09-14 — consistent with the P3 gap ledger.

## 6. What was not done

No candidate regeneration on P-REPLAY (R5), no P2-X full run (no `outcomes.sealed.jsonl` was
ever written on real data), no feature table (R6), no spot-check (R7), no outcome read, no
threshold chosen beyond §4's frozen gates, no family reweighting, no runtime/config change.
The replay logs of the parity run remain in the session scratchpad and are not part of the
repo.

---

**Verdict: R1 REBUILT under Ruling 1 (prereg v1.4 window 2024-10-01 → 2026-06-26; 40,907 rows
× 2, warm-up complete, manifests + integrity clean); R2 BUILT; R3 DELIVERED (16 tests, suite
green); R4 PARITY: 10 families BOTH (pooled-eligible), `ema_pullback_trend` BRACKET_CONFLICT →
split strata by Ruling 2, 1 NOT_TESTABLE, 3 LIVE_ONLY, 3 DEAD. P2 remains UNPROVEN (no
candidate regenerated on P-REPLAY, no outcome read). STOP before R5.**

**Safe next step:** operator review of the rebuilt-R1 proof (§3.1) and the v1.4 amendment;
then, with a separate go, R5 = regeneration on `data/replay_polygon_v2` (v1.4) with P2-X
sealing outcomes, followed by R6 (feature table) and R7 (spot-check) before any seal is opened.
