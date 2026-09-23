# Prereg #929 forward evaluator

**RESEARCH / AUDIT ONLY. No execution authority.** Implements the evaluator for
[`prereg-mnq-portfolio-ex-asia-forward-2026-09-23.md`](prereg-mnq-portfolio-ex-asia-forward-2026-09-23.md).
Where this note and the prereg disagree, the prereg wins.

**Current status (2026-09-23): step 0 PASSES on Polygon data (attempt 3, prereg §9).**
The look is still blocked by the minimum sample. No forward P&L has been computed,
printed or saved.

## Files

| File | Role |
|---|---|
| `research/mnq_combined_portfolio_audit.py`, `scripts/mnq_combined_portfolio_audit.py`, `tests/test_mnq_combined_portfolio_audit.py`, `research/mnq_sustained_trend_continuation_v1.py`, `scripts/mnq_sustained_trend_continuation_v1.py` | Frozen #915 / #912 code, byte-identical to `5a9f14b` (git blob hashes pinned in `tests/test_prereg929_forward_portfolio.py`). |
| `research/prereg929_forward_corpus.py`, `scripts/prereg929_forward_corpus.py` | Fresh Polygon fetch → settlement → existing enrichment path → gap-day removal → corpus + manifests (§9.1–§9.3). The box-bar loader is kept only for its tests; box bars are not scored. |
| `research/prereg929_step0.py`, `scripts/prereg929_step0_parity.py` | Step 0 checks 0a–0d. |
| `research/prereg929_forward_portfolio.py`, `scripts/prereg929_forward_portfolio.py` | Adapter harness, blind `counts` mode, gated single `look`. |

## Enrichment path (reused, not re-implemented)

`scripts/polygon_to_replay.py::derive_candles(raw, instrument, timeframe_minutes)`
turns raw OHLCV (`ts` unix seconds, open/high/low/close/volume) into corpus rows
(session, VWAP, EMA 9/21/55/200, HOD/LOD, NY and London ORB, strat bar types,
HTF FTFC, PDH/PDL/PDC, canonical `market_condition` via
`scripts/pine_market_condition.py`). It is the function
`scripts/structural_level_corpus_build.py` pins, and it produces the
`replay_corpus_v1*` schema (current code adds `prev_week_high/low`, a superset).
Since amendment 1 the builder fetches through `PolygonFuturesClient.fetch_continuous`
(roll 8 days, the same call `polygon_to_replay.main` makes), calls `derive_candles`
unchanged, filters to the corpus start like `polygon_to_replay.main`, and splits by
UTC day.

## Amendment 1 rules as implemented (prereg §9)

- **Settlement (§9.1):** `settled_cutoff(fetched_at)` = the latest 17:00 ET close at
  least 24 h before the fetch; later bars are dropped. Each run re-fetches the
  whole window; nothing is cached across runs. `MANIFEST_<tf>m.json` and
  `FORWARD_MANIFEST.json` record the fetch time, cutoff and the SHA-256 of the
  raw bars (`raw_bars_sha256`: canonical `[ts,o,h,l,c,v]` lines).
- **Warm-up (§9.2):** forward corpus starts `2026-07-25` (`FORWARD_CORPUS_START`) with
  the 10-day `polygon_to_replay` pre-roll; step 0 uses a 60-day pre-roll.
- **Gap days (§9.3), `detect_gap_days`:** per weekday CME observation day
  (prior 18:00 ET → 17:00 ET):
  - regular day: every 5m and 15m slot must exist;
  - reduced-schedule day (a `cme_equity_index_non_trade_dates` date, Dec 24,
    the day after Thanksgiving, Jul 3): no missing slot between the first and
    last bar present (the calendar has no abbreviated hours); an empty
    reduced day is a closure;
  - any day: a 15m bar without its three 5m bars (or the reverse) is a gap.
  Gap days are removed from both timeframes after enrichment (`drop_obs_days`)
  over the whole loaded window. The portfolio CLI reads them from the
  manifests and refuses a corpus without a Polygon manifest.
- **Voids (§9.3), `split_void_gap_fills`:** after the frozen shared-account replay,
  a fill whose signal/fill..exit span (or open-to-`as_of`) touches a gap day's
  [prior 18:00 ET, 18:00 ET) window is `VOID_GAP_DAY`: not terminal, excluded
  from every §5 number and from leave-one-out, counted by family. Skips stay as
  replayed. Gap days in the scoring period / (observed days + gap days) > 10%
  → H1 `INSUFFICIENT_DATA`, H2 `NOT CONFIRMED`.
- **0c holiday class (§9.4):** `holiday_boundary_explanations` explains a 0c
  mismatch only if all its timestamps fall on 06-21..06-23 or 07-05..07-07 (UTC
  days), each of those days has recorded corpus diffs, and every corpus diff
  anywhere in the overlap is a day-boundary field. Reviewed explanations
  (`--explanations`) still work as before.
