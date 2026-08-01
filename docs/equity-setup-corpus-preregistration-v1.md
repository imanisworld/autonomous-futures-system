# Equity Setup Corpus v1 — Preregistration

**BINDING.** Committed before the first corpus fetch. Nothing below may change in
response to results. Any change requires a new version (`equity_corpus_v2`) and a
fresh replay; all prior results are preserved, including failed and unproven cells.

Status: **preregistration only.** No full corpus batch has been authorized or run.

---

## 1. Frozen universe

| Field | Value |
|---|---|
| Universe version | `equity_corpus_v1` |
| Machine-readable | `research/universe/equity_corpus_v1_universe.json` |
| Source file | `docs/options_watchlist_150.csv` |
| Source SHA-256 | `2770c80b9d6b745b481245b457957e45275ad3590d6aa5cdfc6f7ca4761f1d4d` |
| Universe SHA-256 | `327c7dcd795acc9a11d0b14c6030f0a03e14960245e3ef8740f6bedde9b90a67` |
| Git commit SHA | *recorded in the commit that adds this file* |

**156 total entries. 155 setup candidates.**

| Cohort | Count | Role |
|---|---|---|
| `single_name` | 132 | setup candidate — primary equity cohort |
| `etf` | 17 | setup candidate — ordinary ETF cohort |
| `leveraged_inverse` | 6 | setup candidate — **separate stress cohort** |
| `index` (VIX) | 1 | **regime/context input only — never a setup candidate** |

The six leveraged/inverse entries (TQQQ, SQQQ, SPXL, SPXS, SOXL, SOXS) are not in
the source watchlist; they are added here deliberately as a transfer/stress cohort.

**XLC is excluded from v1.** Adding it requires a versioned universe revision.

### Pooling rules

- `leveraged_inverse` results are **never** pooled with `single_name` or `etf`.
- **VIX is excluded from all trade statistics.**
- Every statistic is reported at three levels: **per-ticker, per-cohort, pooled.**
- **Pooled results are never presented alone.**

## 2. Frozen window

**2024-07-31 through 2026-07-30 inclusive, `America/New_York`.** 24 complete
calendar months.

2026-07-31 is excluded: at freeze time its 16:00–20:00 extended session was
incomplete, and a partial final session must not enter a corpus claimed as frozen.

Timezone handling uses the `America/New_York` zone, **not a fixed UTC offset**, so
DST transitions remain correct across both years.

## 3. Session scope and tagging

Full **04:00–20:00 Eastern**. Every bar carries exactly one tag:

| Tag | Window (ET) |
|---|---|
| `PREMARKET` | 04:00 – 09:30 |
| `RTH` | 09:30 – 16:00 |
| `AFTER_HOURS` | 16:00 – 20:00 |

- **Primary validation: RTH only.**
- **Secondary analysis: extended-hours inclusive.**
- **The two modes are never pooled into a single performance figure.**

## 4. Timeframe construction

One canonical **5-minute** corpus is fetched. All higher timeframes are **derived**
from it under the frozen anchors below. Provider-generated higher-timeframe
aggregates are **not** mixed in — their boundary conventions are not guaranteed to
match ours, and a silent boundary difference would corrupt setup detection.

| TF | Anchor | Notes |
|---|---|---|
| 5m | as fetched | canonical source |
| 15m | 09:30 ET | RTH-anchored |
| 30m | 09:30 ET | RTH-anchored |
| 1h | 09:30 ET | RTH-anchored |
| 4h | 09:30 ET | RTH-anchored |
| 1d | session | one bar per session |

**No higher-timeframe bar may silently cross a session boundary.** A derived bar
that would span `RTH`→`AFTER_HOURS`, or a session end, is closed at the boundary
and marked:

```
is_partial_interval = true
```

Partial-interval bars are **preserved, never discarded**, and are **never treated
as full-duration bars** by the detector or by any statistic.

## 5. Existing-data reuse

**TQQQ and SQQQ are refetched.** The existing `data/stocks_advisory_polygon_5m`
files cover 2025-01-17 → 2026-07-10 — a different window — and their adjustment,
timezone, and session conventions predate this preregistration. They do not match
and are not reused.

