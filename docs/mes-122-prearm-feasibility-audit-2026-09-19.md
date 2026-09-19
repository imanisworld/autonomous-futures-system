# MES 1-2-2 Pre-Arm Feasibility Audit — 2026-09-19

## Verdict

**AUDIT ONLY / NOT FEASIBLE AS EXACT PRE-ARM BROKER STRATEGY /
PAPER EVIDENCE ONLY.**

A lower-timeframe observation-only study is technically feasible, but it
cannot establish that the current historical gap-at-next-open fills were
broker-executable under the same strategy identity.

Preregistration:
`docs/prereg-mes-122-prearm-feasibility-audit-2026-09-19.md`

Frozen prereg commit:
`b42f9cf672ea31f027d32afb5c2c041976cc608e`

Audit base:
`bfb2340f46f99a8c6e6582635254ecfba4fb1448`

## Why

The canonical 1-2-2 arm bar is the directional 15m bar immediately following
an inside bar.

Only when that arm bar is complete are all required facts final:
- its final Strat type (2U or 2D);
- its final high;
- its final low;
- reversal direction;
- entry = opposite boundary plus/minus one tick;
- stop = exact opposite boundary;
- target = fixed 2R from that frozen entry/stop.

TradingView sends the authoritative payload only when
`barstate.isconfirmed`, using `alert.freq_once_per_bar_close`.
The payload timestamp is the bar OPEN time, while delivery happens after the
bar closes.

Therefore the final arm is first knowable at the arm bar close.
## Boundary impossibility

Let the arm bar span [T-15m, T).

At T:
1. the arm bar becomes complete;
2. TradingView can finally emit its final OHLC/type;
3. the canonical 1-2-2 trigger/stop/target become fully frozen;
4. the watched 15m bar opens.

So:

`final_arm_known_time == watched_bar_open_time`

It is impossible for a broker order using the FINAL arm-bar high/low to have
already existed **before** the watched bar open.

Any implementation that arms before T must use an incomplete arm-bar high,
low, or classification. That changes the trigger, stop, target, or setup
validity and is therefore a different strategy identity.

## Current model consequence

The canonical resolver assumes the watched bar is being observed against a
boundary that was already fixed. It correctly models:
- gap-at-open entry pricing;
- one-tick break beyond the arm boundary;
- fixed opposite-boundary stop;
- fixed 2R target;
- pessimistic loss when entry and stop are both touched;
- fail-closed invalid bracket after an extreme gap;
- one watched 15m bar only.

Those formulas are internally consistent as a counterfactual paper/replay
model. They are not proof that the exact next-bar-open entry was operationally
available from the completed arm-bar data.

## Safety and persistence

Verified:
- current external-execution path refuses a late substitute after the watched
  bar already determined the outcome;
- PaperBroker/replay reconstruct the counterfactual fill instead;
- armed state is instrument-keyed in `DailyState.strat_212_122_state`;
- ordinary journal rows persist that state;
- `JournalLogger.get_daily_state()` restores it;
- an arm expires after exactly one watched bar;
- interleaved instruments cannot consume each other's arm.

No restart-persistence or accidental-execution defect was found.

## Timeframe and session

The ordinary decision path expects 15m input and rejects mismatched timeframes.
TradingView emits the authoritative 15m payload only on confirmed bar close.
The current NY decision windows are half-open and cover 09:30 <= ET < 16:00;
no new session-boundary defect was found for this strategy.

The existing 1m context path is isolated:
- records 1m data;
- returns `ONE_MIN_CONTEXT`;
- bypasses DecisionEngine and RiskEngine;
- has no execution reachability.

This means a 1m observation-only extension could consume an already-frozen
15m arm without granting 1m setup-discovery authority.

## What a 1m study could validly answer

A separately preregistered observer could measure:
- first post-arm 1m trigger time;
- first-minute gap/open relationship;
- delivery latency from the completed 15m arm;
- whether trigger/stop/target ordering becomes less ambiguous at 1m;
- duplicate and restart behavior.

It must not create or alter the 1-2-2 setup from 1m data.

Such a study would answer:
"What entries are prospectively attainable after the final 15m arm is known?"

It would not convert the existing next-bar-open counterfactual into execution
proof.

## Test proof

Focused regression set:
- `tests/test_strat_212_122.py`
- `tests/test_mes_122_paper_lane.py`
- `tests/test_futures_session.py`

Result: **85 passed**.

## Classification

MES 1-2-2 remains:

**PROMISING BUT UNPROVEN / PAPER EVIDENCE ONLY /
EXACT PRE-OPEN EXECUTION PARITY NOT FEASIBLE AS SAME STRATEGY.**

## Safe next step

Do not build an execution path from this audit.

If the strategy is worth further study, the smallest safe next task is a
separately preregistered **post-arm 1m attainability observer** with:
- MES only;
- completed 15m arm as the sole authority;
- no fills or P&L;
- no risk path;
- no execution path;
- exact one-15m-bar expiry.

No proof, no run.
