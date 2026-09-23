# Strat Setups on 60m / 4H / Daily (Magnitude + FTFC, Intrabar Entry), MNQ/MES — Preregistration (2026-09-23)

**RESEARCH / AUDIT ONLY. NO EXECUTION AUTHORITY.** Nothing here enables,
disables or re-parameterizes any strategy, lane, risk rule, broker path,
observer population or deployment. The runtime stays PAPER / OBSERVE only, and
`LIVE_TRADING_ENABLED=false` is out of scope. The running observer epoch, the
Daily 2-2 paper lane, the 60m 3-2-2 first-live lane and the 4HR re-trigger lane
are **not modified**.

## 1. Why this exists

The 15m test of TheStrat's source rules (#934, results #936) cleared nothing.
The magnitude target never beat a fixed 2R. FTFC helped but not enough. Every
15m Strat study says the same thing: patterns on 15m bars are too small to
cover costs.

TheStrat is taught on higher timeframes. There the magnitude (a prior bar's
extreme) is large relative to the fixed round-trip cost of about $1.48 plus 2
ticks, and the entry is **intrabar, on the break of the trigger**, not at the
close of the bar that completes the pattern. Neither of those has been tested
as a family on this repo's data.

**The question:** on 60m, 4H and daily bars, with an intrabar stop entry, do
the Strat setups have an edge with the magnitude target and/or the FTFC
filter, beyond what a direction-flipped null produces?

## 2. What we already know (frozen at registration; disclosed, not scored)

- **#934 / #936 (15m, completed-bar entry):** DOES NOT CLEAR, all 18 cells.
  - The best cell was 2-2 reversal with the FTFC filter: PF 1.24, n=290.
  - The magnitude target lost to 2R. Hammer/shooter PF 0.52.
  - About 70% of signals occurred in FTFC conflict, where most losses sat.
