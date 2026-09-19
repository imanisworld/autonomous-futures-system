# Futures Causal / Mechanics Audit — 2026-09-19

## Verdict

**AUDIT ONLY / HOLD MIYAGI REPLAY FIX / NO DEPLOYMENT.**

The audit found one proven historical-replay defect and one execution-parity
limitation. It did **not** find a new forming-HTF lookahead leak or a new
4HR/3-2-2 live/replay state-machine divergence in the inspected paths.

Strategy classifications do not improve from this audit.

- 12HR Miyagi: **PROMISING BUT UNPROVEN / PARKED / REPLAY DEFECT FOUND**.
- MES 15m 1-2-2: **PROMISING BUT UNPROVEN / PAPER EVIDENCE ONLY /
  OPERATIONAL PARITY GAP**.
- 4HR Re-Trigger: unchanged, **PROMISING BUT UNPROVEN / PAPER ONLY**.
- 60M 3-2-2 First Live: unchanged, **PROMISING BUT UNPROVEN signal /
  BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS**.
- LC_ZONE v1: unchanged **HOLD**.

Preregistration:
`docs/prereg-futures-causal-mechanics-audit-2026-09-19.md`

Frozen prereg commit:
`f1e08bd56a31ce5fc97866990228ac4516387c3c`
Frozen audit base:
`36e73f1981850b66b043d849ce877c15bd1ab3e7`

