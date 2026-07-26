# 12HR Miyagi — Canonical Evidence Study (2026-07-26)

**Status: PROMISING BUT UNPROVEN (both MNQ and MES).** No configuration,
risk, execution, or deployment behavior changed. This is a research-only
evidence report. No coded Miyagi detector existed anywhere in this repository
before this study (confirmed by `git log --all -- '**/*miyagi*' '**/*Miyagi*'`
and `git log --all --grep=Miyagi`, both empty aside from the rules/spec docs
themselves).

---

## 0. Provenance context — do not treat as proof

`docs/strategy-rules/12HR_Miyagi_Rules.md` §9 documents an external,
non-reproducible manual study: MNQ n=13, 92.3% win rate, +$102.35
expectancy, PF 5.33; MES n=20, 75.0% win rate, +$25.78 expectancy, PF 2.22.
No dated manual-sample CSV backing these figures exists anywhere in this
repository's git history (searched all commits/branches; none found — the
same situation `research/reconcile_322_first_live.py` documents for the
60M 3-2-2 lane's own external study). Per the rules doc's own §9 caveat
("Sample sizes are small... Trade 1 contract until 30+ live trades establish
the edge") and `docs/strategy-rules/README.md`'s explicit framing, these
numbers are **provenance context only**, never a target this study
attempted to reproduce, match, or validate against.

---

## 1. Critical data-availability finding — the task brief's premise was wrong

The build brief for this study described `data/replay_polygon_5m/{MNQ,MES}/`
as strictly RTH-only: file start at 9:30 AM ET, **no pre-market 5-minute
data anywhere in this repository's local caches**. This was verified by
direct inspection (`ls`, `head`/`wc -l` on every file, then a full
programmatic scan of all 621×2 daily files) and found to be **wrong for
all but the very first day of coverage**:

- `MNQ_2024-07-02.jsonl` / `MES_2024-07-02.jsonl` — the first day in the
  5-minute cache — genuinely starts at `13:30:00+00:00` (9:30 AM ET),
  confirming the brief's description for that one date.
- **From `2024-07-03` onward, both instruments' 5-minute caches contain
  near-continuous ~23-24h coverage** (matching CME Globex's session, with a
  routine ~1-hour daily maintenance gap around 5-6 PM ET), **including the
  4:00-9:30 AM ET pre-market window** Step 5 of the detector spec needs at
  true 5-minute granularity.
- A full programmatic scan of the `[4:00, 9:30)` ET window across every
  weekday file in `2024-07-02..2026-06-26` for both instruments found:
  - **6 weekday dates with a fully missing window**: 2024-07-02 (the
    truncated first day), the four Christmas/New-Year's holidays in range
    (2024-12-25, 2025-01-01, 2025-12-25, 2026-01-01), and one anomalous data
    gap on **2025-09-09** (that day's 5-minute file does not start until
    12:55 PM ET — confirmed by direct read, not inferred).
  - **4 additional weekday dates with a partial window** (65/66, 12/66,
    64/66, 63/66 expected 5-minute bars present): 2025-01-10, 2025-11-28,
    2025-12-05, 2026-04-03.
  - Every other weekday (514 of 520 weekdays in `trading_days(2024-07-01,
    2026-06-26)`) has full or materially-full true 5-minute pre-market
    coverage.

`research/bars_12hr_miyagi_loader.py::load_5m_premarket_window()` therefore
uses **true 5-minute bars as the primary evidence path** whenever a date's
premarket window has at least 60 of the expected 66 bars, and falls back to
the 15-minute-cache proxy described in the original brief (mathematically
proven conservative in one direction: a 15-minute bar's own high/low not
both breaching Bar C's range implies no 5-minute sub-bar within it could
have either) only for the handful of dates above. Every date's provenance
(`"5m"` vs `"15m_proxy"`) is recorded and reported, not silently resolved.

**Result across the full candidate scan (both instruments, full study
range):** 7 of 520 weekdays used the proxy for either instrument (the 6
fully-missing + `2025-11-28`, the one partial date whose 12/66 count fell
below the 60-bar completeness threshold; the other three partial dates
had ≥60 of 66 and used true 5-minute data). **Zero of those 7 proxy days
coincide with an actual 1-3-1 candidate pattern for either instrument, and
zero of them flagged a Step-5 breach at 15-minute granularity** — so the
`AMBIGUOUS_GRANULARITY` count for this study is **0/0** (MNQ/MES). This is
reported, not assumed: every one of the 15 MNQ and 19 MES real candidates
resolved Step 5 against true 5-minute data (`premarket_provenance: "5m"` on
every row — see `docs/strategy-rules/evidence_12hr_miyagi/{mnq,mes}_results.json`
→ `candidates[].premarket_provenance` and `granularity_ambiguity`).

---

## 2. Resampler verification

`load_12h_bars_for_date()` and `load_60m_bars_for_date()` build 12-hour
(4AM/4PM ET-anchored) and 60-minute bars from the 15-minute cache (which
covers the full day including pre-market, unlike the RTH-anchored 5-minute
cache's early-history truncation). Both were manually cross-checked against
raw JSONL rows before being trusted at scale:

- **2024-09-05** (a normal weekday): Bar D's 12h bucket (`2024-09-05 04:00
  ET`) resampled to 48 sub-bars (exactly 12h × 4 bars/hour — a fully
  populated bucket). Bar C's bucket (`2024-09-04 16:00 ET`) resampled to 44
  sub-bars; a hand-scan of the raw `MNQ_2024-09-04.jsonl` /
  `MNQ_2024-09-05.jsonl` rows in that exact window independently found the
  same 44 timestamps present (4 bars missing at 17:00-17:45 ET — the CME
  daily maintenance halt) — the resampler is faithfully reproducing gaps in
  the source, not introducing its own. The 8-9 AM ET 60-minute stop
  reference bar for that date was independently recomputed by hand from the
  four raw 15-minute rows (open=18916.25, high=18942.0, low=18828.0,
  close=18889.75) and matched the resampler's output exactly.
- **2024-11-03** (fall-back DST transition Sunday) and the following Monday
  **2024-11-04**: the 12h bucket keyed `2024-11-03 16:00-05:00` (post-DST
  offset) correctly reflects the UTC-offset change, and its 40/48 sub-bar
  count matches the expected Sunday-evening-open gap (Globex Sunday open
  ~6 PM ET), not a resampler artifact.

---

## 3. Detector reconciliation

No dated manual-sample ground truth exists for Miyagi anywhere in this
repository's git history (same situation `research/reconcile_322_first_live.py`
documents for 3-2-2 — and 3-2-2 at least had a documented n=32 external
signal *count* to spot-check against; Miyagi doesn't even have that).
`research/reconcile_12hr_miyagi.py` therefore leans on two independent
evidence sources:

### 3.1 Synthetic fixture suite — 16/16 passed

Every branch of `docs/strategy-rules/Detector_Specifications.md` Detector 2
is exercised: valid SHORT (2U) and LONG (2D) signals; each of Bar
A/B/C/D/Z missing; Bar C not inside Bar B; Bar B not outside Bar A; Bar A
not inside Bar Z; the Candle-3-becomes-outside-bar invalidation; price
exactly equal to Bar C's high and to its low at 9:30 AM (both the ambiguous
edge case); price between Bar C's bounds at 9:30 AM; missing 9:30 AM bar;
missing 60-minute stop-reference data. **All 16 pass.**

### 3.2 Real-data spot-checks — 5/5 passed

Five dates, hand-verified against raw `data/replay_polygon{,_5m}` JSONL rows
independently of the detector before being asserted against it:

| Instrument | Date | Hand-verified finding | Detector agrees |
|---|---|---|---|
| MNQ | 2024-08-22 | Valid 1-3-1: Z[19743.00,19924.75] A[19785.75,19855.75]⊂Z, B[19771.25,19982.00]⊃A, C[19865.75,19946.00]⊂B; trigger=19905.875 | ✅ SHORT, trigger 19905.875 |
| MNQ | 2024-10-11 | Valid 1-3-1: Z[20208.75,20482.25] A[20413.00,20474.50]⊂Z, B[20301.25,20508.00]⊃A, C[20372.50,20468.50]⊂B; trigger=20420.5 | ✅ LONG, trigger 20420.5 |
| MNQ | 2025-02-12 | Structurally valid 1-3-1, but the true (non-proxy) 08:30 ET bar has high=21848.00 > C.high(21831.25) **and** low=21542.75 < C.low(21766.00) — a genuine single-bar engulf, read directly off the raw row | ✅ `CANDLE3_BECAME_OUTSIDE_BAR` |
| MES | 2024-07-12 | Valid 1-3-1 (2nd calendar day of the entire 5m cache's coverage — also confirms earliest-usable-date behavior): Z[5634.75,5690.50] A[5679.00,5687.25]⊂Z, B[5629.75,5707.75]⊃A, C[5632.50,5644.00]⊂B; trigger=5638.25 | ✅ SHORT, trigger 5638.25 |
| MNQ | 2024-07-10 | **No pattern**: Bar A=[20655.50,20748.75] is NOT inside Bar Z=[20588.00,20688.25] (A.high 20748.75 > Z.high 20688.25) — fails at Step 3 | ✅ `None` |

**Overall reconciliation: 21/21 checks passed (16 synthetic + 5 real-data).**
Full detail: `tests/test_reconcile_12hr_miyagi.py`,
`research/reconcile_12hr_miyagi.py`.

---

## 4. Historical coverage

Both instruments' 15-minute cache: `2024-07-01` .. `2026-06-26` (622 daily
files). Both instruments' 5-minute cache: `2024-07-02` .. `2026-06-26` (621
daily files). Study range used for candidate detection and honest-fill
replay, matching the 3-2-2 precedent's baseline window for direct
comparability: **`2024-07-02` through `2026-06-26`** (520 weekdays).

- **MNQ: 15 candidates**, 1 explicit invalidation (`CANDLE3_BECAME_OUTSIDE_BAR`,
  2025-02-12).
- **MES: 19 candidates**, 0 explicit invalidations.
- This is thinner than the rules doc's "approximately 1-2 times per month"
  estimate (15/19 candidates over ~24 months ≈ 0.6-0.8/month) — expected
  variance for a strict 4-bar structural pattern, not a detector defect (the
  reconciliation suite above independently confirms the detector isn't
  under- or over-firing relative to the spec).

---

## 5. Fill model, exit contract, and cost assumptions

- **Tick size**: 0.25 for both instruments (`execution/paper_broker.py`
  `TICK_SIZE`).
- **Point value**: MNQ $2.00/point (0.25 tick × `TICK_VALUE["MNQ"]`=$0.50),
  MES $5.00/point (0.25 tick × `TICK_VALUE["MES"]`=$1.25) — both verified
  directly against `execution/paper_broker.py`.
- **Round-trip commission**: $1.24, matching the immediate 3-2-2 precedent's
  `ROUND_TRIP_COMMISSION` in `research/replay_322_honest_fill.py`. Two other
  files in this repo (`execution/mnq_strat_evidence.py`,
  `execution/mes_trend_consolidation_break_evidence.py`) use $1.48 instead —
  a pre-existing inconsistency in the codebase, not resolved here; $1.24 was
  used because it matches the structurally-closest precedent this lane
  mirrors.
- **Entry fill model — deliberately simpler than 3-2-2's IOC-with-limit
  model**: fills at the exact trigger price plus adverse slippage, whenever
  a 5-minute bar's range crosses the trigger, with **no IOC tolerance and no
  cancellation for gapping too far**. `12HR_Miyagi_Rules.md` §12/§15
  explicitly states "no 50% breach rule applies to Miyagi" / "Do NOT apply
  the 50% breach rule to Miyagi," and documents no IOC tolerance or limit
  cap anywhere. Per the task brief's explicit fallback instruction for this
  exact situation, a plain trigger-price-plus-slippage fill was used instead
  of inventing IOC/limit machinery the rules doc doesn't specify. This is
  also structurally simpler than it first appears: the detector's own Step 6
  guarantees price is strictly beyond Bar C's boundary (farther from the
  trigger than Bar C's own edge) at 9:30 AM, so the "gap-through-the-trigger
  at the very first bar" scenario the 3-2-2 precedent's gap-open branch
  exists to handle is structurally impossible for Miyagi's entry window.
- **No-same-bar-resolves-own-bracket**: the bar whose trigger-touch fills
  the entry is excluded from that trade's own stop/T1 resolution (matches
  the 3-2-2 precedent's stated honest-fill principle). The sole documented
  exception: the day-only-exit contract's own text ("Stop/target resolution
  has precedence over the day-only exit on that bar") is honored literally
  when entry happens to fill on the exact 15:55-16:00 ET bar itself — a
  data-availability edge case that never actually occurred in this study's
  34 real candidates (all entries filled well before 15:55 on every date
  that filled at all).
- **Day-only exit contract**: reused faithfully from `12HR_Miyagi_Rules.md`
  §8, which states the identical contract to `60M_322_FirstLive_Rules.md` /
  PR #318 (main@14e2af2): resolve stop/T1 first on the 15:55-16:00 ET bar if
  reached; otherwise flatten at that bar's close, reason `DAY_ONLY_FLATTEN`;
  if that exact bar is missing, record `EOD_BAR_MISSING` — unresolved, no
  price estimate, no WIN/LOSS/BREAKEVEN counted. **0 `EOD_BAR_MISSING`
  occurrences in this study** (both instruments).
- **Single-contract, T1-only scope**: `12HR_Miyagi_Rules.md` §8 and hard
  rule #15 pin the only currently-validated management mode to "1 contract,
  100% exit at T1." This replay resolves every trade against the fixed stop
  and T1 only; `target_2` (T2) is carried through every trade row for
  transparency but never used to resolve an exit — using T2 here would
  violate an explicit hard rule, not just be an unbuilt feature.
- **Stop-wrong-side fail-closed check**: applied identically to the 3-2-2
  precedent (0 occurrences in this study).

---

## 6. MNQ — full metrics (base case, 2-tick slippage)

| Metric | Value |
|---|---|
| Candidates | 15 |
| Fills | 8 (fill rate 53.3%) |
| No-fill (`TRIGGER_NOT_HIT`) | 7 |
| `EOD_BAR_MISSING` | 0 |
| Resolved fills | 8 |
| Wins / Losses | 7 / 1 |
| Win rate (of resolved fills) | 87.5% |
| Gross P&L | $542.25 |
| Slippage cost | $16.00 |
| Commission | $9.92 |
| Net P&L | **$516.33** |
| Expectancy / signal | $34.42 |
| Expectancy / resolved fill | $64.54 |
| Profit factor | 2.81 |
| Avg win / avg loss | $114.51 / -$285.24 |
| Largest win / largest loss | $228.51 / -$285.24 |
| Max consecutive wins / losses | 2 / 1 |
| Max drawdown | $285.24 (single trade — the one STOP) |
| Top-1 / Top-3 / Top-5 share of gross profit | 28.5% / 58.9% / 83.6% (of 7 total wins) |

**H1/H2** (midpoint `2025-07-01`): H1 n=11, 7 fills, 7 resolved, 6W-1L, net
**$382.82**, PF 2.34. H2 n=4, 1 fill, 1 resolved, 1W-0L, net **$133.51**, PF
undefined (no losses). **H2 is a single trade — not a meaningful
chronological-robustness check on its own.**

**LONG/SHORT**: LONG n=7, 2 fills, 2 resolved, 2W-0L, net **$203.02**, PF
undefined. SHORT n=8, 6 fills, 6 resolved, 5W-1L, net **$313.31**, PF 2.10.
**LONG's 2-fill sample is far too thin to call independently viable — it is
simply small and undefeated so far.**

**By year**: 2024 (Jul-Dec) net **-$60.96** (4 fills, 3W-1L). 2025 net
**+$443.78** (3 fills, 3W-0L). 2026 (partial, through Jun) net **+$133.51**
(1 fill, 1W-0L). **2024 was net negative; all of MNQ's positive result comes
from 2025-2026.**

---

## 7. MES — full metrics (base case, 2-tick slippage)

| Metric | Value |
|---|---|
| Candidates | 19 |
| Fills | 10 (fill rate 52.6%) |
| No-fill (`TRIGGER_NOT_HIT`) | 9 |
| `EOD_BAR_MISSING` | 0 |
| Resolved fills | 10 |
| Wins / Losses | 8 / 2 |
| Win rate (of resolved fills) | 80.0% |
| Gross P&L | $261.25 |
| Slippage cost | $50.00 |
| Commission | $12.40 |
| Net P&L | **$198.85** |
| Expectancy / signal | $10.47 |
| Expectancy / resolved fill | $19.88 |
| Profit factor | 1.98 |
| Avg win / avg loss | $50.32 / -$101.87 |
| Largest win / largest loss | $98.76 / -$141.87 |
| Max consecutive wins / losses | 3 / 1 |
| Max drawdown | $141.87 |
| Top-1 / Top-3 / Top-5 share of gross profit | 24.5% / 62.3% / 80.6% (of 8 total wins) |

**H1/H2**: H1 n=12, 6 fills, 6 resolved, 5W-1L, net **$131.94**, PF 1.93.
H2 n=7, 4 fills, 4 resolved, 3W-1L, net **$66.92**, PF 2.08. Both halves
positive, though still thin (10 resolved fills total split across both).

**LONG/SHORT**: LONG n=10, 3 fills, 3 resolved, 3W-0L, net **$204.41**, PF
undefined (no losses). SHORT n=9, 7 fills, 7 resolved, 5W-2L, net
**-$5.56**, PF 0.97. **SHORT is net slightly negative on its own — MES's
entire positive aggregate result is carried by LONG.** This is the most
material robustness concern in this study (Robustness Question 4, below).

**By year**: 2024 net **-$72.47** (3 fills, 2W-1L). 2025 net **+$171.93**
(5 fills, 4W-1L). 2026 (partial) net **+$99.40** (2 fills, 2W-0L). Same
pattern as MNQ: 2024 negative, 2025-2026 positive.

---

## 8. Slippage sensitivity (1/2/3/4-tick adverse)

| Ticks | MNQ net | MNQ PF | MES net | MES PF |
|---|---|---|---|---|
| 1 | $524.33 | 2.84 | $223.85 | 2.13 |
| 2 (base) | $516.33 | 2.81 | $198.85 | 1.98 |
| 3 | $508.33 | 2.78 | $173.85 | 1.83 |
| 4 | $500.33 | 2.74 | $148.85 | 1.70 |

Both instruments stay net-positive and PF > 1.6 across the full 1-4 tick
sweep — the resolved/win/loss counts do not change at all across the sweep
(no trade flips outcome from added slippage), only P&L magnitude shifts.
**Both survive realistic adverse slippage.**

---

## 9. Robustness questions

1. **Does MNQ remain positive under honest fills?** Yes — net $516.33, PF
   2.81, 7W-1L across 8 resolved fills.
2. **Does MES remain positive under honest fills?** Yes — net $198.85, PF
   1.98, 8W-2L across 10 resolved fills.
3. **Are both chronological halves positive?** MNQ: yes nominally (H1
   $382.82/7 fills, H2 $133.51/1 fill) but H2's single trade is not a
   meaningful robustness check. MES: yes, and more evenly (H1 $131.94/6
   fills, H2 $66.92/4 fills) — a more credible split than MNQ's.
4. **Are LONG and SHORT independently viable?** MNQ: both nominally
   positive, but LONG's 2-fill sample is too thin to call viable on its own
   (undefeated, not proven). MES: **no** — SHORT is net -$5.56 (5W-2L, PF
   0.97) on its own; MES's entire positive result is carried by LONG (3W-0L,
   $204.41). This is the single most material finding against either
   instrument's robustness.
5. **Is profitability concentrated in a few trades?** Yes, materially, for
   both — expected at this sample size (top-5 of 7 MNQ wins = 83.6% of
   gross profit; top-5 of 8 MES wins = 80.6%). With only 7-8 total winners,
   "top 5" is most of the sample, not evidence of unusual concentration
   beyond what a small-n study always shows.
6. **Is profitability concentrated in one short time period?** Partially —
   both instruments were net negative in 2024 (the first ~6 months of
   coverage) and net positive in 2025-2026. This is a real, not cosmetic,
   temporal concentration: neither instrument's positive result predates
   2025.
7. **Does the edge survive realistic adverse slippage?** Yes for both,
   comfortably (§8) — PF stays above 1.6 (MES) / 2.7 (MNQ) through 4 ticks.
8. **Is drawdown controlled relative to expectancy?** MNQ: max DD $285.24 vs
   expectancy/fill $64.54 (≈4.4x) — the drawdown is a single STOP trade, not
   a losing streak (max consecutive losses = 1). MES: max DD $141.87 vs
   expectancy/fill $19.88 (≈7.1x) — also a single trade, also max
   consecutive losses = 1. Both ratios are elevated mainly because
   expectancy/fill is small on an 8-10 trade sample, not because the
   drawdown itself is unusually large in absolute terms.
9. **Do results materially differ from the old manual-study figures (MNQ
   +$102.35, MES +$25.78, n=13/20)?** Per-trade expectancy is in the same
   rough order of magnitude for MES ($19.88/fill here vs $25.78 documented)
   but MNQ's honest-fill expectancy/fill ($64.54) is materially lower than
   the documented $102.35, and both this study's sample composition (8/10
   resolved fills, entirely different date set — this study spans
   2024-07-02..2026-06-26; the external study's dates are not documented)
   and candidate counts (15/19 candidates here) cannot be mapped onto the
   external study's n=13/n=20 at all, since no dated manual-sample CSV
   exists to compare against directly.
10. **If they differ, is the cause detector interpretation, fill realism,
    exit semantics, data coverage, cost assumptions, or an actual defect in
    the old evidence?** Cannot be fully determined — the external study's
    exact dates, fill assumptions, and whether it used a literal-vs-ratchet
    stop are not reproducible from anything committed to this repository.
    What can be said with confidence: this study's honest-fill model (real
    slippage, real $1.24 commission, T1-only single-contract exit, the
    day-only-flatten contract, and a strict causal no-same-bar-resolves-
    own-bracket rule) is a materially more conservative fill/cost model than
    a manual/idealized study is likely to have used, which alone would
    explain a lower expectancy/fill without implying either study is wrong.
    No reproduction of the old numbers was attempted or expected (per the
    task brief and the rules doc's own §9 caveat).

**Sample-size caveat (binding, not optional):** per the operator's standing
instruction, a strong result with a thin sample stays PROMISING BUT
UNPROVEN, never rounds up to VALIDATED. This study's samples (8 MNQ
resolved fills, 10 MES resolved fills) are **thinner than the 60M 3-2-2
precedent's own already-flagged-thin 20 resolved fills** — Miyagi's 1-3-1
structural pattern combined with a ~53% no-fill rate (over half of all
detected candidates never had the trigger touched at all) makes for a
genuinely small evidence base even after 2 years of history. This should be
read as directionally encouraging, not as proof.

---

## 10. Classification

### MNQ: PROMISING BUT UNPROVEN

**For:** net positive ($516.33, PF 2.81), survives 1-4 tick slippage without
flipping sign or losing more than ~13% of net P&L, single isolated drawdown
(not a losing streak), 0 `EOD_BAR_MISSING`/data-parity issues, detector
independently reconciled against both synthetic coverage and hand-verified
real dates.

**Against VALIDATED:** only 8 resolved fills total; H2 (the second
chronological half) is a single trade, not a genuine robustness check; LONG
direction has only 2 fills; the entire positive result is concentrated in
2025-2026, with 2024 net negative. None of VALIDATED's "materially adequate
sample size" or "evidence across more than one favorable period" bars are
cleared.

### MES: PROMISING BUT UNPROVEN

**For:** net positive ($198.85, PF 1.98), both chronological halves
individually positive with a more even split than MNQ, survives 1-4 tick
slippage, 0 `EOD_BAR_MISSING`/data-parity issues, same reconciliation
evidence as MNQ (spot-checked jointly).

**Against VALIDATED:** only 10 resolved fills; **SHORT is net negative on
its own** (-$5.56, PF 0.97) — the entire positive aggregate is carried by
LONG's undefeated 3-fill sample, which is itself too thin to be reassuring;
same 2024-negative / 2025-2026-positive temporal concentration as MNQ.

Neither instrument clears VALIDATED's bar (materially adequate sample,
temporal robustness across genuinely multiple favorable periods, no
unacceptable winner/direction concentration). Both are honestly thinner,
directionally-mixed-at-the-margin evidence bases than the 60M 3-2-2
precedent's own PROMISING BUT UNPROVEN verdict, which itself was explicitly
not rounded up to VALIDATED on a larger (20 resolved fill) sample. No new
strategies/gates/runtime changes are authorized by this result, consistent
with the standing evidence-phase directive.

---

## 11. Reproduction

Requires Python 3.11+ (developed/tested on 3.13) and this repo's
`requirements.txt`/`requirements-dev.txt`. The historical study additionally
requires the local, gitignored `data/replay_polygon/` and
`data/replay_polygon_5m/` bar caches (not present in a fresh clone or CI —
see `docs/strategy-rules/project_polygon_data_source` context in memory).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# unit tests (no data cache required)
python3 -m pytest tests/test_detector_12hr_miyagi.py \
  tests/test_bars_12hr_miyagi_loader.py \
  tests/test_reconcile_12hr_miyagi.py \
  tests/test_replay_12hr_miyagi_honest_fill.py -v

# full historical study (requires data/replay_polygon{,_5m}/)
python3 -m research.run_12hr_miyagi_evidence
```

---

## 12. File manifest

- `research/detector_12hr_miyagi.py` — pure detector (canonical spec, verbatim).
- `research/bars_12hr_miyagi_loader.py` — 12H/60M/5M loaders + resamplers,
  including the true-5m/15m-proxy fallback for Step 5.
- `research/reconcile_12hr_miyagi.py` — synthetic + real-data reconciliation gate.
- `research/replay_12hr_miyagi_honest_fill.py` — honest-fill replay engine.
- `research/run_12hr_miyagi_evidence.py` — full-study driver.
- `tests/test_detector_12hr_miyagi.py`, `tests/test_bars_12hr_miyagi_loader.py`,
  `tests/test_reconcile_12hr_miyagi.py`, `tests/test_replay_12hr_miyagi_honest_fill.py`.
- `docs/strategy-rules/evidence_12hr_miyagi/mnq_results.json`,
  `docs/strategy-rules/evidence_12hr_miyagi/mes_results.json` — full evidence
  artifacts (candidates, invalidations, granularity-ambiguity instrumentation,
  base-case replay, 1-4 tick slippage sensitivity, by-month/by-year breakdowns).
- `docs/strategy-rules/Strategy_Inventory.md` — "12HR Miyagi" row and profile
  updated to reflect this study.
