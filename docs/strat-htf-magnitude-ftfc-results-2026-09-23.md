# Strat Setups on 60m / 4H / Daily (Magnitude + FTFC, Intrabar Entry) — Results (2026-09-23)

**RESEARCH / AUDIT ONLY. NO EXECUTION AUTHORITY.**

- **Preregistration:** `docs/prereg-strat-htf-magnitude-ftfc-2026-09-23.md`
  (#938, merged `448d228` before any script existed).
- **Run:** once, at script commit `dd48525` (script sha256 `37b4c809…`).
- **Results:** `docs/strat-htf-magnitude-ftfc-results-2026-09-23.json`.

Nothing here changes the observer, the paper lanes or the demo lanes.

## Verdict

**DOES NOT CLEAR: all 54 MNQ candidate cells** (60m, 4H and daily × 18).

- **No PASS and no PROMISING:** no cell met the minimum trade count together
  with PF ≥ 1.94, both halves positive, leave-best-day positive and top-3-days
  < 50%.
- **Recorded as:** a rejection of these Strat setups, with an intrabar stop
  entry, on 60m/4H/daily for MNQ on this data (2024-11 → 2026-07, with
  2026-07-24 → 09-21 out of sample).
- **Per §9:** no variants will be run on this data.

**Family-wise null, 95th percentile of the best cell's PF under random
direction flips (500 runs, seed 20260923):**

| TF | Null threshold | Gate used |
|---|---|---|
| 60m | 1.932 | 1.94 floor |
| 4H | 1.985 | 1.985 |
| Daily | 1.356 | 1.94 floor |

In a typical flip run, the best 4H cell reached PF 1.41 and the best 60m cell
PF 1.35. PF values like the ones below are what luck produces here.

## What the numbers say (MNQ, main window, after costs)

Cells are shown as trades · PF · net.

**4H, the strongest timeframe, still below the bar:**

| Setup | 2R | 2R + FTFC | Magnitude | Magnitude + FTFC |
|---|---|---|---|---|
| 2-2 reversal | 295 · 1.16 · +$7,535 | 102 · 0.88 · −$2,228 | 311 · 0.96 · −$1,317 | 87 · 0.71 · −$2,714 |
| 1-2-2 | 102 · 1.41 · +$4,936 | 25 · 1.40 · +$1,401 | 98 · 1.25 · +$1,910 | 24 · 1.48 · +$1,079 |
| 2-1-2 reversal | 119 · 1.16 · +$2,546 | 26 · 0.83 · −$639 | 105 · 1.32 · +$2,346 | 23 · 1.67 · +$1,101 |
| 3-2-2 reversal | 77 · 0.82 · −$2,977 | 19 · 1.55 · +$1,629 | 61 · 0.98 · −$168 | 13 · 0.57 · −$844 |
| 3-1-2 | 52 · 0.86 · −$1,378 | 17 · 0.56 · −$1,559 | 39 · 0.81 · −$854 | 13 · 1.22 · +$172 |
| 2-2 continuation | 412 · 1.12 · +$8,783 | 249 · 0.94 · −$2,859 | — | — |

**How each of those failed:**
- **2-2 reversal and 2-2 continuation, 2R unfiltered (baselines, not
  candidates):** both halves positive, but PF 1.12–1.16, and 2-2 reversal is
  negative out of sample.
- **1-2-2 2R (baseline):** PF 1.41 with both halves positive, but out of
  sample is −$179 on 13 trades.
- **Filtered 1-2-2:** 24–25 trades, below the 40 minimum.

**60m:** nothing above PF 1.29.
- 2-2 reversal with the magnitude target lost $9,881 on 986 trades.
- 2-2 continuation 2R made +$8,114 on 1,185 trades (PF 1.08), but H1 was
  negative.

**Daily (capped at PROMISING; 25-trade minimum):**
- **2-2 reversal magnitude + FTFC:** 23 trades, PF 4.00, +$4,588, both halves
  positive, OOS +$1,012 on 3. It fails only the trade minimum, and it is the
  prior-exposed cell (the Daily 2-2 lane).
- **2-1-2 reversal 2R + FTFC:** 9 trades, PF 2.89, +$4,408.
- Both counts are far too small to call anything. Only two daily candidate
  cells reach 25 trades: 2-2 continuation 2R + FTFC (64 trades, PF 0.96) and
  2-2 reversal magnitude (63, PF 0.74).

**Hammer at a level:** 0–11 trades per cell. Too rare to judge.

## Findings (descriptive; none is a rule)

1. **Bigger bars helped a little, not enough.** 4H is the only timeframe where
   several unfiltered setups made money in both halves (2-2 reversal, 2-2
   continuation, 1-2-2). All sit at PF 1.1–1.4, inside what random direction
   flips produce (median best-cell PF 1.41 on 4H).
2. **FTFC did not carry over from 15m.** At 15m (#936) it improved 4 of 5
   reversal baselines. Here, adding FTFC to the 2R version:
   - **60m:** lowered PF for every setup (e.g., 2-2 reversal 1.02 → 0.93,
     3-1-2 1.19 → 1.13, 2-2 continuation 1.08 → 0.98);
   - **4H:** lowered it for 4 of 6 (2-2 reversal 1.16 → 0.88, 2-1-2
     1.16 → 0.83, 3-1-2 0.86 → 0.56, 2-2 continuation 1.12 → 0.94). 1-2-2
     was flat and 3-2-2 improved (0.82 → 1.55, 19 trades).

   On this data, FTFC is not a reliable filter at higher timeframes.
3. **The magnitude target lost to 2R on 60m for every setup except 3-2-2**
   (1.10 vs 1.01). On 4H it was mixed: better for 2-1-2 and 3-2-2, worse for
   2-2 reversal, 1-2-2 and 3-1-2. At 15m it lost everywhere. The one place
   magnitude + FTFC looks strong is daily 2-2 reversal: 23 trades, the same
   pattern the existing Daily 2-2 lane trades.
4. **MES does not replicate MNQ's best baselines.** 2-2 continuation is PF
   1.12 (4H) and 1.08 (60m) on MNQ, but 0.90 and 0.88 on MES. 4H 1-2-2 2R is
   PF 1.41 on MNQ and 1.33 on MES, the only close match.

## Integrity (prereg §8)

- **Aggregation:** HTF highs/lows equal their 15m extremes (all bars). 4H
  buckets start only at 18/22/02/06/10/14 ET.
- **Daily bars vs the Daily 2-2 lane's `_daily_sessions`: 461/461 identical
  (100%), MNQ and MES.** See the disclosure below: the first attempt failed.
- **Stop-entry parity:** unit-tested against `PaperBroker(stop_market)` for
  trigger touch, gap-open and no-touch.
- **Causality:** unit tests show that arming and FTFC are unchanged when every
  later bar is mutated.
- **Data:**
  - 46,559 MNQ 15m bars (2024-10-01 → 2026-09-22).
  - 3 bars differed between v2 and the canonical corpus (2025-09-08 22:30Z,
    2025-09-09 16:45Z, 2025-12-11 21:30Z); the canonical corpus was kept.
  - Complete HTF bars: 60m 11,639 / 11,652; 4H 3,012 / 3,041; daily 484 / 497.
  - Fingerprints: MNQ canonical `a118637b…`, v2 `55e7bc33…`, extension
    `31fb689d…`.

## Disclosures (interpretations and one pre-scoring fix)

1. **Integrity check 1 failed first, and was fixed before any scoring.**
   - The first build used a plain 18:00-ET calendar, which treated CME holidays
     (Thanksgiving, MLK Day, …) as their own trading days. Daily identity with
     the lane was 94.3%, below 99%.
   - The fix was to use the repo's proven calendar
     (`context.cme_trading_day`, the same one the lane uses). A holiday's
     shortened session then folds into the next trade date, and a daily bar
     may span the holiday halt that ends at an 18:00 ET reopen.
   - Re-checked at 100%. No outcome had been computed; the script committed at
     `dd48525` includes the fix.
2. **A two-sided inside-bar setup** (2-1-2, 3-1-2) whose scored side has an
   invalid magnitude is counted `MAGNITUDE_INVALID` when that side triggers,
   not at arming. If the continuation side breaks first, it is
   `CONTINUATION_FIRST`. No trade is affected.
3. **Busy rule:** a signal on the same 15m bar as the prior trade's exit is
   `SKIPPED_BUSY`.
4. **Null:** flipped trades that were still open at the end of the data are
   dropped from that trade's flip draw (rare).
5. **The JSON's source paths** were made repo-relative after the run (the
   local path prefix was removed). No numbers changed.
6. **Per-trade rows** are kept out of the repo.

## What this closes, and what it does not

- **Closed:** "the Strat just needs bigger timeframes and the proper entry".
  With an intrabar entry on 60m/4H/daily, no setup × target × FTFC combination
  beats random direction flips on MNQ.
- **Still open, forward only:** daily 2-2 reversal. It looks strong here (23
  trades) and in the existing Daily 2-2 lane (34 trades, PF 2.02). Both
  samples are tiny. The existing lane keeps collecting under its own rules; no
  new lane is permitted by this result.
- **Unchanged:** the observer, #940's labels (15m), the paper lanes and the
  demo lanes.
