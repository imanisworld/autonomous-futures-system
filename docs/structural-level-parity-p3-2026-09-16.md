# Structural-Level Prereg — P1 feature builder + P3 levels-only parity (2026-09-16)

**Status:** RESEARCH RECORD ONLY (research module, read-only tooling, docs). No runtime,
collector, replay, strategy, risk, gate or deployment change. **No outcome was read, no
candidate was scored, no replay was run.** Prereg under execution:
`docs/prereg-dynamic-structural-level-attribution-2026-09-16.md` v1.2, frozen at `fdeac72`.
**Verdict: P1 DONE · P3 FAIL on 2 of 16 admitted level checks (VWAP, PWH/PWL) — STOP per
operator rule; disagreements characterised below, tolerance untouched, nothing continued
into outcomes.**
Machine-readable results: `docs/structural-level-parity-p3-2026-09-16-results.json`
(tool `scripts/structural_level_parity_check.py`, `slf-parity-v1`).

---

## 1. P1 — `research/structural_level_features.py`

Pure offline feature builder (levels §3, `LC_ZONE` §4.1 via the *same* `location_context`
functions the live collector uses, events §5, reduction rule §5.1, hypothesis labels §6).
No I/O; imports only `context.location_context` pure helpers and the contract tick table
(asserted by a test). Frozen constants: τ = 0.25, prox = 0.5, K = 4, R = 8, N = 2,
D_max = 1.0 (× MTR15), live bar window 960.

22 synthetic-bar tests in `tests/test_structural_level_features.py` (one per level rule and
per event), including: missing 09:30 / 03:00 bar → ORB `NOT_AVAILABLE` even when a 09:45
bar exists; VWAP does not reset at sub-session boundaries; `LC_ZONE` and MTR15 byte-match
`build_location_context` on the same window; bars after `B0` are ignored; break → retest
states incl. `RETESTED_EARLIER` / `IMMEDIATE` / `BACK_THROUGH_NO_RETEST`; anchor
tie-precedence and own-level exclusion; H5 relevance band; H2 age-matched T/F. Full suite:
5,038 passed, 7 skipped.

Two things the module adds beyond the prereg text, both fail-closed and reported, not
silent: per-level `gap_minutes` / `gap_contaminated` (§9.4, ≥ 45 min inside the formation
window) and a `BACK_THROUGH_NO_RETEST` E5 state (break held through `B−1`, `B0` closes
back through without touching τ) which is neither T nor F for H2.

## 2. P3 — levels-only parity against the frozen journal snapshot