Two later main commits (#782 options 1-2-2 collector docs/deployment state and
#783 Daily 2-2 timing audit) were merged into the branch after preregistration.
Neither changed Miyagi, the futures 2-1-2/1-2-2 state machine, HTF logic,
runner, replay engine, risk, or execution code.

## 1. Miyagi — proven trigger-bar replay defect

The controlling Miyagi rule says:
- wait for the reversal to **hit the trigger**;
- enter on the hit;
- set the fixed stop **immediately at entry**.

The current research replay does something different. In
`research/replay_12hr_miyagi_honest_fill.py`, the bar whose range first
touches the trigger is deliberately excluded from stop/T1 resolution, except
for the special 15:55 EOD bar.

That means a stop touched after the entry sometime inside the same 5m bar can
be ignored and a later target can be credited instead.

This is a mechanical mismatch, not a threshold or performance opinion.
### Frozen-population causal A/B

Population and detector were held fixed:
- MNQ: 15 corrected candidates, 8 trigger fills;
- MES: 19 corrected candidates, 10 trigger fills;
- study range: 2024-07-02 through 2026-06-26;
- chronological midpoint: 2025-06-29;
- same commission and 1/2/3/4-tick adverse slippage;
- same fixed stop and T1;
- only trigger-bar eligibility and stop-market gap semantics changed.

Audit policy:
- trigger bar is eligible for stop/T1 immediately after the touch;
- if entry and stop both occur on a 5m OHLC bar and path is unknowable,
  resolve pessimistically as STOP;
- a true gap-through stop-market entry references the bar open, not the stale
  trigger level.

The frozen sample contained **zero** gap-through entry bars, so the result
change comes entirely from immediate trigger-bar stop handling.

### MNQ result

One row changes materially: **2024-09-18 SHORT**.

- trigger: 19698.125
- stop: 19733.0
- T1: 19669.25
- trigger bar: 09:30 ET
- O/H/L/C: 19731.0 / 19736.75 / 19689.25 / 19697.75
- current replay: later TARGET, +$54.51 at 2 ticks
- causal/pessimistic replay: same-trigger-bar STOP, **-$72.99**

The trigger bar crossed the entry and also traded above the fixed stop.
OHLC cannot establish whether the stop excursion came before or after entry,
so the standing pessimistic rule treats it as a loss.

At 2 adverse ticks:

| Metric | Current replay | Causal trigger-bar audit |
|---|---:|---:|
| Fills | 8 | 8 |
| W-L | 7-1 | **6-2** |
| Net | +$552.83 | **+$425.33** |
| PF | 3.223 | **2.322** |
| Max drawdown | $248.74 | **$321.73** |
| H1 net | +$419.32 | **+$291.82** |
| H2 net | +$133.51 | +$133.51 |
| H2 fills | 1 | **1** |

The corrected result remains positive, but H2 still contains only one fill.
This is not validation.
MNQ causal sensitivity:
- 1 tick: +$433.33, PF 2.355, H1 +$298.82, H2 +$134.51;
- 2 ticks: +$425.33, PF 2.322, H1 +$291.82, H2 +$133.51;
- 3 ticks: +$417.33, PF 2.289, H1 +$284.82, H2 +$132.51;
- 4 ticks: +$409.33, PF 2.257, H1 +$277.82, H2 +$131.51.

### MES result

MES has one trigger bar that also reaches its stop: **2025-12-04 SHORT**.

- trigger: 6861.375
- stop: 6870.5
- T1: 6854.25
- trigger bar: 09:30 ET
- O/H/L/C: 6876.25 / 6877.0 / 6861.0 / 6862.25

The existing replay already records this trade as a loss on the next bar.
The causal correction moves the stop resolution onto the trigger bar but does
not change final P&L.

At 2 ticks MES remains:
- 10 fills;
- 7W / 3L;
- +$138.85;
- PF 1.593;
- H1 +$140.685;
- H2 **-$1.835**.

At 3 and 4 ticks H2 remains negative.
### Miyagi evidence provenance

Reproduction harness:
`scripts/miyagi_trigger_bar_causal_audit_2026_09_19.py`

Machine-readable result:
`scripts/miyagi_trigger_bar_causal_audit_2026-09-19.json`

Result SHA-256:
`a0c7c469a2a748f253d71e20a1dc414e4e4e93b5f1a602992a745333064be8d8`

Affected source files:
- MNQ 2024-09-18 SHA-256
  `a617193557295e77c3810def0b39a4e3ec3d336425ed9e5e79f9cb1c36665619`
- MES 2025-12-04 SHA-256
  `49536996ff2c4ca1cc0e765b9b821adc0aa973ab2d83d9e5ad798b0e191900b4`

The raw cache timestamps are UTC; the audit loader converts them to ET before
session/entry analysis.

## 2. Miyagi gap-through semantics

The current replay also states that a later intraday 5m bar which gaps through
the trigger is filled at the old trigger price plus slippage.

A pre-armed stop-market order would instead use the gap-open reference.
This is mechanically incorrect for future execution-parity work.
No frozen Miyagi trigger fill in the corrected MNQ/MES population was a
gap-through event, so this issue does **not** change the current historical
numbers. It still must be corrected before replay can be treated as an
execution-faithful Miyagi model.

## 3. Futures 2-1-2 / 1-2-2 — formula clean, execution parity absent

The shared canonical state machine in `strategy/strat_212_122.py` is
mechanically strong for a hypothetical pre-armed order:

- precursor bar arms a fixed boundary;
- only the next bar is watched;
- gap-through fills use the watched bar open;
- same-bar target/stop cases are resolved;
- ambiguous entry+stop cases resolve pessimistically;
- gap beyond the fixed bracket fails closed;
- per-instrument armed state round-trips through the journal.

Replay and PaperBroker preserve those same formulas.

However, the production runner explicitly documents that **no broker order was
actually armed ahead of the watched 15m bar**. By the time the 15m close
arrives, the hypothetical entry/outcome already happened.

For PaperBroker/replay the runner reconstructs that already-decided state via
`restore_position()` or `force_resolve()`.
For a non-Paper broker, the runner correctly refuses to submit a substitute
late order.

Therefore:

**The present MES 1-2-2 evidence proves a causal reconstructed paper model.
It does not prove operational execution parity.**

This is not an accidental-live safety defect. The live-broker path fails
closed. It is a promotion blocker: actual DEMO/live execution would require a
separate pre-armed/lower-latency architecture and prospective proof.

## 4. Higher-timeframe causality — clean in inspected paths

No new forming-HTF lookahead was found.

Verified:
- TradingView `risksentinel_context.pine` requests Daily/4H/1H values from
  `[1]` / `[2]` with `barmerge.lookahead_on`, i.e. completed HTF bars;
- `context/htf_loader.py` delays HTF rows until their close;
- regression tests prove Daily, 4H, and 1H forming bars are not visible early;
- `context/live_direction.py` uses current price against prior-day fixed
  levels and the **prior completed** UTC-anchored 4H window;
- runner and replay both call the same live-direction helper when that source
  is enabled.
The UTC-anchored live-direction 4H partition differs from TradingView's
session-anchored 4H candles by design. That identity difference is already
documented and must not be confused with a lookahead leak.

## 5. 4HR / 3-2-2 static parity — clean in inspected scope

No new formula fork was found:
- production and replay route 4HR and 3-2-2 through the canonical pure state
  machines;
- 5m-native replay routing is regression-tested;
- current 1m observers remain evidence-only and do not acquire setup authority.

This does not replace the existing requirement for natural prospective 1m
trigger evidence.

## 6. 3-2-2 restart persistence — fixed on current main

The previously known executable-state persistence omission has been repaired
repo-side:
- runner/replay journal `strat_322_first_live` state;
- `JournalLogger.get_daily_state()` restores it;
- regression tests cover round-trip persistence and legacy journals.

This repair is a repository fact, not a reason to deploy or enable executable
3-2-2. The active observation-only 3-2-2 lane has separate isolated state and
does not require executable-strategy promotion.
## 7. Timestamp / session mechanics

No new boundary defect was proven in the inspected scope.

Verified:
- payload timestamps accept ISO or Unix ms and normalize to aware UTC;
- session detection converts to America/New_York;
- DecisionEngine NY windows use ET and half-open boundaries;
- 2-1-2 / 1-2-2 are NY-restricted;
- 3-2-2 and 4HR observer/state-machine boundaries use explicit ET logic.

## Test proof

Targeted regression set:
- HTF lookahead;
- futures session handling;
- 5m-native replay parity;
- 4HR executable state machine;
- 3-2-2 state machine/persistence;
- 2-1-2/1-2-2 state/replay/live-broker refusal;
- Miyagi detector;
- Miyagi current replay.

Result: **144 passed**.

Important: the existing Miyagi replay test suite intentionally asserts that
the trigger bar cannot resolve its own bracket. Passing tests therefore
confirm the implementation matches that policy; they do not make the policy
consistent with the written Miyagi rule.
## Problems found

### Blocker — Miyagi replay identity

Current Miyagi replay is not fully causal because it suppresses trigger-bar
stop/T1 resolution. One frozen MNQ trade is materially misclassified.

### Promotion blocker — MES 1-2-2 operational parity

The current paper/replay state is reconstructed after the watched bar closes.
No actual pre-armed broker order exists. Do not treat this lane as proof that a
broker-connected system can capture the historical/paper entries.

### No blocker found in inspected HTF/session paths

The inspected HTF, timestamp, 4HR, and 3-2-2 formula paths have explicit
causality protections and passing regression coverage.

## Required fixes

Before relying on Miyagi replay as execution-faithful evidence:
1. make the trigger bar eligible for immediate stop/T1 resolution;
2. preserve pessimistic stop-first handling when path is unknowable;
3. use gap-open reference for stop-market gap-through entries;
4. regenerate Miyagi evidence and retire superseded headline metrics;
5. update tests that currently pin the wrong trigger-bar policy.
Before MES 1-2-2 receives any broker-execution authority:
1. define a genuinely pre-armed or lower-latency order-equivalent mechanism;
2. prove its trigger, gap, bracket, dedupe, and restart behavior prospectively;
3. keep reconstructed PaperBroker evidence separate from execution proof.

Do not:
- widen risk caps;
- relax R:R;
- change Miyagi targets/stops to rescue results;
- add LC_ZONE filtering;
- deploy this audit;
- enable live trading.

## Safe next step

The smallest justified code change is a **Miyagi research-replay correction**
only, under a new fix scope:
- change same-trigger-bar handling and gap semantics;
- regenerate the exact frozen evidence;
- reconcile Miyagi docs/tests;
- no runtime wiring and no deployment.

The MES 1-2-2 execution-parity design should remain a separate later decision.

No proof, no run.
