# MNQ Sustained Trend Continuation v1 — Detector Specification

**Status: RESEARCH SPECIFICATION ONLY. No runtime implementation is authorized.**

This document is subordinate to `docs/prereg-mnq-sustained-trend-continuation-v1-2026-09-22.md`. If wording conflicts, the preregistration controls.

## Strategy identity

Research name: `mnq_sustained_trend_continuation_v1`

Purpose: detect a persistent MNQ LONG auction from closed-bar price behavior, wait for a bounded pullback, then require causal continuation confirmation before producing a research candidate.

This is not:

- `impulse_first_pullback_observed`
- rejected 5m `impulse_pullback_continuation`
- `strat_22_continuation_observed`
- 4HR Re-Trigger
- 60M 3-2-2 First Live
- an ORB/VWAP/EMA/regime-gate variant

## Inputs

Required historical/live-equivalent inputs:

- completed MNQ 15m OHLC bars
- completed MNQ 5m OHLC bars
- canonical MNQ tick size/value metadata
- observation-day/session timestamp identity

Not permitted as detector inputs:

- future bars
- Pine market condition
- Pine trend label or trend strength
- EMA stack
- volume/relative volume
- Signa
- ORB
- VWAP
- PDH/PDL/PWH/PWL
- wall/range state
- GEX
- strategy outputs from another family

## State machine

The detector has exactly four states:

`IDLE -> ARMED -> PULLBACK_READY -> CONSUMED`

An expired arm returns to `IDLE`.

### IDLE

On each completed 15m bar, evaluate the frozen 8-bar persistence arm.

If the arm fails, remain IDLE.

If it passes:

- store arm timestamp;
- store arm-window open;
- store arm close;
- store ATR20;
- store arm_move;
- initialize pullback bar count = 0;
- transition to ARMED.

### ARMED

Only completed 15m bars after the arm bar may advance this state.

For each such bar:

1. increment pullback count;
2. update pullback high/low;
3. require at least one qualifying pullback bar:
   - close < open OR close < prior completed 15m close;
4. fail/expire immediately if retrace > 50% of arm_move;
5. fail/expire immediately if a completed pullback bar closes below the arm-window midpoint;
6. expire if count exceeds 3.

Once at least one qualifying pullback bar exists and all invalidation checks still pass, transition to PULLBACK_READY.

### PULLBACK_READY

The structural geometry is frozen at the current valid pullback high/low.

Only completed 5m bars whose close occurs after PULLBACK_READY may trigger.

Trigger when:

`five_min_close > pullback_high`

The first such close wins. No better later entry may replace it.

Before trigger, a newly completed 15m bar may extend the pullback only while the total count remains <=3 and all pullback invalidation rules remain satisfied. If it extends the pullback, refresh pullback high/low causally before evaluating later 5m bars.

Expire once the fourth 15m bar after the arm has completed without a trigger.

### CONSUMED

After TRIGGERED, STOP_CAP_REJECTED, INVALID_GEOMETRY, or EXPIRED, the episode is consumed.

Do not re-arm from overlapping bars belonging to the same 8-bar structural window. A new arm is eligible only after the prior episode reaches CONSUMED/expired and a later completed 15m bar independently satisfies the full 8-bar arm.

## Arm formula

Let completed 15m bars be `b0..b7`, chronological, where `b7` is the arm-decision bar.

`net_displacement = b7.close - b0.open`

`path = Σ abs(bi.close - b(i-1).close)` for i=1..7

`efficiency = net_displacement / path` if path > 0, else 0

`up_transitions = count(bi.close > b(i-1).close)` for i=1..7

`ATR20` is Wilder/RMA ATR(20), using standard true range, seeded as the simple average of the first 20 causal true-range observations and then updated as `(prior_atr * 19 + current_tr) / 20`; the arm bar b7 is included. This matches the project's established meaning of standard ATR rather than a rolling SMA of true range.

Arm iff:

- net_displacement >= 2.0 * ATR20
- efficiency >= 0.65
- up_transitions >= 5
- b7.close > b0.open

All values must be finite. Missing/invalid ATR or insufficient history means no arm.