Snapshot: VPS `logs/` read-only copy taken 2026-09-16 22:38Z (the closed study's). Window:
15m MNQ/MES decision rows with `context.timestamp` in **2026-07-16 → 2026-09-14T22:00Z
(exclusive; first Z6 bar)** = **7,271 rows** (MNQ 3,629 / MES 3,642); 892 5m-path rows and
368 out-of-window rows skipped and counted; 0 build failures; 0 rows whose `B0` bar was
absent from bar history. Reconstruction window per row = bars with ts ≤ `B0` in the prior
14 days, last 960 (the runner's `BarHistory.recent(…, 960, lookback_days=14)`).
Tolerance one tick (0.25) for every level; pass ≥ 98%.

| Admitted level | compared | agree | verdict |
|---|---:|---:|---|
| PDH / PDL / PDC vs `location_context.levels` | 7,199 | 100.0% | **PASS** |
| ONH / ONL vs `location_context.levels` (rows that carry one) | 5,337 | 100.0% | **PASS** |
| NY ORB high / low vs `context.orb` (NY rows, canonical 09:30 bar present) | 2,150 | 100.0% | **PASS** — 79 session-days; 3 `NOT_AVAILABLE` days excluded (§14 P3) |
| London ORB high / low vs `context.orb` (London rows, 03:00 bar present) | 2,011 | 100.0% | **PASS** — 82 session-days; 0 `NOT_AVAILABLE` |
| `LC_ZONE` 1H supply / demand (top, bottom, tests, broken, formed_ts) | 6,563 / 5,530 | 100.0% | **PASS** |
| `LC_ZONE` 4H supply / demand | 5,831 / 4,800 | 99.40% / 99.65% | **PASS** (52 rows differ; see §2.3) |
| MTR15 | 7,199 | 100.0% | PASS (control) |
| **VWAP** vs `context.vwap.value` (Pine `ta.vwap(hlc3)`) | 7,271 | **88.997%** | **FAIL** |
| **PWH / PWL** vs `wall_context` (Pine weekly `high[1]/low[1]`) | 7,271 | **96.14% / 96.12%** | **FAIL** |

Informational (not admitted / not parity targets): Pine `previous_day.high/low` vs the
collector's PDH/PDL 90.6% / 93.7%; Pine `previous_day.close` vs the collector's PDC
**1.3%** (different construct — see C15); `wall_context` HOD/LOD 96.4% / 96.7%.

### 2.1 VWAP — exact disagreement (800 rows)

Every disagreeing row falls on one of 8 trading days; on the other 45 trading days VWAP
agrees within one tick on 100% of rows. Decomposition (`disagree_by_trading_day` in the
JSON):

| Trading day | rows disagreeing | bar-history gap inside the day | first-bar diff | cause |
|---|---|---|---|---|
| 07-16 (TV outage) | MNQ 47/52, MES 45/66 | 480 / 315 min | 0.00 | **box bar history missing bars** Pine had — divergence starts at the gap |
| 07-17 | MNQ 79/81, MES 80/80 | 165 / 180 min | 0.00 / −2.19 (MES first bar is 18:30, the 18:00 bar is missing) | same |
| 07-23 | 29/29 both | 345 / 330 min (rows start 09:45) | −22.65 / −4.14 | same |
| 08-04 | MES 29/91 | 15 min | 0.00 | one missing bar (below the 45-min §9.4 flag) |
| 08-14 | MNQ 66/73, MES 44/72 | 285 / 300 min | 0.00 | box gap |
| 09-02 | MNQ 28/90 | 30 min | 0.00 | two missing bars (below the flag) |
| 09-04 | MNQ 90/91, MES 50/91 | 15 min (the **18:00 ET anchor bar** is missing; history starts 18:15) | −1.25 / +0.57 | one missing bar at the anchor |
| **09-08 (day after Labor Day)** | **92/92 both** | **0 min** | **−3.96 MNQ / −5.61 MES at the 18:00 ET bar** | **Pine `ta.vwap` did not reset at Mon 18:00 ET** — TradingView carried the 09-07 holiday session into the 09-08 daily bar; the divergence is present on the first bar and decays as volume accumulates |

Split by the §9.4 flag: 402 disagreements gap-flagged, 398 "clean" — of the clean ones 184
are the 09-08 holiday-anchor rows and ~214 are days with a single missing 15m bar (15–30
min, below the 45-min flag). VWAP is cumulative and volume-weighted, so one missing bar
already exceeds one tick.

**Reading:** the frozen §3.7 definition (reset at the first bar with open ≥ 18:00 ET,
hlc3 × volume) reproduces Pine **exactly on every regular trading day with complete bar
history**. It fails for two reasons that are not tolerance: (a) the box bar history is
incomplete on outage days (data completeness, not definition), and (b) **TradingView's
session anchor after a CME holiday is not 18:00 ET** — a genuine source-of-truth
divergence (**C14**, new) that also applies to `csv_to_replay.vwap_day_range` /
`polygon_to_replay` (both reset at 18:00 ET on every day) and therefore to every existing
`vwap_*` replay-vs-live comparison on holiday weeks. Not fixed here.

### 2.2 PWH / PWL — exact disagreement (283 / 284 rows)

100% of PWH disagreements are MNQ rows in ISO week 30 (trading days 07-20, 07-21, 07-23,
07-24: 92/92, 68/68, 29/29, 92/92 — mine 30,050.00 vs Pine 30,062.50) plus one 09-15 row;
100% of PWL disagreements are the MES rows of the same week (mine 7,474.75 vs Pine
7,473.00). The previous week for week 30 is **07-13 → 07-17, the TradingView-outage week**:
the bar history is missing the bars that printed the week's extreme, so the box-derived
weekly extreme is inside Pine's. **Every disagreeing row carries `gap_contaminated=True`;
on the 3,161 clean rows agreement is 100%.** (The two 09-15 rows are the Z6 roll.)

**Reading:** the §3.2 definition matches Pine wherever the bar history is complete; the box
bar history cannot reproduce PWH/PWL for the week after an outage. §9.4 already excludes
those rows for that level. The P3 rule as frozen counts them in the denominator, so the
level FAILS as written — the decision whether the parity denominator may exclude
§9.4-contaminated rows is the operator's, not the tool's.

### 2.3 `LC_ZONE` 4H — 52 rows (PASS, disclosed)

35 + 17 rows on 07-17 10:00–11:45Z, 07-30 15:15Z and 08-12 04:00–05:30Z have a different
nearest 4H zone (different `formed_ts`). All sit on or after outage/gap days where fewer
than 960 bars exist in 14 days, so the 960-bar cap does not bind and the exact start of the
live window depends on the runner's *file-date* boundary (`lookback_days=14` reads day
files by processing date), which the harness approximates with `ts ≥ B0 − 14 days`. The
4H epoch buckets then start a few bars apart and one zone at the very back of the 60-bar
lookback differs. Harness approximation, within tolerance; 1H zones are unaffected (100%).

### 2.4 ORB denominators

`NY_ORB` `NOT_AVAILABLE` session-days: `MES:2026-07-17`, `MES:2026-08-04`,
`MNQ:2026-09-02` — on all three the runtime still carried an ORB (Pine's first-observed
in-session bar fallback), the designed divergence the prereg excludes. 86 rows. London: 0.
The 09:30 / 03:00 bar itself (`NOT_IN_WINDOW`, valid from 09:45 / 03:15) is excluded on
77 / 82 rows.

## 3. New definition conflicts found (report only; freeze intact)

- **C14 — Pine VWAP holiday anchor.** After a CME holiday session TradingView's
  `ta.vwap` does not reset at the next 18:00 ET reopen (09-08 evidence above). The
  repo's proven "one reset per CME trading day at 18:00 ET" convention holds on regular
  days only. Affects: prereg §3.7, `csv_to_replay.vwap_day_range`, `polygon_to_replay`,
  and live/replay parity of every `vwap_*` family on holiday weeks. Options for the
  operator (none taken): exclude post-holiday trading days for VWAP features via a holiday
  ledger; or mark VWAP `NOT_ADMITTED` per the P3 rule; or (post-09-30) define the
  holiday-session convention explicitly.
- **C15 — two "previous close" constructs.** Pine `previous_day.close` (daily
  `close[1]`, the 17:00 ET print) agrees with the collector's `prev_close` (last 15m bar
  close) on 1.3% of rows. The prereg's PDC is the collector's; anything that reads
  `context.previous_day.close` (confluence "target near PDC", key-level observer) is using
  the other one.
