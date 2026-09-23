# Observer Signal Grading (A / B / C / D), Forward Test — Preregistration (2026-09-23)

**RESEARCH / AUDIT ONLY. NO EXECUTION AUTHORITY.** This fixes, in advance, a
grade for observer signals and how it will be judged on **future** live
observer data. It changes no detector, bracket, population, epoch, lane, risk
rule or broker path. Nothing here displays, filters or trades on a grade.

## 1. Why this exists

The operator wants to know which signals are "A+", the ones worth taking,
versus the rest. Every backtest so far says no setup family has a proven edge
by itself:

- #936: the 15m Strat source rules;
- #941: Strat on 60m/4H/daily;
- #911: trend families;
- the 09-21 grid and the 07-31 shadow null.

A grade built by looking at those backtests would mostly pick lucky cells.
This document instead **writes the grade down before the data exists** and
judges it **once**, on observer outcomes collected after the labels go live.

"A+" never means certain. The question is only whether A-grade signals do
measurably better than the rest, after costs.

## 2. The grade (frozen)

Every observer signal is graded from fields the observer records on every
row:

- `strat_ftfc.alignment` (added by #940): aligned / against / conflict /
  unknown;
- `session`;
- `market_condition`.

| Grade | Rule |
|---|---|
| **A** | FTFC **aligned** AND `session` = `new_york` AND `market_condition` ≠ `DEAD` |
| **B** | FTFC **aligned**, but not A |
| **C** | FTFC **conflict** |
| **D** | FTFC **against** |
| (excluded) | FTFC **unknown**, or the label is missing |

**Why these three factors.** They were chosen before any forward data exists,
and each has only weak prior support. That is disclosed, and it is exactly why
this is a forward test.

- **FTFC** is TheStrat's own continuity rule.
  - #936 (15m): aligned beat conflict on 5 of 6 families' baselines, and
    conflict held about 70% of signals and most of the losses.
  - #941 found it did **not** help on 60m/4H entries. The observer is 15m.
- **New York session:** older fixed-target studies found the NY filter helped
  fixed-target exits (runner-edge note, 2026-06-29, legacy fill model).
- **Not DEAD:** in #936 Q3, DEAD was negative for 5 of 6 families' baselines.

No other factor is used: no time-of-day buckets beyond the session, no
setup-specific rules, no volume, no EMA. Adding any factor later requires a
new preregistration.

## 3. Data

- **Source:** `logs/cross_instrument_observation_v1.jsonl` on the box.
  - `OUTCOME` rows of **structural_outcome** populations only (their bracket
    is authoritative).
  - Deduplicated by `(record_type, candidate_id)`.
- **Start:** the first bar processed by the first futures release that carries
  #940 (`strat_ftfc` definition `strat_ftfc_opens_v1`). Rows without the label
  are excluded, never back-filled.
- **Instruments:**
  - **MNQ** is primary.
  - **MES** is replication.
  - M2K, MGC, MCL and MBT are descriptive only (collection-only roots).
- **Terminal outcomes:** WIN, LOSS and EXPIRED rows that carry `pnl_r`.
  NO_FILL is excluded.
- **Cost-adjusted R per trade (the primary metric):**
  `r_net = pnl_r − cost_r`, where
  `cost_r = (2 × tick_value + 1.48) / (risk_ticks × tick_value)`. That is 1
  adverse tick on entry and exit plus $1.48 round-turn commission, the same
  costs as #934/#938. The observer's own rows are gross.
- **Roll exclusion:** #940's opens are not roll-adjusted. Signals labeled
  `roll_adjusted: false` whose trading date falls from **14 calendar days
  before the 3rd Friday** of March/June/September/December **through the end
  of that month** are excluded. If a roll-adjusted label ships first, its rows
  are not excluded.
- **Sample end:** a change to any structural population's detector or bracket
  ends the sample. The look then happens on what exists, marked `TRUNCATED`.

## 4. The single look

The test is evaluated **once**, at the first Friday-report run on which both
conditions hold for **MNQ**:

- grade A has ≥ **60** terminal outcomes on ≥ **30** distinct trading days;
- each of B, C and D has ≥ **30** terminal outcomes.

If those are not met by **2027-03-31**, the look happens then anyway, and any
gate whose minimum is unmet reads `INSUFFICIENT`.

**Before the look, no report shows per-grade performance.** The only
exception, disclosed here, is #940's Friday "MNQ 2-2 reversal: big-picture
check" field. It shows lined-up / mixed / against dollars for one family. The
grade is frozen, so seeing it cannot change the rule.

## 5. Gates (MNQ; all pooled across structural families)

Confidence intervals come from a **day-clustered bootstrap**: 10,000
resamples of trading days, seed `20260923`.

| # | Gate |
|---|---|
| G1 | Grade A mean `r_net` > 0, and its 95% CI lower bound > 0 |
| G2 | Mean `r_net` of A minus C: 95% CI lower bound > 0 |
| G3 | Point estimates ordered A ≥ B ≥ C |
| G4 | Grade A mean `r_net` > 0 in both halves (split at the median trading date of A's outcomes), and A's net R after removing its best day > 0 |
| G5 | No single strategy family supplies > 50% of grade A's net R |

**Verdicts:**
- **GRADE VALIDATED (MNQ):** G1–G5 all pass.
- **GRADE ORDERS SIGNALS, A NOT PROFITABLE AFTER COSTS:** G2 and G3 pass, G1
  fails.
- **GRADE NOT SUPPORTED:** anything else.
- **INSUFFICIENT:** minimums unmet at the deadline.

**Replication (MES):** REPLICATED if grade A's mean `r_net` > 0 and G3 holds
on MES. MES never creates a validation by itself.

**Descriptive only:** per-family and per-instrument grade tables, win rates,
and the collection-only roots.

## 6. Procedure

1. Merge this document **before** #940 is released, so the grade is fixed
   before a single labeled row exists.
2. Write `scripts/observer_grade_evaluation.py` plus tests on a research
   branch, computing only counts until the look. Commit before the look.
3. At the look: run once, and commit the JSON and markdown unchanged.
4. **No changes** to the grade, factors, cost model, minimums, gates or
   deadline. A different grade needs a new preregistration and a new forward
   sample.

## 7. What a result can and cannot do

- **GRADE VALIDATED** permits only:
  - a PR that **displays** the grade on observer Discord cards (operator GO
    required);
  - a separate preregistration for an **A-only paper lane**.

  No demo or live trading, and no change to any existing lane.
- **GRADE ORDERS SIGNALS, A NOT PROFITABLE:** the grade may be displayed as
  context (operator GO), with the words "better than the rest, not
  profitable". No lane.
- **GRADE NOT SUPPORTED / INSUFFICIENT:** recorded. Nothing changes.
- **In every case:** the observer keeps collecting unchanged.