- **0d (§9.4):** adds 3-2-2 and Miyagi over `replay_polygon`/`replay_polygon_5m`
  vs `replay_corpus_v1_market_condition_fixed`/`replay_corpus_v1_5m`, #915 window.

## How the three permitted changes are applied

The frozen files are not edited. The harness patches module attributes for the
duration of a call and restores them:

- **Corpus directory + date range.** Adapters already take corpus roots. The
  window is the frozen module's `START`/`END`. Day-range views are symlink dirs.
- **Drop `ASIA_D_EMA` for H1.** `replay_portfolio(..., excluded_families={"ASIA_D_EMA"})`.
- **Tie order.** Frozen `TIE_PRIORITY` minus Asia is exactly the §4 order (tested).

Two further mechanics were needed to run the frozen adapters on any corpus
other than the frozen one. Both are listed under amendments below:

- The #915 full-window **control assertions** (for example "4HR fills == 80")
  describe the frozen corpora. Off those corpora the assertion is a no-op, the
  control-only summaries are made `None`-safe (`_none_safe`), and control
  diagnostics are discarded unread. Every other fail-closed guard still runs,
  including the 3-2-2 detector/state-machine crosscheck and the #912 5m
  coverage guard. stdout and stderr are swallowed while adapters run.
- **12HR Miyagi candidates.** The #915 adapter reads a frozen candidate file.
  For other corpora, candidates come from the unchanged detector
  (`research/run_12hr_miyagi_evidence.detect_candidates`) pointed at the corpus
  and are handed to the frozen adapter under the frozen file name. Step 0 checks
  that the detector reproduces the frozen file (it does: 15/15 identical).

## Runbook

All outputs go outside the repo. Never commit corpora or reports. Runs on the Mac:
the Polygon key is only in the local `.env` (the box has none).

1. **Build the forward corpus** (fresh fetch, about 1 min):

   ```text
   python3 scripts/prereg929_forward_corpus.py --out <scratch>/prereg929_corpus
   ```

2. **Step 0** (fresh fetch + adapters, a few minutes):

   ```text
   python3 scripts/prereg929_step0_parity.py --data-root data \
     --out <scratch>/step0_attemptN.json --summary <scratch>/step0_attemptN.txt \
     [--previous-report <scratch>/step0_attempt(N-1).json] [--explanations reviewed.json] \
     [--keep-rebuilt <scratch>/rebuilt]
   ```

   Exit code 0 = PASS, 2 = FAIL. `--previous-report` carries the attempts log
   forward (prereg §6/§7). The tool never writes reviewed explanations itself;
   the §9.4 holiday class is the only automatic one.

3. **Counts** (blind, safe to run any time):

   ```text
   python3 scripts/prereg929_forward_portfolio.py counts \
     --corpus-5m <scratch>/prereg929_corpus/5m --corpus-15m <scratch>/prereg929_corpus/15m [--out counts.json]
   ```

   Output: fills, terminal fills and `VOID_GAP_DAY` fills by family, open fills,
   busy-skips (by skipped and by blocking family), max-trades skips, scored
   attempts, CME observation days (18:00 ET roll, completed, with data), gap
   days and the 10% cap, per-family pipeline status. `assert_blind` rejects any
   key that looks like P&L. If any family fails closed, no portfolio counts are
   produced.

4. **Look** (once, later; rebuild the corpus fresh first):

   ```text
   python3 scripts/prereg929_forward_portfolio.py look \
     --corpus-5m ... --corpus-15m ... \
     --step0-report <passing step0.json> --out look.json --confirm-single-look
   ```

   The look is refused when: no `--confirm-single-look`; `--out` already exists;
   the step-0 report is missing or not PASS on all of 0a–0d; the corpus has no
   Polygon manifest; any family failed closed; or fewer than 40 terminal
   portfolio fills or 120 CME days before 2027-09-30. It writes the §7 fields
   plus the gap-day status. Record the manifests' `raw_sha256` and fetch time
   in §7.

Scoring rule: an event counts only if its `signal_ts` and `eligible_fill_ts`
are both at or after 2026-09-23T22:00:00Z. Earlier events are dropped before the
shared-account replay, so the account starts flat at the scoring start.
"Terminal" follows the frozen `summarize_fills` set (WIN/LOSS/BREAKEVEN).
Sustained-trend `OPEN_EOD` and Asia `EXPIRED` are not terminal.

## Step 0 result, attempt 3 (Polygon, 2026-09-23 02:51Z) — PASS

