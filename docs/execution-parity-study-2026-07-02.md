# Execution Parity Study — 2026-07-02

## Decision

Do **not** enable the 5-minute retest lane or either continuation candidate.
Keep `FIVE_MIN_FEED_ENABLED=false`, `EXIT_MODE=static`, and
`HTF_DIRECTION_MODE=off`.

## Data and model

- MES and MNQ Polygon/Massive data, 2024-07-01 through 2026-06-26.
- 622 daily 15-minute replay files per instrument.
- 621 synchronized 5-minute files per instrument (140,273 MES and 140,277 MNQ
  raw bars, including warmup).
- One micro, one tick adverse slippage, pessimistic same-bar ordering.
- Retest entries use the exact 15-minute-authorized ORB bracket and causal
  completed 5-minute bars. Runner exit: activate at 1.0R and trail by 0.5R.
- Predefined grid only: TTL 15/20/30 minutes × close distance 1/2/4 ticks.

## ORB retest result

The current proposal (20-minute TTL, one-tick close distance):

| Instrument | Arms | Fill rate | Resolved | Expectancy | Net P&L | First half | Second half |
|---|---:|---:|---:|---:|---:|---:|---:|
| MES | 522 | 8.62% | 43 | $7.53 | $323.76 | $13.23/trade | $0.33/trade |
| MNQ | 316 | 1.58% | 4 | -$18.31 | -$73.25 | -$26.50/trade | -$15.58/trade |

MES was positive in both halves only at the current 20-minute/one-tick setting.
MNQ had no grid variant with positive expectancy in both halves. Therefore no
variant satisfies the cross-instrument promotion rule.

Full machine-readable results:

- `logs/retest_scorecard/MES.json`
- `logs/retest_scorecard/MNQ.json`

## Continuation observation

The two causal, first-per-direction/day candidates use local swing invalidation
and remain non-executable:

| Candidate | MES first / second | MNQ first / second | Decision |
|---|---:|---:|---|
| First structured pullback | $4.99 / $1.07 | $13.26 / -$0.89 | Reject |
| Tight consolidation break | $1.95 / $0.17 | $6.86 / -$7.26 | Reject |

Both candidates decay out of sample on MNQ. Drawdowns and losing streaks are
also too large for the selected risk-adjusted objective.

## Engineering changes retained

- Live and replay share one pure close-confirmed retest predicate.
- Retest TTL now begins when the 15-minute bar closes, not at its opening
  timestamp (the previous implementation effectively reduced a 20-minute TTL
  to about five minutes).
- `EXIT_MODE` is the proof-critical authoritative contract:
  `static`, `runner_shadow`, or `runner_live`.
- `runner_live` uses Tradovate's required `bracket1` as a stop and omits the
  optional second child, removing the fixed target that conflicted with replay.
- Every accepted live stop replacement persists the new stop and possibly
  reminted order ID for restart-safe reconciliation.
- Atomic promotion remains pinned to `EXIT_MODE=static`; runner-live cannot
  enter production accidentally.
