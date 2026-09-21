# Prereg — ORB_BREAKOUT_LONG geometry + fill study (`orbx-v0.1`), research-only

**Status: RESEARCH / READ-ONLY. Written 2026-09-21 12:30Z before the run.**
Follows `docs/futures-non-strat-coverage-fns-v0.1-first-run-2026-09-21.md`, where
`ORB_BREAKOUT_LONG` was the only family above the drift control in all four
instrument × half cells. This study asks the only question that record left
open: **does that excess survive a pre-registered exit and realistic fills?**

## Why this is not a re-run of the July 2026 rejection

`docs/mnq-structural-level-5m-study-2026-07-13.md` tested break/retest/reclaim/
rejection with (a) a tiny structural stop against a distant single-level fixed
target, (b) a 1.0R-activation / 0.5R-trail runner. Fixed target: robustly
negative. Runner: positive but dies to a 2-tick slippage stress. Both exits
are excluded here. This design is different on purpose: the exit is the same
**time horizon** the coverage statistic was measured on, so the study tests
the observed excess directly rather than a new target hypothesis.

## Frozen design (one primary, one secondary variant, nothing else)

| element | rule |
|---|---|
| universe | MNQ and MES, RTH 09:30–16:00 America/New_York, same replay, same roll exclusion and session-completeness rule as `fns-v0.1` |
| signal | `ORB_BREAKOUT_LONG` first event per episode from `ns-v0.1` predicates (`prev.close <= orb_high < close`, evaluated from bar 6) |
| entry | market at the **next** bar's open after the event bar close (never the event close itself), + adverse slippage |
| stop (primary) | `orb_low` of that session (structural, known at arm); checked on every later bar's low; a bar whose low ≤ stop is a stop-out at the stop price minus adverse slippage (intrabar order is never assumed in our favour) |
| stop (secondary) | ORB midpoint `(orb_high + orb_low) / 2` — tighter, same rules |
| exit | **time stop**: close of the 12th bar after entry (60 minutes), or the stop, whichever first; if fewer than 12 bars remain, exit at the session's last bar close |
| size | 1 contract; MNQ $2/pt, MES $5/pt; commission $1.50 round trip (micro) |
| slippage stress | reported at 0, 1 and **2 ticks** adverse per side (tick = 0.25 → MNQ $0.50, MES $1.25 per side) |
| one trade per session per instrument | the first eligible episode only; later same-session breakouts are not traded (no pyramiding, no re-entry) |
| halves | H1 2024-07 → 2025-08, H2 2025-09 → 2026-06 (as `fns-v0.1`) |

## Pass bar (fixed before running)

**Primary pass:** at **2 ticks** adverse slippage, net expectancy per trade
> 0 in **all four** cells (MNQ H1, MNQ H2, MES H1, MES H2), primary stop.
**Then and only then** a second gate: pooled profit factor at 2 ticks ≥ the
standing null p95 **1.94**. Passing both = eligible to be written up as a
*forward-paper candidate prereg* for operator decision. Passing the first
only = "PROMISING BUT UNPROVEN, no forward lane" (recorded, closed).
Failing the first = closed, and the July rejection is extended to this design.

The secondary stop is reported alongside; it cannot rescue a primary failure.

## What is reported

Per instrument × half × slippage level: n, win rate, net P&L, expectancy,
profit factor, max drawdown (sequential), stop-out share, time-exit share,
median bars held. Pooled row per slippage level. No other cut, no parameter
search, no "what if the stop were…".

## Runner

`python research/futures_orb_breakout_time_exit.py --instrument MNQ --out logs/orbx_v01`
(read-only; imports `research/futures_non_strat_coverage.py` helpers and
`alert_ranker.non_strat_coverage`; nothing under `strategy/`, `execution/`,
`engine/` imports it).
