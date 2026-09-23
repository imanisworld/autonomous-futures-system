# Prereg #929 forward evaluator

**RESEARCH / AUDIT ONLY. No execution authority.** Implements the evaluator for
[`prereg-mnq-portfolio-ex-asia-forward-2026-09-23.md`](prereg-mnq-portfolio-ex-asia-forward-2026-09-23.md).
Where this note and the prereg disagree, the prereg wins.

**Current status: step 0 FAILS (0a, 0b, 0c fail; 0d passes). The look is blocked.**
No forward P&L has been computed, printed or saved.

## Files

| File | Role |
|---|---|
| `research/mnq_combined_portfolio_audit.py`, `scripts/mnq_combined_portfolio_audit.py`, `tests/test_mnq_combined_portfolio_audit.py`, `research/mnq_sustained_trend_continuation_v1.py`, `scripts/mnq_sustained_trend_continuation_v1.py` | Frozen #915 / #912 code, byte-identical to `5a9f14b` (git blob hashes pinned in `tests/test_prereg929_forward_portfolio.py`). |
| `research/prereg929_forward_corpus.py`, `scripts/prereg929_forward_corpus.py` | Box bars to corpus rows through the existing enrichment path. |
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
The builder only parses box files (ISO or epoch-ms `ts`, `timeframe` "5m"/"15"),
calls `derive_candles` unchanged, and splits by UTC day like
`polygon_to_replay.main`.

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

All outputs go outside the repo. Never commit corpora, box bars or reports.

1. **Rebuild the forward corpus** from box bar copies (5m: box `logs/tf5m/`,
   15m: box `logs/`):

   ```text
   python3 scripts/prereg929_forward_corpus.py \
     --bars-5m <copy>/tf5m --bars-15m <copy>/tf15m --out <scratch>/prereg929_corpus
   ```

2. **Step 0** (about 1 min on the local corpora):

   ```text
   python3 scripts/prereg929_step0_parity.py --data-root data \
     --bars-5m <copy>/tf5m --bars-15m <copy>/tf15m \
     --rebuilt-root <scratch>/prereg929_corpus \
     --out <scratch>/step0_attemptN.json --summary <scratch>/step0_attemptN.txt \
     [--previous-report <scratch>/step0_attempt(N-1).json] [--explanations reviewed.json]
   ```

   Exit code 0 = PASS, 2 = FAIL. `--previous-report` carries the attempts log
   forward (prereg §6/§7). `--explanations` is a human-reviewed JSON
   `{mismatch_id: reason}`. 0c/0d pass only if every listed mismatch has one.
   The tool never writes explanations itself.

3. **Counts** (blind, safe to run any time):

   ```text
   python3 scripts/prereg929_forward_portfolio.py counts \
     --corpus-5m <scratch>/prereg929_corpus/5m --corpus-15m <scratch>/prereg929_corpus/15m \
     --corpus-start 2026-09-08 [--out counts.json]
   ```

   Output: fills and terminal fills by family, open fills, busy-skips (by
   skipped and by blocking family), max-trades skips, scored attempts, CME
   observation days (18:00 ET roll, completed, with data), per-family pipeline
   status. `assert_blind` rejects any key that looks like P&L (net, pf, pnl,
   drawdown, win, loss, result, entry/stop/target, and so on). If any family
   fails closed, no portfolio counts are produced.

4. **Look** (once, later):

   ```text
   python3 scripts/prereg929_forward_portfolio.py look \
     --corpus-5m ... --corpus-15m ... --corpus-start <same as counts> \
     --step0-report <passing step0.json> --out look.json --confirm-single-look
   ```

   The look is refused when: no `--confirm-single-look`; `--out` already exists;
   the step-0 report is missing or not PASS on all of 0a–0d; any family failed
   closed; or fewer than 40 terminal portfolio fills or 120 CME days before
   2027-09-30. At or after the deadline with the minimum unmet, the verdict is
   `INSUFFICIENT_SAMPLE`. It writes the §7 fields: H1 criteria 1–5, H1 verdict,
   H2 values and verdict, and the descriptive table (per-family
   fills/skips, leave-one-out deltas inside the five, Daily 2-2 share of net and
   of occupied account-hours, monthly net/PF, max consecutive losses, and net
   with +1 tick adverse slippage per side).

Scoring rule: an event counts only if its `signal_ts` and `eligible_fill_ts`
are both at or after 2026-09-23T22:00:00Z. Earlier events are dropped before the
shared-account replay, so the account starts flat at the scoring start.
"Terminal" follows the frozen `summarize_fills` set (WIN/LOSS/BREAKEVEN).
Sustained-trend `OPEN_EOD` and Asia `EXPIRED` are not terminal.

## Step 0 result (local data, 2026-09-23 UTC)

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

- **The Sustained v1 coverage guard blocks the whole portfolio.** The frozen #912
  guard refuses to score if any 15m bar in the loaded corpus lacks a 5m bar. With
  the corpus starting 07-01 or 07-20 it trips (07-03..07-12 gap, and a 5m NY-session
  gap on 09-03). `--corpus-start 2026-09-08` or later loads cleanly today. Any
  future 5m gap after the start will fail the whole evaluation closed, and the
  prereg forbids backfilling.
- Current `derive_candles` warms EMAs up from the first box bar (2026-07-01, no
  pre-roll), so EMAs differ from the frozen corpus early in the overlap.
- The box 15m files for early June use epoch-ms `ts`. The builder normalizes them.
- Counts from `2026-09-23` show 0 scored fills and 0 completed CME days: scoring
  has not started yet.

## Needs an operator decision / prereg amendment

See the PR report. In brief:

1. Step 0 as registered cannot pass on the fixed overlap window. Box gaps and a
   roll-week contract mismatch are permanent, and backfilling is forbidden.
2. The prereg is silent on the Miyagi candidate source and the control-assertion
   handling described above.
3. 0d names the wrong lineage for 3-2-2's 15m detector (`replay_polygon`) and
   Miyagi's fill corpus (`replay_polygon_5m`). Both are covered by the
   supplementary checks and are identical.
4. The corpus warm-up start (`--corpus-start`) should be fixed in writing before
   any forward row is scored.