| Check | Verdict | Numbers |
|---|---|---|
| 0a 5m OHLC, fresh Polygon vs `replay_corpus_v1_5m`, 07-01..07-23 | **PASS** | 4644/4644 bars identical; 0 RTH gaps |
| 0b 15m OHLC, fresh Polygon vs `replay_corpus_v1_market_condition_fixed`, 06-05..07-23 | **PASS** | 3188/3188 bars identical (the June roll matches too); 0 RTH gaps |
| 0c candidate parity (Polygon rebuild, 60-day pre-roll, vs frozen) | **PASS** | 0 mismatches; the holiday class was not needed. Streams: Asia 390 attempts / 272 fillable, Sustained v1 10/0, 4HR 3/3, Daily 4/1, 3-2-2 and Miyagi 0 (both sides). The corpus diffs are day-boundary fields only, on 06-21..06-23 and 07-05..07-07. |
| 0d lineage | **PASS** | v1_5m vs 4hr_audit: 0 mismatches (598 days). `replay_polygon*` vs v1 corpora, #915 window: 3-2-2 12/11 and Miyagi 1/1, identical |

Supplementary: the Miyagi detector reproduces the frozen file (15/15); the 3-2-2 and
Miyagi detector lineages are identical; the ported #915 core reproduces prereg §1.
No gap days in the step-0 rebuild.

First forward build (fetched 2026-09-23 02:54Z, cutoff 2026-09-21 21:00Z,
corpus 2026-07-25..): one gap day, **2026-09-11**, removed (240 5m / 80 15m
candles). Counts: all six families OK (Sustained v1 included), 0 scored fills,
0 completed CME days, because scoring starts 2026-09-23T22:00Z.

## Step 0 attempts 1–2 (box bars, superseded by amendment 1)

Box copies: 5m 2026-07-01..09-23 (65 files), 15m 2026-06-05..09-23 (95 files).

| Check | Verdict | Numbers |
|---|---|---|
| 0a 5m OHLC vs `replay_corpus_v1_5m`, 07-01..07-23 | **FAIL** | 2742/2808 overlapping bars match (97.65% < 99.5%); 66 mismatches, mostly the close off by 2–4 ticks (61 are ≤ 1 pt, 1 is > 3 pt); 432 corpus RTH bars missing in box; 6 RTH gap runs > 2 bars (07-03 and 07-06..07-10: the 5m collector has no files 07-03..07-12) |
| 0b 15m OHLC vs `replay_corpus_v1_market_condition_fixed`, 06-05..07-23 | **FAIL** | 2674/2943 match (90.86%); 176 of the 269 mismatches are on 06-11/06-12 (about 300 pt apart: the two series are on different contracts across the June roll); without those days the rate is still 96.8%; 11 RTH gap runs > 2 bars (06-05, 06-09, 06-29, 07-14, 07-15, 07-16) |
| 0c candidate parity (rebuilt vs frozen overlap) | **FAIL** | 271 unexplained mismatches. 4HR: 07-06 candidate only in frozen (box gap day). Daily 2-2: 3 attempts only in frozen (07-08, 07-10, 07-14). Sustained v1: rebuilt side fails closed on the frozen #912 5m-coverage guard (8 missing 5m day files, 96 uncovered 15m bars). Asia D+EMA (15m, 06-05..07-23): 43/46 attempts only on one side, 34/38 fillable events only on one side, 104 field diffs (stop/target/entry). 3-2-2 and Miyagi: 0 candidates on both sides (identical, but vacuous). |
| 0d 4HR-audit lineage (`replay_corpus_v1_5m` vs `_4hr_audit`, 2024-07-29..2026-06-26, 598 shared days) | **PASS** | 0 mismatches. Identical streams: 4HR 77 attempts / 76 fillable; 3-2-2 34/33; Daily 201/70; Miyagi 8/8 |

Supplementary (reported, not prereg gates):

- The unchanged Miyagi detector reproduces the frozen #915 candidate file: 15/15
  identical. This is required for 0c because forward Miyagi uses the detector.
- 3-2-2 detector on `replay_polygon` vs `replay_corpus_v1_market_condition_fixed`,
  2025-07-24..2026-06-26: identical (12/12).
- Miyagi detector on `replay_polygon*` vs the v1 corpora, same range: identical (4/4).
- The ported #915 core on the frozen corpora reproduces prereg §1 with all
  controls passing: six-family 488 fills / +$5,138.46; ex-Asia 60 fills / +$9,372.70
  (historical #915 window only).

Attempts: #1 had Daily 2-2 and Sustained v1 failing on **both** sides of 0c with
`float(None)`. The frozen control summaries return `None` PF on a three-week
window with no control trades. That was a harness bug, fixed with `_none_safe`
(control-only). #2 is the result above. No family, threshold, or data was changed.

## Landmines

- **Polygon is local only.** The box `.env` has no key; run everything on the Mac.
- A gap day is removed from the whole loaded window, warm-up included, so
  indicators on later days still carry that day's bars (computed before
  removal), but the families never see its candles.
- The frozen corpora predate the holiday-aware trade date (#775). Day-boundary
  fields differ after holidays (see 0c). The forward corpus uses current code
  throughout.
- The box bars (and their gaps) still feed the live paper bot. That is outside
  this study.
- The #915 control asserts are no-ops off the frozen corpora (§9.5), and the
  Miyagi forward candidates come from the detector (§9.5).