- **C16 — VWAP gap sensitivity.** A single missing 15m bar (below the §9.4 45-min flag)
  moves VWAP by more than one tick; PDH/ONH-style extremes are robust to it. If VWAP stays
  admitted, its contamination rule needs to be "any missing bar in the current trading
  day" (704 rows here), a v1.3 amendment for the operator.

## 4. Verdict and next step

**P1: DONE.** **P3: FAIL on VWAP and PWH/PWL as the rule is written; PASS on all 13 other
admitted checks at 99.4–100%.** Per the operator rule this pass stops here: the tolerance
was not loosened, no rows were dropped to make a level pass, and nothing proceeded into
P2 / candidate regeneration / outcomes.

Operator decisions needed before P2 (each is a prereg amendment or a ruling, not code):

1. PWH/PWL: rule whether the P3 denominator may exclude §9.4 gap-contaminated rows (then
   100% / PASS) — or whether P-LIVE PWH/PWL must be read from Pine's `wall_context` copy
   (the prereg already says "no replay copy exists — parity proven against wall_context").
2. VWAP: choose between `NOT_ADMITTED` (prereg P3 clause) and an amended §3.7 with an
   explicit holiday-anchor rule + "any missing bar" contamination (C14, C16).
3. Whether C14 is queued as a post-09-30 runtime/replay parity fix for the `vwap_*` lanes.