## Pullback formula

`arm_move = arm_close - arm_window_open`

`midpoint = arm_window_open + 0.5 * arm_move`

As completed pullback bars arrive:

`pullback_low = min(low)`

`pullback_high = max(high)`

`retrace = arm_close - pullback_low`

Valid only if:

- 1 <= pullback_bar_count <= 3
- at least one pullback bar qualifies by close behavior
- retrace <= 0.5 * arm_move
- every completed pullback close >= midpoint

No wick-only midpoint violation invalidates the setup; the preregistration binds invalidation to completed-bar close except for the retrace measurement itself.

## Trigger and bracket

On the first qualifying completed 5m bar:

- `planned_entry = five_min_close`
- `modeled_fill = planned_entry + tick`
- `stop = pullback_low - tick`
- `risk_points = modeled_fill - stop`

Reject before outcome simulation when:

- risk_points <= 0
- risk_points / tick > 120
- any price is non-finite

If accepted:

- `target = modeled_fill + 2 * risk_points`
- contracts = 1
- commission = $1.48 round trip
- stop/target are static
- ambiguous same-bar stop+target = stop first

The research implementation must not silently substitute the planned entry for the modeled fill.

## Daily capacity

Per observation day:

- maximum 3 filled v1 trades;
- only one v1 position may be open at once;
- NO_TRIGGER / expired arms do not consume a trade count;
- stop-cap rejects do not consume a filled-trade count;
- a triggered candidate blocked only because a v1 position is already open is journaled `SKIPPED_BUSY`;
- a triggered candidate beyond the daily fill cap is journaled `SKIPPED_MAX_TRADES`.

## Required research event schema

Every implementation run must be able to emit/reconstruct these event types:

- `ARMED`
- `ARM_INVALIDATED_RETRACE`
- `ARM_INVALIDATED_MIDPOINT`
- `ARM_EXPIRED`
- `PULLBACK_READY`
- `TRIGGERED`
- `STOP_CAP_REJECTED`
- `SKIPPED_BUSY`
- `SKIPPED_MAX_TRADES`
- `FILLED`
- `WIN`
- `LOSS`
- `OPEN_EOD`

Each event must carry enough causal fields to replay the decision:

- event/bar timestamp
- arm timestamp
- arm-window open/close
- ATR20
- net displacement
- path
- efficiency
- up-transition count
- pullback count/high/low
- midpoint
- trigger 5m close
- planned entry
- modeled fill
- stop
- target
- stop ticks
- observation day

## Implementation boundary

The first implementation, if separately authorized, belongs under `research/` plus a read-only runner under `scripts/`.

It must not import or call:

- Tradovate/Webull broker adapters
- webhook execution routes
- RiskEngine mutation paths
- live state persistence

Using `execution.paper_broker.PaperBroker` for offline resolution is permitted only if the public mirror hook is bypassed/disabled so research cannot emit sandbox orders.

No `context/` or `strategy/` integration is permitted in the first implementation.

## Required implementation tests

Before a corpus run, unit tests must prove:

1. insufficient 15m history cannot arm;
2. exact arm-boundary equality passes;
3. below-threshold displacement/efficiency/directionality each fail independently;
4. pullback cannot use an arm bar or future bar;
5. >50% retrace invalidates;
6. midpoint close violation invalidates;
7. fourth 15m bar expires;
8. only a completed 5m close above pullback_high triggers;
9. earlier 5m wick above pullback_high without close does not trigger;
10. first valid trigger is immutable;
11. 121-tick stop rejects, 120-tick stop admits;
12. max 3 fills/day;
13. one open position blocks another;
14. same-bar stop+target resolves stop-first;
15. research path cannot call a broker mirror/external adapter.

## Verdict semantics

- `DOES_NOT_CLEAR_RETROSPECTIVE_GATE`: any frozen retrospective criterion fails.
- `PROMISING_BUT_UNPROVEN`: all retrospective criteria pass; still research-only.
- `VALIDATED`: impossible from the retrospective run alone. Requires the separately preregistered prospective proof described in the controlling preregistration.

No other verdict label may be inferred from a visually impressive day or a single historical episode.
