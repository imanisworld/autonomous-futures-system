# C14 Holiday / Daily-Session Anchor Proof Spec — 2026-09-17

**Status:** OFFLINE PROOF SPEC / NO FIX AUTHORIZED / VWAP REMAINS NOT_ADMITTED

## Proven problem

P3 found a zero-gap parity failure on the trading day after the 2026-09-07 Labor Day session:

- MNQ and MES Pine `ta.vwap` did not reset at the repo's assumed Monday 18:00 ET boundary;
- the divergence appears on the first post-boundary bar;
- Pine `time("D")`-derived HOD/LOD showed the same daily-session identity behavior;
- therefore C14 is not a VWAP arithmetic defect and must not be patched as a VWAP-only holiday exception.

Current replay/research convention keys daily ranges from `scripts/csv_to_replay.detect_day_boundaries()`, which assigns a new session day whenever the 18:00 ET-based session-day key changes. `polygon_to_replay` consumes the same boundary concept.

## Hypothesis under test

TradingView/Pine daily-series identity on CME futures is governed by the exchange/session daily bar construction, including holiday/early-close schedule behavior, rather than an unconditional local-time 18:00 ET reset.

This is a hypothesis to prove, not an implementation instruction.

## Required evidence before code change

Build a read-only fixture/evidence table covering at minimum:

1. ordinary weekday transition;
2. Friday -> Sunday reopen;
3. known Labor Day 2026 transition that produced C14;
4. at least two additional CME holidays with available Pine/TradingView evidence;
5. at least one early-close session;
6. DST transition where ET offset changes but exchange session identity should remain causal.

For each transition record:

- instrument;
- last pre-boundary bar timestamp;
- first post-boundary bar timestamp;
- Pine daily-bar identity / `time("D")` transition where observable;
- Pine VWAP reset or carry status;
- Pine HOD/LOD reset or carry status;
- replay `detect_day_boundaries()` decision;
- exchange holiday/session schedule source;
- data-gap status;
- verdict: MATCH / REPLAY_EARLY_RESET / REPLAY_LATE_RESET / NOT_TESTABLE.

MNQ and MES should be checked independently even when the expected calendar is shared.

## Fix acceptance criteria

A code fix is allowed only after one generalized rule explains the tested ordinary and holiday transitions without instrument/date hard-coding.

The correction should live at the narrowest shared daily-session identity layer that feeds all affected constructs. Do not separately teach VWAP, HOD, LOD, PDH/PDL/PDC different holiday rules unless the evidence proves their source semantics differ.

Required regression proof:

- fail-before/pass-after on the known 2026-09-08 C14 case;
- ordinary non-holiday sessions unchanged;
- weekend transition unchanged unless evidence requires otherwise;
- additional holiday fixtures pass;
- early-close fixture passes;
- `csv_to_replay` and `polygon_to_replay` retain identical daily-boundary semantics;
- deterministic outputs;
- full CI green.

## Explicit non-solutions

Reject any patch that:

- special-cases `2026-09-07` or Labor Day by date/name only;
- widens VWAP tolerance;
- excludes holiday rows from the parity denominator merely to pass;
- changes VWAP entry/stop/target logic;
- rewrites historical Pine values;
- changes live strategy/gate behavior;
- treats missing-bar C16 as the same defect as C14.

## Downstream rule

Even after a code fix, `vwap_*` is not automatically admitted or validated. Re-run the affected P3/parity population on the corrected boundary semantics and separately handle C16 gap sensitivity.

Until that proof is complete: **C14 BLOCKED / VWAP NOT_ADMITTED / NO PROMOTION-GRADE HOLIDAY-WEEK VWAP CONCLUSIONS.**