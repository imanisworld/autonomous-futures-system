# Structural-Level Prereg — P1 feature builder + P3 levels-only parity (2026-09-16/17)

**Status:** RESEARCH RECORD ONLY (research module, read-only tooling, docs). No runtime,
collector, replay, strategy, risk, gate or deployment change. **No outcome was read, no
candidate was scored, no replay was run, no threshold was tuned.** Prereg under execution:
`docs/prereg-dynamic-structural-level-attribution-2026-09-16.md` — v1.2 frozen at `fdeac72`,
**amended to v1.3 on this branch** on the basis of the first (v1.2-rule) P3 run and the
operator rulings of 2026-09-17 (see §3).
**Verdict: P1 DONE · P3 PASS under v1.3 — every admitted level reproduces its journaled copy at
100% within one tick on eligible rows; VWAP `NOT_ADMITTED` (diagnostic 94.2% eligible / 89.0%
all rows); Pine previous-day fields demoted to a separate construct.**
Machine-readable results: `docs/structural-level-parity-p3-2026-09-16-results.json`
(tool `scripts/structural_level_parity_check.py`, `slf-parity-v1.3`, generated
2026-09-17T00:47:59Z). Every number below is copied from that file.

---

## 1. P1 — `research/structural_level_features.py`

Pure offline feature builder (levels §3, `LC_ZONE` §4.1 via the *same* `location_context`
functions the live collector uses, events §5, reduction rule §5.1, hypothesis labels §6).
No I/O; imports only `context.location_context` pure helpers and the contract tick table
(asserted by a test). Frozen constants: τ = 0.25, prox = 0.5, K = 4, R = 8, N = 2,
D_max = 1.0 (× MTR15), live bar window 960. v1.3: VWAP is computed and returned as a
diagnostic (`exploratory=True`, in `DIAGNOSTIC_LEVELS`) but can never be an anchor, a cluster
member or a hypothesis level; the previous-day close is `PDC_BAR`.

23 synthetic-bar tests in `tests/test_structural_level_features.py` (one per level rule and
per event), including: missing 09:30 / 03:00 bar → ORB `NOT_AVAILABLE` even when a 09:45 bar
exists; VWAP does not reset at sub-session boundaries; `LC_ZONE` and MTR15 byte-match
`build_location_context` on the same window; bars after `B0` are ignored; break → retest
states incl. `RETESTED_EARLIER` / `IMMEDIATE` / `BACK_THROUGH_NO_RETEST`; anchor
tie-precedence and own-level exclusion; H5 relevance band; H2 age-matched T/F; VWAP never
enters an anchor or cluster even when it is the nearest level. Full suite passes (see PR).

Two things the module adds beyond the prereg text, both fail-closed and reported, not
silent: per-level `gap_minutes` / `gap_contaminated` (§9.4, ≥ 45 min inside the formation
window) and a `BACK_THROUGH_NO_RETEST` E5 state (break held through `B−1`, `B0` closes
back through without touching τ) which is neither T nor F for H2.

## 2. P3 — levels-only parity against the frozen journal snapshot (v1.3 rule)

