# Prereg — MNQ ORB rework Stage A: timeframe, volume, sweep/failure state

**Study ID:** `MNQ_ORB_REWORK_STAGE_A`  
**Version:** `orb-a-v0.1`  
**Trial ID:** `T-2026-09-24-prereg-mnq-orb-rework-stage-a-2026-09-23-01`  
**Status:** RESEARCH ONLY / RAW-SIGNAL SCREEN. NO EXECUTION AUTHORITY.

This prereg is frozen before the first scored run. It starts the approved rework of
regular MNQ ORB and MNQ inverse ORB. It does not enable or modify any strategy, risk
rule, broker route, webhook, Pine alert, scheduler, proof lane, or deployment state.

The old regular `orb_breakout` and mirrored inverse lane are not treated as valid
baselines. Prior inverse profitability is retired evidence. This study asks a new,
narrower question first:

> Does a causally-defined opening-range event carry reproducible directional
> information before entry, stop, target, or execution mechanics are applied?

## Source / population

- Instrument: **MNQ only**.
- Data: existing Polygon 5-minute RTH replay corpus under
  `data/replay_polygon_5m/MNQ`.
- Session: 09:30–16:00 America/New_York only.
- Incomplete RTH sessions: excluded exactly as `fns-v0.1`.
- Roll-week exclusions: reuse `research.futures_non_strat_coverage.roll_excluded_sessions`.
- Half split: **H1 < 2025-09-01; H2 >= 2025-09-01**, matching current RTH structural work.
- One event episode contributes one Stage-A outcome per definition.

No old journal-approved population is used to select the events.

Within each session and each OR-duration × confirmation-timeframe definition, only the **first causal occurrence of each event-family × direction** is scored. Later same-day recurrences of that same primary cell are ignored. This is frozen before the first run to limit serially-correlated intraday duplicates.

## Frozen research axes

### A. Opening-range duration

Three opening ranges are evaluated independently:

- `OR15`: first 15 minutes, 09:30–09:45 ET (3 native 5m bars).
- `OR30`: first 30 minutes, 09:30–10:00 ET (6 native 5m bars).
- `OR60`: first 60 minutes, 09:30–10:30 ET (12 native 5m bars).

`OR30` is the current canonical RTH research definition. `OR15` and `OR60`
are new research variants and do not replace it.

### B. Confirmation timeframe

Each opening-range duration is observed on two causal confirmation clocks:

- `5m`: native completed 5-minute bars.
- `15m`: session-aligned 15-minute OHLCV aggregated from three completed 5m bars.

No event may use a confirmation bar that overlaps the opening-range formation window.

This creates **6 OR-duration × confirmation-timeframe definitions** before event state.

### C. Event state

For each OR high/low and confirmation clock, record these event families separately:

1. `BREAKOUT`
   - LONG: prior close <= OR high and current close > OR high.
   - SHORT: prior close >= OR low and current close < OR low.

2. `WICK_REJECTION`
   - SHORT: prior close <= OR high, current high > OR high, current close < OR high.
   - LONG: prior close >= OR low, current low < OR low, current close > OR low.
   - This is the same-bar sweep / close-back-inside state.

3. `BREAK_RETEST`
   - A causal breakout arms the corresponding boundary.
   - First later bar that touches/crosses the broken boundary and closes back on the
     breakout side records the retest event.
   - Only the first retest after each armed break is counted.

4. `ACCEPTANCE`
   - First point where **2 consecutive completed confirmation bars** close beyond
     the same OR boundary after the OR is formed.
   - LONG/SHORT follows the accepted side.

5. `FAILED_BREAKOUT_INVERSE`
   - Requires an actual causal `BREAKOUT` first.
   - Within **30 elapsed minutes after the breakout bar closes**, the first completed
     confirmation bar that closes back inside the OR creates an inverse event.
   - Failed upside breakout -> SHORT inverse event.
   - Failed downside breakout -> LONG inverse event.
   - If no close back inside occurs within 30 elapsed minutes, there is no inverse event.

This is the only inverse-ORB population admitted in Stage A. Ordinary losing
breakouts are **not** relabelled as inverse signals. The retired mirrored-bracket
population is not reused.

## Volume

Volume is measured at the event bar, not used to create the event.

Relative volume = event-bar volume / mean volume of the prior **20 completed bars on
the same confirmation timeframe**. If 20 prior bars are unavailable, volume label is
`UNKNOWN`.

Frozen descriptive bins:

- `LOW`: < 0.8
- `NORMAL`: >= 0.8 and < 1.2
- `HIGH`: >= 1.2
- `UNKNOWN`

These thresholds reuse existing repository values/semantics. Stage A does **not**
discard any event because of its volume bin.

## Raw outcome contract

No stop, target, R:R, sizing, IOC tolerance, risk gate, trend gate, or session filter
is applied in Stage A.

Entry reference for measurement = event bar close.

For the event's stated direction, measure later completed bars only:

- signed close return at 15m, 30m, 60m, and EOD;
- signed MFE and MAE through each horizon;
- episode count and distinct session count.

Report all metrics:

- overall;
- H1 and H2;
- by OR duration;
- by confirmation timeframe;
- by event family;
- by direction;
- by volume bin.

No result may be called executable expectancy because Stage A has no fill model.

## Multiple-testing accounting

The full frozen grid is part of the trial count:

- 3 opening-range durations
- × 2 confirmation timeframes
- × 5 event states
- × 2 directions
- = **60 primary directional cells**, before descriptive volume decomposition.

Volume bins are descriptive subdivisions, not additional rescue trials. **Volume cannot rescue a failing primary cell and does not participate in the Stage-A advancement gate.**

No cell can be promoted from Stage A. A cell can only earn a separately preregistered
Stage-B execution study.

## Frozen Stage-A advancement rule

A primary cell is eligible for Stage B only if all are true:

1. >= 80 resolved events total;
2. >= 30 resolved events in each chronological half;
3. mean signed 60m return > 0 in **both** halves;
4. median signed 60m return >= 0 in **both** halves;
5. median 60m MFE > median 60m MAE in **both** halves;
6. no causality, aggregation, timestamp, roll, or population-identity defect is found.

Passing this rule means **PROMISING BUT UNPROVEN / eligible for Stage B only**.

Failing means WAIT or BROKEN for that definition. No post-hoc threshold changes.

## Stage B boundary

Stage B, if earned, is a separate prereg. It will test realistic entry/fill,
detachment, bracket geometry, costs, slippage, stop-first ambiguity, and risk
feasibility. Stage A must not add those mechanics after seeing raw-signal results.

## Relationship to the seven-lane rework

This is the first work item in the broader approved research program:

1. ORB Breakout
2. ORB Reclaim
3. ORB Rejection
4. PDL Reclaim
5. VWAP Reclaim
6. VWAP Rejection
7. Inverse ORB

Stage A deliberately begins with the ORB family because breakout, rejection/sweep,
retest, acceptance, and failed-breakout inverse can be measured from one shared,
causal population without changing execution code.

The other families remain unchanged until their own research step is preregistered.

## Prohibited

- no live/demo/paper activation;
- no strategy/risk/runtime edits;
- no broker calls;
- no Pine changes;
- no filter mining after results;
- no changing OR windows, volume thresholds, 30-minute inverse window, or two-close
  acceptance rule after results;
- no treating old inverse ORB profits as evidence;
- no promotion from this screen.
