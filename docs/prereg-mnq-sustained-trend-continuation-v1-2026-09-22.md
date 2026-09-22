# MNQ Sustained Trend Continuation v1 — Preregistration (2026-09-22)

**RESEARCH ONLY / AUDIT ONLY / NO EXECUTION AUTHORITY**

## Why this track exists

The 2026-09-22 existing-strategy audit tested the current MNQ LONG shadow families on the canonical 313-day corpus and none cleared the frozen PF 1.94 + stability gate. The only net-positive broad family, `strat_22_continuation_observed`, remained low-PF and half-unstable. Canonical 4HR Re-Trigger and 60M 3-2-2 First Live retain stronger evidence but are intentionally sparse and remain separately constrained by sample/risk/timing.

This track therefore tests a **new, explicitly distinct** research hypothesis: sustained trend continuation should be detected from multi-bar directional persistence first, then entered only after a bounded pause/pullback and causal continuation trigger.

It is not a retune of any existing shadow family.

## Prior rejected work that must not be revived

Do not reuse or rename the rejected 5m `impulse_pullback_continuation` research detector. That design required an upstream STRONG+TRENDING bar and then a contiguous countertrend pullback; its apparent edge failed concentration robustness after removing its best 10 trades.

v1 below is deliberately different:

- persistence is defined directly from closed-bar price path, not Pine market-condition/trend labels;
- the arm is based on a multi-bar displacement + efficiency condition;
- the trigger occurs only after the arm exists and a bounded pullback is complete;
- only one trigger is allowed per directional episode;
- current-account stop compatibility is part of the detector gate.

## Instrument / direction / timeframe

- instrument: **MNQ only**
- direction: **LONG only**
- structural timeframe: **15m completed bars**
- trigger timeframe: **5m completed bars**
- sessions: asian, london, new_york; no session exclusion in v1
- Sunday reopen is not special-cased

No SHORT mirror is tested until LONG v1 is adjudicated.

## Frozen structural arm

At the close of a completed 15m bar, compute from the most recent **8 completed 15m bars**:

- `net_displacement = close[-1] - open[-8]`
- `path = sum(abs(close[i] - close[i-1]))` over the 7 close-to-close transitions
- `efficiency = net_displacement / path` when path > 0
- `up_transitions = count(close[i] > close[i-1])` over those 7 transitions
- `ATR20` = standard true-range ATR over the latest 20 completed 15m bars, including the arm bar

Arm LONG only when all are true:

1. `net_displacement >= 2.0 * ATR20`
2. `efficiency >= 0.65`
3. `up_transitions >= 5 of 7`
4. the 8-bar window closes positive: `close[-1] > open[-8]`
5. no prior v1 LONG episode is still active

No Pine `market_condition`, trend label, volume gate, EMA stack, ORB, VWAP, PDH/PWH, Signa, or regime label is part of the arm.

## Frozen pullback state

After a LONG arm:

- wait for **1 to 3 completed 15m bars**;
- at least one pullback bar must close below its open OR below the prior 15m close;
- define `arm_move = close_arm - open_arm_window`;
- define `pullback_low` as the lowest low of the pullback bars;
- define `pullback_high` as the highest high of the pullback bars;
- reject the episode if the pullback retraces more than **50% of arm_move** from the arm close;
- reject if any pullback bar closes below the 8-bar arm-window midpoint:
  `open[-8] + 0.5 * arm_move`;
- if no valid pullback exists by the third completed 15m bar, expire the arm.

These are fixed v1 rules; no post-result relaxation.

## Frozen trigger

Once a valid pullback exists:

- inspect only subsequent completed 5m bars;
- trigger on the **first completed 5m close strictly above `pullback_high`**;
- the trigger must occur before a fourth 15m bar after the arm completes;
- if no trigger occurs by expiry, record NO_TRIGGER;
- after one trigger or expiry, the episode is consumed. No re-entry/retrigger in v1.

The trigger is close-confirmed. No intrabar high-touch fill is credited.

## Frozen bracket / account-compatibility screen

For the triggered LONG:

- planned entry = trigger 5m close;
- modeled fill = planned entry + **1 MNQ tick** adverse slippage;
- stop = `pullback_low - 1 MNQ tick`;
- reject if stop is not below modeled fill;
- reject if stop distance exceeds **120 ticks**;
- one contract;
- target = modeled fill + **2.0R**;
- round-turn commission = **$1.48**;
- pessimistic stop-first treatment when a later bar contains both stop and target;
- no breakeven;
- no runner;
- no averaging down;
- one open v1 position at a time;
- maximum **3 filled v1 trades per observation day**.

The 120-tick ceiling is the existing MNQ hard-stop budget and is frozen to avoid building another strategy that is positive only with account-incompatible stops.

## Data / development status

Primary retrospective structural screen:

`data/replay_corpus_v1_market_condition_fixed/MNQ`
- 313 daily files
- 2025-07-24 through 2026-07-23
- canonical 15m structural source used by the existing evidence/null-baseline work

Frozen 5m trigger/path source:

`data/replay_corpus_v1_5m/MNQ`

The evaluator must select the exact 5m files matching the 313 structural dates and fail closed before scoring if any measured 15m bar has no causal 5m coverage inside its interval. No silent session dropping, RTH-only substitution, or interpolation is allowed.

Important: these corpora have been used repeatedly by the project. They are **not true out-of-sample validation** for a new idea. A retrospective PASS may only authorize a prospective observation/paper proposal.

The 2026-09-21 move that motivated the research is also not validation data because it has already been observed.

## Retrospective evaluation

Run the detector exactly once after implementation freeze.

Report:

- armed episodes;
- expired/no-trigger episodes;
- triggers;
- stop-cap rejects;
- fills;
- W/L/OPEN;
- net after commission;
- expectancy;
- PF;
- max drawdown;
- distinct filled days;
- H1/H2 chronological split;
- best day;
- leave-best-day-out;
- leave-best-3-days-out;
- top-3 positive-day contribution to total positive day net;
- max consecutive losses;
- session breakdown;
- monthly breakdown.

Also report large-move coverage descriptively using the project's already-established missed-move denominator: non-overlapping 4-bar (15m) blocks with MNQ high-low range >= 60 points. Report all large-range windows and the LONG-relevant subset whose final close is above the first bar's open; for each, report overlap with ARMED, trigger, and filled events. This coverage analysis has no pass/fail authority and may not alter the detector.

## Frozen retrospective gate

v1 may be classified **PROMISING BUT UNPROVEN** only if all are true:

1. >= 40 terminal fills
2. >= 20 distinct filled days
3. net P&L > 0 after commission
4. PF >= **1.94**
5. H1 net > 0
6. H2 net > 0
7. leave-best-day-out net > 0
8. leave-best-3-days-out net > 0
9. top-3 positive-day contribution < 50% of total positive net
10. max drawdown <= **$450** on the one-contract stream

Any failed item = **DOES NOT CLEAR RETROSPECTIVE GATE**. Do not retune on the same corpus.

## Prospective requirement

A retrospective pass does not permit normal paper, DEMO, or live execution.

Before any promotion discussion:

- build a separately reviewed observation/paper lane;
- start a fresh evidence epoch after the detector is frozen;
- collect at least 40 terminal fills over at least 20 distinct trading days;
- use identical signal/bracket/fill formulas;
- require PF >= 1.94, positive net, positive chronological halves, leave-best-day-out > 0, top-3 contribution < 50%, and max drawdown <= $450;
- preserve max 3/day and one open position;
- require replay/live formula parity and causal trigger proof.

## Prohibited follow-ups after seeing results

Do not:

- change 2.0 ATR to another displacement threshold;
- change 0.65 efficiency;
- change 5-of-7 directionality;
- change 1–3 pullback bars;
- change 50% retrace;
- add session filters;
- add Pine TRENDING, EMA, volume, Signa, ORB, VWAP, or structural-level filters;
- change stop cap;
- change target R;
- add runner/breakeven exits;
- mirror SHORT;
- create a "v1.1" from the same results.

A materially different rule requires a new hypothesis, new preregistration, and a fresh evidence question.

## Do not touch

No changes to:

- `risk_rules.yaml`
- `strategy/`
- `risk/`
- `execution/`
- `webhook/`
- broker adapters
- runtime environment
- live/paper/DEMO enablement
- existing 4HR / 3-2-2 / H6-H7 / Asia D+EMA lanes

**No proof, no run.**