## 6. Preregistered setups (7)

`2-1-2`, `3-1-2`, `1-2-2`, `3-2-2`, `reclaim`, `break-and-retest`,
`inside-bar-continuation`.

Detection uses the existing `strategy/strat_classifier.py`
(`classify_bar`, `classify_sequence`, `classify_from_ohlc`). **No second detector
implementation** — a divergent classifier is the exact defect that gated eight
futures strategies.

## 7. Trigger, invalidation, targets — fully mechanical

- **Trigger:** break of the trigger bar's high (long) / low (short) by one tick.
- **Invalidation:** the opposite extreme of the trigger bar.
- **R** = |entry − invalidation|; **T1** = entry + 1R; **T2** = entry + 2R.

R-multiples only. Structural targets require selecting a swing, which is a
discretionary knob that cannot be honestly preregistered.

## 8. Regime definition

Computed from **SPY daily only** — one definition, no per-ticker regime.

| Regime | Condition |
|---|---|
| `TREND_UP` | SPY 20EMA > 50EMA **and** close > 20EMA |
| `TREND_DOWN` | SPY 20EMA < 50EMA **and** close < 20EMA |
| `RANGE` | otherwise |

## 9. Replay modes

Both are run and reported separately; they are never merged.

**A. `15M_NATIVE`** — setup and trigger resolved only from completed 15-minute
bars. No synthetic 5-minute assumptions.

**B. `15M_SETUP_5M_ENTRY`** — setup identified on completed 15-minute bars; entry
timing and subsequent stop/target sequencing resolved from **real** 5-minute bars.
The 5-minute detector may not originate the higher-timeframe thesis. No lookahead
into later 5-minute bars.

**5-minute bars are never interpolated or synthesized from 15-minute data.**

The gap between A and B measures how much of any apparent edge is an artifact of
bar resolution.

## 10. Same-bar ambiguity

Any bar containing both the stop and a target is counted `SAME_BAR_AMBIGUOUS`.

- **Primary result: stop-first** (conservative).
- **Target-first sensitivity reported separately.**
- The **expectancy delta between the two conventions is published**, so the
  dependence on the convention is visible rather than buried.

## 11. Evidence labels and statistics

| Sample size | Label |
|---|---|
| n < 30 | `PROBABILITY_UNPROVEN` |
| 30 ≤ n < 100 | `DESCRIPTIVE_ONLY` |
| n ≥ 100 | eligible — quotable **only** if both walk-forward halves agree in directional sign |

- **A large pooled n never rescues an inadequate per-ticker n.**
- Every rate and expectancy is reported with **n and a confidence interval**.
- **Wilson score** intervals for proportions (T1/T2-before-stop rates).
- **Bootstrap percentile** (10,000 resamples) for expectancy and average R.
- **Walk-forward split at the calendar midpoint** of the frozen window, so both
  halves are contiguous in time and a burst of setups cannot move the boundary.

**Multiple comparisons are reported transparently.** Cell counts are published in
full, including empty and failing cells. Ranking thousands of cells and declaring
the top one an edge is forbidden.

## 12. Post-preregistration prohibitions

- No changing thresholds after seeing results.
- No deleting poor-performing tickers or cells.
- No adding filters discovered from outcome data.
- No universe membership change without a new version.
- All prior results preserved, including failed and unproven cells.

## 13. Gate before the full batch

The complete corpus batch is **NOT authorized** by this document. Required first:

1. This preregistration committed and SHA-pinned. ✅
2. One representative single-name smoke test.
3. Verification of: earliest/latest timestamp, duplicate count, missing-session
   count, adjustment status, timezone/DST behavior, derived-timeframe bar counts.
4. Restart/idempotency behavior verified.
5. **Explicit operator authorization** for the full batch.

Estimated full batch: 156 entries × ~15 requests ≈ **2,340 requests**; at the
measured Polygon limit of **5 requests/minute** ≈ **7.8 hours** before retries and
validation overhead.

**Nothing deployed. No futures behavior changed. No production code modified.**