Snapshot: VPS `logs/` read-only copy taken 2026-09-16 22:38Z (the closed study's). Window:
15m MNQ/MES decision rows with `context.timestamp` in **2026-07-16 → 2026-09-14T22:00Z
(exclusive; 22:00Z is the first Z6 bar)** = **7,271 rows** (MNQ 3,629 / MES 3,642); 892
5m-path rows and 368 out-of-window rows skipped and counted; 0 build failures; 0 rows whose
`B0` bar was absent from bar history. Reconstruction window per row = **exactly** the
runner's `BarHistory.recent(inst, 960, for_date=today, lookback_days=14)`: day files
`for_date−13 … for_date` (processing dates), bars with ts ≤ `B0`, last 960. Tolerance one
tick (0.25) for every level; pass ≥ 98% on **eligible** rows — rows already
`context_gap_contaminated` for that level under §9.4 are excluded from the denominator,
counted, and reported with their own agreement rate.

| Admitted level | eligible | agree | §9.4-excluded (of which agree) | all-rows % | verdict |
|---|---:|---:|---:|---:|---|
| PDH / PDL / `PDC_BAR` vs `location_context.levels` | 6,072 | 100.0% | 1,127 (1,127) | 100.0% | **PASS** |
| ONH / ONL vs `location_context.levels` (rows carrying one) | 4,967 | 100.0% | 370 (370) | 100.0% | **PASS** |
| NY ORB high / low vs `context.orb` (NY rows, canonical 09:30 bar present) | 2,150 | 100.0% | 0 | 100.0% | **PASS** — 79 session-days; 3 `NOT_AVAILABLE` days excluded (§14 P3) |
| London ORB high / low vs `context.orb` (London rows, 03:00 bar present) | 2,011 | 100.0% | 0 | 100.0% | **PASS** — 82 session-days; 0 `NOT_AVAILABLE` |
| PWH vs `wall_context` (Pine weekly `high[1]`) | 3,161 | 100.0% | 4,110 (3,829) | 96.135% | **PASS** (v1.3 denominator) |
| PWL vs `wall_context` (Pine weekly `low[1]`) | 3,161 | 100.0% | 4,110 (3,828) | 96.122% | **PASS** (v1.3 denominator) |
| `LC_ZONE` 1H supply / demand (top, bottom, tests, broken, formed_ts) | 2,977 / 2,621 | 100.0% | 3,586 / 2,909 (all agree) | 100.0% | **PASS** |
| `LC_ZONE` 4H supply / demand | 1,318 / 1,427 | 100.0% | 4,513 / 3,373 (all agree) | 100.0% | **PASS** |
| MTR15 (control) | 7,199 | 100.0% | — | 100.0% | PASS |

**Overall: PASS.** Diagnostic (not in the family): **VWAP** 94.167% of 6,823 eligible,
88.997% of 7,271 all rows — `NOT_ADMITTED` tranche 1 (§3). Informational (never parity
sources): Pine `previous_day.high` / `.low` vs the bar-derived PDH/PDL 90.579% / 93.673%;
Pine `previous_day.close` vs `PDC_BAR` **1.265%** (C15); `wall_context` HOD/LOD 98.3% /
98.1% eligible (exploratory levels).

### 2.1 PWH / PWL — the 281 / 282 disagreements, all §9.4-excluded

PWH: 281 disagreeing rows, all MNQ, all in ISO week 30 (trading days 07-20: 92/92,
07-21: 68/68, 07-23: 29/29, 07-24: 92/92; mine 30,050.00 vs Pine 30,062.50). PWL: 282
disagreeing rows, all MES, the same four days (mine 7,474.75 vs Pine 7,473.00). The previous
week for week 30 is **07-13 → 07-17, the TradingView-outage week**: the bar history is
missing the bars that printed the week's extreme, so the bar-derived weekly extreme is inside
Pine's. **Every disagreeing row carries `gap_contaminated=True`; on the 3,161 eligible rows
agreement is 100%, and 3,829 / 3,828 of the excluded rows agree too.** Definition validated;
box data incomplete for that one week. Per the 2026-09-17 ruling the level stays admitted
and bar-derived; Pine `wall_context` remains the parity *source*, never the feature source.

### 2.2 VWAP — why it is `NOT_ADMITTED` (800 disagreeing rows)

Every disagreeing row falls on one of 8 trading days; on the other 45 trading days VWAP
agrees within one tick on 100% of rows. Decomposition (`disagree_by_trading_day` in the
JSON, rows disagreeing / rows on that day):

| Trading day | disagreeing rows | bar-history gap inside the day | first-bar diff | cause |
|---|---|---|---|---|
| 07-16 (TV outage) | MNQ 47/52, MES 45/66 | 480 / 315 min | 0.00 | **box bar history missing bars** Pine had — divergence starts at the gap |
| 07-17 | MNQ 79/81, MES 80/80 | 165 / 180 min | 0.00 / −2.19 (MES history starts 18:30) | same |
| 07-23 | 29/29 both | 345 / 330 min (rows start 09:45) | −22.65 / −4.14 | same |
| 08-04 | MES 29/91 | 15 min | 0.00 | one missing bar (below the 45-min §9.4 flag) |
| 08-14 | MNQ 66/73, MES 44/72 | 285 / 300 min | 0.00 | box gap |
| 09-02 | MNQ 28/90 | 30 min | 0.00 | two missing bars (below the flag) |
| 09-04 | MNQ 90/91, MES 50/91 | 15 min (the **18:00 ET anchor bar** is missing) | −1.25 / +0.57 | one missing bar at the anchor |
| **09-08 (day after Labor Day)** | **92/92 both** | **0 min** | **−3.96 MNQ / −5.61 MES at the 18:00 ET bar** | **Pine `ta.vwap` did not reset at Mon 18:00 ET** — TradingView carried the 09-07 holiday session into the 09-08 daily bar (C14) |

Split by the §9.4 flag: 402 disagreements gap-flagged (excluded), 398 on eligible rows — of
those, 184 are the 09-08 holiday-anchor rows and 214 are days with one or two missing 15m
bars (15–30 min, below the flag; C16). The same holiday effect shows on `wall_context`
HOD/LOD (MES 09-08: 92/92 disagree with zero gap): Pine's `time("D")` daily bar did not roll
at the reopen either, so **C14 is a TradingView daily-session anchoring behaviour after a
CME holiday, not VWAP-specific**; it also explains part of the Pine `previous_day.high/low`
disagreement.

**Ruling applied (2026-09-17):** VWAP `NOT_ADMITTED` for tranche 1; no holiday rule invented
from one case; tolerance untouched; VWAP stays computed and parity-reported as a diagnostic;
removed from H5 and every confirmatory level set. **C14 is queued as a post-2026-09-30
live/replay parity blocker for `vwap_*` evidence** — holiday-week VWAP comparisons are not
promotion-grade until the exact holiday/session anchor is established and live and replay
share one formula (`csv_to_replay.vwap_day_range` / `polygon_to_replay` reset at 18:00 ET
on every day).

### 2.3 `LC_ZONE` 4H — 100% once the live window is replicated exactly

Under the first (v1.2-rule) run 52 rows had a different nearest 4H zone (99.40% / 99.65%).
Cause found and fixed **in the harness, not the definition**: 14 calendar days of day files
hold ≈ 10 trading days ≈ 920 bars, fewer than the 960-bar cap, so the live window begins at
the runner's *file-date* boundary, which `ts ≥ B0 − 14 d` does not reproduce. With the
runner's file rule replicated (`live_window()` in the tool) every 4H and 1H zone row agrees:
100% on eligible rows **and** on all rows. Note for P-LIVE feature availability: 77% of 4H-zone
rows and 55% of 1H-zone rows are §9.4-contaminated (10-day lookbacks vs any ≥ 45-min gap),
consistent with the closed study's gap ledger — the P-REPLAY corpus has no such gaps.

### 2.4 ORB denominators

`NY_ORB` `NOT_AVAILABLE` session-days: `MES:2026-07-17`, `MES:2026-08-04`,
`MNQ:2026-09-02` — on all three the runtime still carried an ORB (Pine's first-observed
in-session bar fallback), the designed divergence the prereg excludes; 86 rows. London: 0.
The 09:30 / 03:00 bar itself (`NOT_IN_WINDOW`, valid from 09:45 / 03:15) is excluded on
77 / 82 rows.

## 3. Definition conflicts found (recorded in prereg v1.3 §15; freeze intact)

- **C14 — Pine daily-session anchor after a CME holiday** (VWAP, HOD/LOD, daily H/L/C).
  Queued post-09-30 as a live/replay parity blocker for `vwap_*` lanes.
- **C15 — two "previous close" constructs.** Pine `previous_day.close` (daily `close[1]`,
  the 17:00 ET print) agrees with `PDC_BAR` (last 15m close) on 1.3% of rows; Pine daily
  H/L differ from the bar-derived PDH/PDL on 9.4% / 6.3% of rows. The study's level is
  `PDC_BAR`; Pine previous-day fields are a separate, non-admitted construct; consumers of
  `context.previous_day.close` (confluence "target near PDC", key-level observer) use the
  other one. The v1.2 claim that the Pine and collector definitions "all agree" is withdrawn.
- **C16 — VWAP gap sensitivity.** One missing 15m bar (below the §9.4 45-min flag) moves the
  cumulative VWAP by more than one tick; extremes are robust to it.

## 4. Verdict

**P1: DONE. P3: PASS (v1.3).** All 15 admitted level checks reproduce their journaled copies
at 100% within one tick on eligible rows (13 of them at 100% on all rows as well); VWAP is
withdrawn from tranche 1; nothing proceeded into P2 / candidate regeneration / outcomes.
P3 is closed. The next step is P2 (candidate regeneration spec + family compatibility
matrix), which requires its own go.
