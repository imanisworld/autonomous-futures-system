# MNQ 60M 3-2-2 First Live — Trigger-Timing A/B — 2026-09-18

## Verdict

**TIMING EDGE SURVIVES / PROMISING BUT UNPROVEN / AUDIT ONLY.**

No runtime, strategy, risk, broker, collector, TradingView, paper-fill authority,
or deployment change was made.

The documented rule is explicit: entry is the **first live break** of the
opposite 9AM boundary during 10:00–11:00 ET; no candle close is required.

The accepted implementation/evidence stack had a timing approximation:
it learned that a 5m bar crossed the level only when that bar completed. This
audit compares that completed-bar model with a causal pre-armed stop-entry on
the exact same frozen 34 candidates.

## Frozen audit trail

Preregistration:
- `docs/prereg-322-trigger-timing-ab-2026-09-18.md`
- commit `97c87fddbfb98404ab0d87a1d61148ba3c6ad2ba`

Frozen harness:
- `scripts/322_trigger_timing_ab_2026_09_18.py`
- harness commit `6d05a62`

Result artifact:
- `scripts/322_trigger_timing_ab_2026-09-18.json`
- SHA-256 `f0bde574e4fbf74d22e0554f27721976fc6a4629c46bbee18f77c5f713146254`

Focused verification after the result:
- **74/74 passed**
- includes the audit harness, canonical 3-2-2 state machine/replay,
  PaperBroker stop-market entry semantics, and edge-decomposition resolver.

## Population parity

The accepted July research detector and current canonical 5m state machine
were cross-checked before scoring outcomes.

Both produced the exact same:
- 34 candidates;
- 17 LONG / 17 SHORT;
- first candidate 2024-08-02;
- last candidate 2026-06-11;
- entry trigger;
- stop;
- target;
- direction.

Primary timing corpus:
`data/replay_corpus_v1_5m_4hr_audit/MNQ`

Corpus SHA-256:
`7f09a7f82ee282e892f5db9b86be3130e6a5c1210d86828a56b9666bb06afd35`

## Reproduction gates

The new model was not interpreted until the prior accepted models reproduced
the September audit.

### Legacy plan/backfill — 1 adverse tick

- candidates 34
- fills 33
- wins 32 / losses 1
- net **+$2,532.66**
- PF **13.57**
- H1 **+$1,383.34**
- H2 **+$1,149.32**

Exact reproduction: **PASS**.

### Completed-5m close + IOC32 — 1 adverse tick

- candidates 34
- fills 20
- wins 19 / losses 1
- net **+$1,859.40**
- PF **12.169**
- H1 **+$1,068.68**
- H2 **+$790.72**

Exact reproduction: **PASS**.

## Completed-5m latency

At the completed crossing-bar close, adverse distance from the original First
Live trigger was:

- median: **9 ticks**
- p90: **176 ticks**
- maximum: **444 ticks**
- more than the 32-tick IOC tolerance: **13/34 = 38.24%**

So the completed-5m IOC model is not a faithful implementation of the
documented First Live rule for a material portion of the historical sample.

## Causal pre-armed First Live model

At 10:00 ET, the 9AM trigger/stop/target are already known. The primary model
therefore arms a resting stop-entry before the crossing bar:

- trigger fill at the documented level plus adverse slippage;
- gap-through fills at the causal bar open;
- reject if the slipped fill lands outside its own fixed bracket;
- trigger bar itself can resolve stop/target;
- same-bar stop+target ambiguity is pessimistic stop-first.

### Results

| Slippage | Fills | Resolved | W-L | Net | PF | H1 | H2 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 tick | 33 | 33 | 33-0 | **+$2,742.66** | ∞ | +$1,383.34 | +$1,359.32 |
| 2 ticks | 33 | 33 | 33-0 | **+$2,726.16** | ∞ | +$1,374.84 | +$1,351.32 |
| 3 ticks | 33 | 33 | 33-0 | **+$2,709.66** | ∞ | +$1,366.34 | +$1,343.32 |

At 3 ticks:
- LONG: 17 resolved, **+$1,754.34**
- SHORT: 16 resolved, **+$955.32**
- both chronological halves positive
- max drawdown in the resolved ledger: $0 because all 33 resolved rows were
  positive
- one candidate rejected as `ENTRY_BRACKET_INVALID_AT_FILL`

That rejected row is 2026-05-12 SHORT:
- trigger 29113.50
- target 29113.25
- adverse slippage makes the entry incompatible with the 0.25-point target
  geometry
- fail-closed behavior is correct.

## Same-trigger-bar audit

The pre-armed model resolves 12 trades on the trigger bar itself.

For all 12:
- the bar opens on the pre-trigger side;
- the documented trigger lies between the open and target;
- the target lies farther in trade direction;
- therefore touching the target necessarily requires price to cross the trigger
  first.

There are **zero** trigger bars in this set that touch both the documented stop
and target.

The corrected improvement is therefore not produced by choosing a favorable
path through an unknowable same-bar stop/target ambiguity.

Eleven of the twelve same-bar targets eventually have the same target outcome
under the old plan model.

The single material outcome change is:

### 2025-10-16 SHORT

- trigger 25026.00
- stop 25125.50
- target 25020.75
- trigger bar O/H/L/C:
  25055.00 / 25063.75 / 25013.50 / 25043.50

A true First Live SHORT becomes active when price trades below 25026.00.
That same bar then trades to 25013.50, necessarily through the 25020.75
target.

Results:
- legacy plan model: later STOP, **−$203.48** at 3 ticks
- completed-close IOC: later STOP, **−$168.48**
- pre-armed First Live: same-bar TARGET, **+$7.52**

The completed-close models miss the rule-defined target because the order is
not considered active until after the crossing bar closes.

## Interpretation

The historical 3-2-2 signal does **not** collapse when the First Live timing
rule is modeled more causally. Under this frozen population it becomes stronger.

This resolves the specific trigger-timing question:

**the old completed-5m IOC representation was late relative to the documented
strategy; a causal pre-armed representation preserves the historical edge.**

It does **not** validate the strategy.

Major remaining blockers:
- only 34 historical candidates;
- 33/33 resolved wins under the corrected model is an extreme small-sample flag,
  not evidence to trust blindly;
- the historical population is consumed evidence;
- prospective confirmation is still required;
- every historical candidate remains incompatible with the current real-account
  stop-width / R:R architecture;
- at audit completion, no 1m 3-2-2 trigger observer was authorized. Subsequently, PR #749 deployed an observation-only observer at release `6d5b224aa5c2`; it has no paper-fill or broker authority.

## Operational ruling

- strategy evidence classification: **PROMISING BUT UNPROVEN**
- trigger-timing classification: **TIMING EDGE SURVIVES**
- current-account execution status: **HOLD / PARKED**
- live execution: **NOT AUTHORIZED**
- new paper-fill authority: **NOT AUTHORIZED**

The smallest next proof, if separately authorized, is an
**observation-only 1m 3-2-2 armed-trigger evidence lane** that can compare
natural First Live touches with the existing 5m-close timestamps without
creating or submitting trades.

No proof, no run.
