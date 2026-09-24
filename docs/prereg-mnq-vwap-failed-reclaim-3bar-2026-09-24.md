# Prereg — MNQ VWAP failed-reclaim within 3 bars raw-signal study

**Study ID:** `MNQ_VWAP_FAILED_RECLAIM_3BAR`  
**Version:** `vwap-fr3-a-v0.1`  
**Trial ID:** `T-2026-09-24-prereg-mnq-vwap-failed-reclaim-3bar-2026-09-24-01`  
**Status:** RESEARCH ONLY / RAW-SIGNAL SCREEN / NO EXECUTION AUTHORITY.

## Why this is new

The existing `fns-v0.1` family `VWAP_FAILED_RECLAIM_SHORT` requires the **immediately prior bar**
to be the reclaim. That exposed MNQ population was inconsistent:
- H1 n=180, median signed 60m return **+3.56 bps**
- H2 n=109, median signed 60m return **−1.84 bps**

It is closed as an immediate-failure hypothesis.

External research independently proposed a broader causal definition: a VWAP reclaim that fails back
below VWAP within **three completed bars**. No repository study matching that frozen population was found.
This trial tests that one definition only. Because lag-1 is already exposed, a pass can be at most
`PROMISING BUT UNPROVEN`.

## Population

- MNQ only.
- Existing Polygon 5m RTH replay corpus.
- Complete 09:30–16:00 ET sessions only.
- Existing roll-week exclusions.
- Cumulative RTH VWAP from completed bars only.
- H1/H2 split = existing `HALF_SPLIT`.

## Frozen event

1. A causal LONG reclaim occurs when:
   `prior_close <= prior_cumulative_vwap` and `current_close > current_cumulative_vwap`.
2. Starting with the next completed 5m bar, observe at most **3 bars**.
3. The first bar whose close is below its then-current cumulative VWAP creates
   `VWAP_FAILED_RECLAIM_3BAR_SHORT`.
4. Direction = SHORT.
5. If no close below VWAP occurs by the third bar, the reclaim expires with no event.
6. A reclaim can produce at most one failed-reclaim event.

The event is known only at the failure bar close.

`failure_lag_bars` = 1, 2, or 3 is recorded descriptively. It is **not** three separate variants and
cannot be used post hoc to rescue a failing combined population.

## Volume / ATR boundary

No ATR threshold and no volume threshold participates in event creation or advancement.

Relative volume at the failure bar is recorded using the prior 20 completed 5m bars and reported only
as descriptive LOW / NORMAL / HIGH / UNKNOWN bins using the repository's existing 0.8 / 1.2 boundaries.

The external 1.5x-volume and ATR sweep/body rules are explicitly **not** coded in this trial.

## Raw outcome

Entry reference for measurement = failure-bar close.

Measure, SHORT-signed:
- close return at 15m, 30m, 60m, EOD;
- MFE and MAE at the same horizons;
- event count and distinct sessions;
- H1 / H2;
- failure lag 1 / 2 / 3 descriptively;
- volume bins descriptively.

No stop, target, IOC, commission, risk gate, session filter, or P&L is applied.

## Frozen advancement rule

The combined <=3-bar population may earn a separate execution study only if:
1. >=100 resolved 60m events total;
2. >=40 resolved events in each half;
3. mean signed 60m return > 0 in both halves;
4. median signed 60m return >= 0 in both halves;
5. median 60m MFE > median 60m MAE in both halves;
6. no timestamp/VWAP/session/roll causality defect is found.

A pass means `PROMISING BUT UNPROVEN` only.

## Prohibited

- no lag-window tuning after results;
- no ATR/volume/news/regime filter;
- no target/stop/entry optimization;
- no runtime/Pine/strategy/risk/broker/config change;
- no paper/demo/live activation;
- no promotion from this historical screen.
