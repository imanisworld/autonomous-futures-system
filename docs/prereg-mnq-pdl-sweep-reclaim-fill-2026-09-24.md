# Prereg — MNQ true PDL sweep→reclaim execution study

**Study ID:** `MNQ_PDL_SWEEP_RECLAIM_EXECUTION`  
**Version:** `pdl-sr-b-v0.1`  
**Trial ID:** `T-2026-09-24-prereg-mnq-pdl-sweep-reclaim-fill-2026-09-24-01`  
**Status:** RESEARCH ONLY / PAPER-REPLAY ONLY / NO EXECUTION AUTHORITY.

## Why this is not duplicate work

The existing observer's `PDL_REJECTION_LONG` event is the semantic **true PDL sweep→reclaim**:
the bar starts from inside/above the prior RTH low, trades below PDL, and closes back above it.
The older strategy name `PDL_RECLAIM_SHORT` is instead a break/close below PDL and is not this event.

Prior exposed `fns-v0.1` raw-signal evidence for MNQ `PDL_REJECTION_LONG`:
- H1 n=125, median signed 60m return **+5.43 bps**
- H2 n=86, median signed 60m return **+3.34 bps**

The cross-instrument study closed the family because MES did not replicate it. This trial is therefore
**MNQ-only and prior-exposed**. It cannot produce VALIDATED status on the historical corpus. Its purpose
is narrower: determine whether the already-observed MNQ raw signal survives honest fill mechanics,
structural risk, costs, and current account constraints.

## Population

- MNQ only.
- Existing Polygon 5m RTH replay corpus.
- Prior RTH high/low defined from the immediately preceding complete RTH session, exactly as `fns-v0.1`.
- Event family: `PDL_REJECTION_LONG` only.
- First event in each existing episode; multiple distinct episodes in a session are retained.
- Account-feasible view is capped at the first **3 chronological episodes per session**; full event economics are also reported.
- Existing roll-week exclusions and complete-session rules are reused.
- H1/H2 split reuses `research.futures_non_strat_coverage.HALF_SPLIT`.

## Frozen execution contract

Signal is known at the 5m event-bar close.

- planned entry = event-bar close;
- decision-time market reference = **next 5m bar open**, timestamp-equal to the event close;
- entry = `PaperBroker(entry_fill_model="ioc_limit")`;
- MNQ IOC tolerance = **32 ticks**, the audited production pin;
- one MNQ contract;
- slippage stress = **1 / 2 / 3 adverse ticks**;
- round-trip commission = **$1.48**;
- same-bar stop+target ambiguity = **STOP first**;
- breakeven trail = off;
- no runner;
- no averaging down;
- no live/demo broker path.

### Stop

Exactly one stop definition is admitted:

`STOP_SWEEP_WICK = sweep-bar low - 1 tick`

No wider-stop search is permitted. Current account feasibility is reported at the existing **120-tick MNQ stop cap**.

### Targets

Exactly two structural targets are admitted:

1. `TARGET_VWAP_AT_SIGNAL` = cumulative RTH VWAP known at the event close.
2. `TARGET_PRIOR_DAY_MIDPOINT` = midpoint of the prior complete RTH session high/low.

Targets at/below the entry/fill are structurally invalid and are not replaced by a friendlier target.

No fixed-R multiple target is tested.

## Current-risk view

For every actual fill, report:

- actual stop ticks from fill;
- actual R:R from fill to structural target;
- `risk_feasible = stop_ticks <= 120 and actual_rr >= 2.0`.

The study reports both raw structural economics and the current-risk-feasible subset. It does **not**
widen the 120-tick cap or lower the 2.0 R:R floor to rescue the setup.

## Metrics

For each target × slippage cell:

- attempts / IOC fills / no-fills / bracket-invalid-at-fill;
- WIN / LOSS / OPEN;
- fill rate;
- net P&L after commission;
- expectancy per attempt and per resolved fill;
- PF and max drawdown;
- H1 and H2 separately;
- risk-feasible counts/economics;
- first-three-per-session account-feasible view;
- top-3 winner concentration;
- stop-width and actual-R:R distributions.

## Advancement boundary

This trial can only classify the historical implementation as:
`BROKEN`, `WAIT`, or `PROMISING BUT UNPROVEN`.

It may earn a separate independent-period confirmation only if, without changing geometry:
- the 2-tick stress cell is positive in both chronological halves;
- the current-risk-feasible subset is not empty in either half;
- PF > 1 in both halves on the first-three-per-session view; and
- 3-tick stress does not reverse the overall sign.

No volume, ATR, session, weekday, regime, or news filter participates in this trial.

## Prohibited

- no ATR/volume filter;
- no target/stop tuning after results;
- no live/demo/paper activation;
- no strategy/risk/config/Pine/broker changes;
- no promotion from this historical, prior-exposed population.