- **15m history:** the 07-16 tranche 2 (#287), the 07-31 shadow null and the
  09-21 grid all REJECT the 15m Strat families.
- **Daily 2-2 (completed-close entry, `context/daily_22_swing_collector.py`):**
  34 trades over 621 days, PF 2.02, both halves positive. That is above the
  1.94 single-test null but below 2.55, with n=34. It is a paper lane:
  "strongest active lane", edge unproven.
- **60m 3-2-2 first-live:** 20 fills, PF 8.00 (tiny n). Demo lane.
- **4HR re-trigger:** a different pattern (a 1H structure reclaim). Out of
  scope.
- **HTF direction gating, 2026-07-02 (#140):** gating trades on *payload
  daily/4H trend labels* was strongly negative across 622 days. Those labels
  were lagging trend proxies, **not** Strat FTFC (price vs the M/W/D/60m
  opens). Disclosed because it is the closest prior "higher-timeframe filter"
  result, and it was negative.

**Prior-exposed cells.** Two cells overlap lanes whose results were seen before
registration: **daily 2-2 reversal** and **60m 3-2-2 reversal**. Both are
scored and reported. **Neither can be labeled PASS**; the best either can
reach is PROMISING / FORWARD-ONLY. The rules here differ from those lanes (an
intrabar stop entry, not a completed-close entry or context gate), so this is
not a re-run of them.

## 3. Data

- **Source:** 15m Polygon continuous bars, concatenated in this order of
  precedence:
  1. `data/replay_corpus_v1_market_condition_fixed/{MNQ,MES}`
     (2025-07-24 → 2026-07-23), the canonical corpus;
  2. `data/replay_polygon_v2/{MNQ,MES}` (2024-10-01 → 2026-06-26);
  3. `data/replay_polygon_parity_2026_07_16_09_22/{MNQ,MES}`
     (2026-07-16 → 2026-09-22), admitted in #934.
- **Overlap, measured before registration (no outcomes computed):**
  - v2 vs the canonical corpus: **21,790 / 21,793 bars OHLC-identical** (MNQ
    and MES each). The canonical corpus wins on the 3 differing bars, which
    the results will list.
  - The extension overlap was verified identical in #934.
- **History limit:** the paid Polygon plan returned **no futures bars before
  2024-10** when probed on 2026-09-23 (2023-01, 2024-01, 2024-05 and 2024-07
  each returned 0 bars). No earlier data is available. The legacy
  `data/replay_polygon` (2024-07 →) is not used: it has no manifest or roll
  ledger.
- **Rolls:** seams come from the committed manifests: v2 (2024-12-12,
  2025-03-13, 2025-06-12, 2025-09-11, 2025-12-11, 2026-03-12, 2026-06-11) plus
  the extension (2026-09-15). The #934 roll rules apply (§6).
- **Windows** (by the **entry** trading date):
  - **Main:** 2024-11-01 → 2026-07-23. November 2024 is the first month whose
    monthly open is inside the data; Oct 2024's first session is truncated.
  - **Halves:** H1 < 2025-09-12 ≤ H2 (the calendar midpoint of the main
    window).
  - **OOS:** 2026-07-24 → 2026-09-21. Scored separately and never pooled.
- **Instruments:** MNQ primary; MES replication only. Gold, crude, M2K and MBT
  are out of scope (no canonical corpus).

## 4. Higher-timeframe bars (built from 15m, causally)

The CME trading day runs 18:00 ET → 17:00 ET. Weekends and the daily
17:00–18:00 break are not gaps.

| TF | Buckets (ET) | Complete when |
|---|---|---|
| **60m** | each clock hour inside the session (18:00, 19:00, … 16:00) | all four 15m bars present |
| **4H** | session-anchored: 18:00, 22:00, 02:00, 06:00, 10:00, 14:00 (the last is 3h, 14:00–17:00) | every expected 15m bar present |
| **Daily** | one trading day, 18:00 → 17:00 (or the early close) | the first bar is 18:00 ET and there is no missing 15m bar inside the session |

- 4H buckets follow TradingView's session-anchored CME 4H, which is what
  TheStrat charts show. The repo's 4HR lane uses ET wall-clock buckets
  (`strategy/four_hr_retrigger.et_bucket_start`: 00/04/08/…). That is
  disclosed and **not** used here.
- An incomplete HTF bar may not be used as any setup bar. A pattern touching
  one is skipped as `GAP`.
- On holiday early closes, the trading day's last HTF bar is whatever the
  session contains, and it counts as complete if it has no internal gap.

## 5. Setups, triggers, stops, targets

Bar types use `strategy.strat_classifier.classify_bar` on consecutive HTF
bars. Let `t-1` be the **last completed** HTF bar. The trigger is armed at
`t-1`'s close and is live for the whole of the next HTF bar `t`.

| Setup (LONG shown) | Armed when | Trigger (stop-entry) | Stop | Magnitude (target, arm M) |
|---|---|---|---|---|
| **2-2 reversal** | `t-1`=2d, `t-2` ∈ {2u, 2d} | `t-1` high + 1 tick | `t-1` low − 1 tick | `t-2` high |
| **3-2-2 reversal** | `t-1`=2d, `t-2`=3 | `t-1` high + 1 tick | `t-1` low − 1 tick | `t-2` high |
| **1-2-2 rev-strat** | `t-1`=2d, `t-2`=1 | `t-1` high + 1 tick | `t-1` low − 1 tick | `t-3` high (motherbar) |
| **2-1-2 reversal** | `t-1`=1, `t-2`=2d | `t-1` high + 1 tick | `t-1` low − 1 tick | `t-2` high |
| **3-1-2** (both sides) | `t-1`=1, `t-2`=3 | `t-1` high + 1 tick (or low − 1 tick for SHORT) | opposite side of `t-1` ± 1 tick | `t-2` high (low) |
| **2-2 continuation** | `t-1`=2u | `t-1` high + 1 tick | `t-1` low − 1 tick | *none* (2R arms only) |
| **Hammer at a level** (shooter mirrored) | `t-1` is a hammer (open and close both in the top 33% of its range) **and** `t-1` low ≤ a level from the table below | `t-1` high + 1 tick | `t-1` low − 1 tick | `t-2` high |

**Hammer levels** are fixed per TF, all from completed prior periods, with a
tolerance of 0 ticks. The hammer must trade at or through the level:

| TF | LONG levels (low ≤ any of) | SHORT levels (high ≥ any of) |
|---|---|---|
| 60m, 4H | prior trading day low, prior week low | prior trading day high, prior week high |
| Daily | prior week low, prior month low | prior week high, prior month high |

Prior day, week and month use the §4 trading-day calendar and the §6 seam
back-adjustment.

SHORT is mirrored.

- **Precedence** is the same as `classify_sequence`: 2-2 reversal excludes 3-
  and 1-openers.
- When `t-1` is an inside bar, both sides are armed. For 2-1-2, only the
  reversal side is in scope; the continuation side of a 2-1-2 is not
  scored.
- **The first side to trigger wins, and the other is cancelled.** If one 15m
  bar crosses both trigger levels, the setup is skipped as `AMBIGUOUS`.
- `t-1` = 3 arms nothing (3-2 has no magnitude).
- **Stop:** one tick beyond the trigger bar `t-1`. This is the only Strat stop
  that is known at the moment of an intrabar entry. The #934 "beyond the
  entry bar" stop cannot be known until `t` closes.
- `MAGNITUDE_INVALID`: the magnitude is not at least 1 tick beyond the trigger
  price at arming. It is counted and not taken.

**Hammer/shooter** was PF 0.52 at 15m with no confluence (#936). It is
included here only in the level-plus-FTFC form above, added on the operator's
instruction on 2026-09-23, before any script or result exists.

**Excluded:** 1-3, 3-2, 2-2-2, 1-3-2,
measured moves, PMG/kicker/IOI (the reasons are as in #934 §4), and the
continuation side of 2-1-2 and 1-2-2.

## 6. Arms, fills, exits

**Arms (fixed):**

| Arm | Target | Filter |
|---|---|---|
| **R** (baseline) | entry + 2 × (entry − stop), from the trigger price | none |
| **R+F** | 2R | FTFC |
| **M** | magnitude (§5) | none |
| **M+F** | magnitude | FTFC |

- 2-2 continuation has R and R+F only.
- **Hammer at a level always carries the FTFC filter.** FTFC is part of its
  confluence, so it has **M+F** and **R+F** only. Its baseline for item 6 of
  §7 is its own R+F arm, so only **M+F** is judged against a baseline; R+F is
  judged on items 1–5.

**FTFC** is evaluated at the trigger level, at the start of the 15m bar that
triggers. With the opens as defined in #934 §5 (the month, week and day opens
of the CME trading day, and the 60m open of the ET clock hour, all taken from
15m bars and back-adjusted across seams):

- LONG requires the trigger price to be > all four opens;
- SHORT requires it to be < all four;
- anything else is conflict, and the signal is not taken;
- a missing open means `UNKNOWN`. Every arm is scored only on signals where
  FTFC is known, so all arms share one denominator.

**Entry fill:** a stop-market order at the trigger, simulated on the 15m bars
inside `t`, reusing `PaperBroker(entry_fill_model="stop_market")` semantics
one 15m bar at a time:

- if a 15m bar **opens** through the trigger, the fill is that open plus 1
  adverse tick;
- otherwise, if the bar's range reaches the trigger, the fill is the trigger
  plus 1 adverse tick;
- if no 15m bar inside `t` reaches the trigger, the setup expires as
  `NOT_TRIGGERED`.

The broker's bracket-validity check applies at the fill: a gap fill beyond
the target or stop is cancelled.

**Pessimistic fill-bar rule:** if the 15m bar that fills also reaches the stop,
the trade is a LOSS at the stop minus 1 tick. The target is never credited on
the fill bar.

**After the fill:** `PaperBroker` static-bracket resolution on each later 15m
bar, with `pessimistic_both_hit=True` and gap-through stops priced from the
bar's open. 1 tick of slippage, $1.48 round-turn commission, 1 contract.

**Time exit** (at the close of the stated 15m bar, 1 adverse tick):

| TF | Exit if neither stop nor target is hit |
|---|---|
| 60m | the last bar of the entry trading day (`SESSION_END`) |
| 4H | the last bar of the trading day **after** the entry trading day (≤ 2 sessions) |
| Daily | the last bar of the **5th** trading day after the entry trading day |

Positions may be held across the daily break and weekends; gaps are priced as
above.

**Rolls:**
- A setup whose bars `t-3` … `t` contain a seam is skipped as `ROLL`.
- An open position at a seam is closed at the last pre-seam 15m close
  (`ROLL_EXIT`, terminal).
- FTFC opens are back-adjusted by the seam gap.

**Position rules** per cell (setup × arm × TF × instrument): one position at a
time; signals while one is open are `BUSY`. For 60m only, at most 3 fills per
trading day.

## 7. Hypotheses and gates

**Candidate cells (MNQ):**
- per TF: the five magnitude setups × {M, M+F, R+F}, plus 2-2 continuation ×
  {R+F}, plus hammer-at-a-level × {M+F, R+F}, which is 18 per TF;
- **54 in total.**

Arm R is the baseline and cannot pass.

**Minimum terminal trades (main window):** 60m ≥ 60; 4H ≥ 40; daily ≥ 25.

**Family-wise null (computed in the same run, before gates are read):** for
each TF, 500 replications with seed `20260923`:

1. Every *filled* trade of every candidate cell keeps its actual fill time and
   price, but its direction is flipped with probability 0.5.
2. The stop and target are mirrored around the fill at the same distances.
3. The trade is resolved on the real bars under the same exit rules. Null
   trades are resolved independently, with no BUSY interaction.

In each replication, record the **maximum PF** over that TF's candidate cells
that meet the minimum trade count. `T_tf` is the 95th percentile of those 500
maxima.

**Q1 (per candidate cell, MNQ main window).** **PASS** only if all hold:

1. terminal trades ≥ the TF minimum;
2. PF ≥ **max(T_tf, 1.94)**;
3. H1 net > 0 **and** H2 net > 0;
4. net after removing the single best trading day > 0;
5. the top 3 trading days are < 50% of net;
6. PF > the same setup's arm-R PF (for the 2-2 continuation R+F cell, versus
   continuation R). For hammer-at-a-level: M+F must beat its R+F; R+F is N/A
   on this item.

**PROMISING / FORWARD-ONLY:** PF ≥ 1.94 but below `T_tf`, with 1 and 3–6 met.
**DOES NOT CLEAR:** everything else.

- **Daily cells can be PROMISING at most**, never PASS: at n ≈ 25–60 the PF is
  too noisy for a pass.
- **Prior-exposed cells** (daily 2-2 reversal, 60m 3-2-2 reversal) can be
  PROMISING at most (§2).

**Q2 (replication).** Every PASS/PROMISING MNQ cell is reported on MES:
REPLICATED if MES net > 0 and PF > 1.0. MES never creates a pass.

**Q2b (OOS, 2026-07-24 → 09-21).** For PASS/PROMISING cells:

| Label | Condition (MNQ OOS) |
|---|---|
| OOS CONFIRMED | ≥ 10 trades on 60m (≥ 5 on 4H), net > 0, PF > 1.0 |
| OOS CONTRADICTED | net < 0 at those counts |
| OOS INSUFFICIENT | otherwise; always the case for daily, since the window has about 40 daily bars |

**Q3 (descriptive, no rule).** For arms R and M of every setup and TF: P&L by
FTFC state at the trigger (aligned / against / conflict), and by the corpus
`market_condition` of the 15m fill bar. No range rule is adopted.

## 8. Integrity checks (run before scoring; any failure stops the study)

1. **Aggregation:**
   - 60m and 4H highs/lows equal the max/min of their 15m bars, verified on the
     whole corpus.
   - The 4H bucket boundaries fall on 18/22/02/06/10/14 ET.
   - Daily bars equal the Daily 2-2 lane's `_daily_sessions` aggregation
     (`context/daily_22_swing_collector.py`) run on the same 15m bars: ≥ 99%
     OHLC identity on complete days, with mismatches listed.
2. **Causality:**
   - Unit test: triggers are armed only from completed HTF bars.
   - Unit test: fill scanning uses only 15m bars inside `t`.
   - Unit test: FTFC opens use only bars at or before the trigger bar.
   - Mutating any bar after the trigger must not change the decision.
3. **Stop-entry parity:** the fill logic matches `PaperBroker` stop-market
   fills (gap-open and range-touch cases), unit-tested against the broker
   itself.
4. **Corpus fingerprints and the script sha256** are recorded in the results.

## 9. Procedure

1. Merge this document **before** any script is written.
2. Write `scripts/strat_htf_magnitude_ftfc_audit.py` plus tests on a research
   branch. Commit before running.
3. Run **once** on MNQ and MES. Commit the JSON and markdown results unchanged.
4. **No reruns** with different timeframes, windows, setups, stops, targets,
   filters, exits or thresholds. A materially different idea needs a new
   preregistration.

## 10. What a result can and cannot do

- **PASS or PROMISING** (and not OOS CONTRADICTED) permits only a *separate* PR
  proposing a **new observer population** in a **new epoch** that forward-
  collects that cell's rule on the live feed. No trading lane, risk, broker
  or demo change. Any trading use needs its own prereg, forward evidence,
  staged rollback, and explicit operator GO.
- **DOES NOT CLEAR everywhere:** recorded as a rejection of the Strat setups
  with intrabar entry on 60m/4H/daily on this data. The existing Daily 2-2 and
  60m 3-2-2 lanes keep collecting under their own rules. Nothing about them
  changes.
- **In every case:** the running observer, the paper lanes and the demo lanes
  are unchanged.
