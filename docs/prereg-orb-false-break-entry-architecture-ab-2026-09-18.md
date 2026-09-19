# ORB false-break entry-architecture A/B — preregistration — 2026-09-18

## Verdict boundary

**RESEARCH ONLY / AUDIT ONLY. No runtime change is authorized by this study.**

The reproduced R5 entry-conditioning audit shows that `orb_false_break_fade` has a strong symmetric 1R directional effect before the current resting-entry requirement, but that effect collapses toward coin-flip after a later physical retouch is required. This test changes **one variable only: entry architecture**.

## Frozen population

Use the exact hash-gated R5 candidate and `replay_polygon_v2` inputs already frozen by `scripts/r5_entry_conditioning_reproduction.py`.

- instruments: MNQ and MES;
- family: `orb_false_break_fade` only;
- sessions: the frozen population as stored (London/New York only);
- candidate direction, signal timestamp, absolute stop, and absolute target are unchanged;
- no filters, session rules, risk gates, target width, stop width, or candidate membership may change.

Expected family counts from the frozen candidate bytes:
- MNQ: 1,491;
- MES: 1,550.

## Arm A — current resting-retouch architecture

Reproduce the existing audit exactly:

1. signal bar is complete and excluded from the entry search;
2. search the next 16 completed 15m bars;
3. fill only when the original candidate entry lies physically inside a bar range (`low <= entry <= high`);
4. a gap over the entry is not a fill;
5. fill price = original candidate entry;
6. evaluate the original absolute stop/target over a fresh 16-bar horizon including the fill bar;
7. if stop and target are both reachable in one bar, stop resolves first;
8. no-fill/unresolved contributes 0R to gross-R/all.

## Arm B — causal signal-close architecture

Use the already-established decision-bar-close reference used elsewhere in the repository.

1. signal bar must be complete;
2. fill price = that signal bar's close;
3. the signal bar's own high/low is never reused after the close;
4. original absolute stop and original absolute target are unchanged;
5. require the signal-close fill to lie strictly inside the original bracket; otherwise classify `INVALID_AT_ENTRY` and do not fill;
6. evaluate the next 16 completed 15m bars, beginning with the bar after the signal bar;
7. same stop-first ambiguity rule;
8. unresolved/invalid contributes 0R to gross-R/all.

This deliberately does **not** recompute a 2.5R target from the new fill. Recomputing the target would change both entry and exit geometry and would no longer be a one-variable test.

## Primary metrics

Report separately for MNQ and MES:

- n;
- fill/admissible count;
- target / stop / unresolved / no-fill-or-invalid counts;
- gross R across all frozen candidates;
- gross R among filled candidates;
- chronological H1/H2 gross R across all candidates;
- LONG/SHORT gross R;
- London/New York gross R;
- Arm B minus Arm A gross-R/all delta.

Arm B R is measured from the actual signal-close fill to the unchanged stop/target.

## Secondary robustness view

Without changing membership or bracket levels, repeat Arm B with exactly **1, 2, and 3 adverse ticks** applied to the signal-close fill before bracket-validity checking.

These are sensitivity views only. They are not optimized tolerances and cannot be selected post hoc as policy.

## Pre-registered interpretation

`ENTRY_ARCHITECTURE_SIGNAL_SUPPORTED` only if all are true:

1. Arm B gross-R/all is positive for both MNQ and MES;
2. Arm B improves gross-R/all versus Arm A for both instruments;
3. MNQ H1 and H2 are both positive;
4. MES H1 and H2 are both positive;
5. the 1-adverse-tick Arm B view remains positive for both instruments.

If any condition fails, classification is `NO_RUNTIME_CHANGE / MIXED_OR_UNSUPPORTED`.

Even if all pass, the result only authorizes a later execution-realism/cost test. It does **not** authorize changing `shadow_setups.py`, `signal_engine.py`, risk rules, paper lanes, DEMO, or live execution.

## Safety

No runtime modules are modified by this study. No broker, account, order, risk, environment, service, timer, deployment, or restart is involved.
