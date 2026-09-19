# ORB false-break entry-architecture A/B — results — 2026-09-18

## Verdict

**NO RUNTIME CHANGE / SIGNAL-CLOSE ENTRY REJECTED AS A FIX.**

The preregistered one-variable A/B is complete. The reproduced entry-conditioning defect is real, but replacing the later resting retouch with a completed signal-bar-close entry does not recover a robust ORB false-break edge.

Preregistration commit: `39b292a`.

Result SHA-256: `e0da53e51d7ea416de73680e4d9ce672833171d2061cdea945c42859ca9e3f63`.

A complete second run produced a byte-identical result JSON.

## Frozen test

Population:
- MNQ: 1,491 `orb_false_break_fade` candidates;
- MES: 1,550;
- exact hash-gated R5 candidate bytes;
- exact hash-gated `replay_polygon_v2` bar corpus;
- London/New York only in the frozen population.

Only entry architecture changed:
- Arm A: current later physical retouch at the original candidate entry;
- Arm B: completed signal-bar close;
- original absolute stop and target unchanged;
- signal bar not reused for outcome resolution;
- next 16 completed 15m bars for Arm B;
- stop first on same-bar ambiguity.
## MNQ

Arm A:
- fill: 1,322 / 1,491;
- gross R/all: **+0.0111**;
- H1 **+0.0141**, H2 **+0.0080**;
- 365 target / 896 stop / 61 unresolved / 169 no-fill.

Signal-close Arm B:
- admissible: 1,361 / 1,491;
- 130 invalid-at-entry because the completed close was already outside the original bracket;
- gross R/all: **-0.0596**;
- H1 **-0.0674**, H2 **-0.0517**;
- 554 target / 736 stop / 71 unresolved;
- delta vs Arm A: **-0.0706R/candidate**.

Adverse signal-close stress:
- 1 tick: **-0.0683R/all**;
- 2 ticks: **-0.0761R/all**;
- 3 ticks: **-0.0842R/all**.

The result is negative in both directions and both London/New York session partitions.

## MES

Arm A:
- fill: 1,416 / 1,550;
- gross R/all: **-0.0416**;
- H1 **-0.0103**, H2 **-0.0729**;
- 359 target / 962 stop / 95 unresolved / 134 no-fill.
Signal-close Arm B:
- admissible: 1,499 / 1,550;
- 51 invalid-at-entry;
- gross R/all: **-0.0847**;
- H1 **-0.0725**, H2 **-0.0968**;
- 531 target / 865 stop / 103 unresolved;
- delta vs Arm A: **-0.0430R/candidate**.

Adverse signal-close stress:
- 1 tick: **-0.1194R/all**;
- 2 ticks: **-0.1504R/all**;
- 3 ticks: **-0.1784R/all**.

The result is negative in both directions and both London/New York session partitions.

## Pre-registered gate

All five required checks failed:
- signal-close positive on both instruments: **FAIL**;
- improvement over Arm A on both: **FAIL**;
- MNQ H1/H2 positive: **FAIL**;
- MES H1/H2 positive: **FAIL**;
- 1-tick stress positive on both: **FAIL**.

Classification: **`NO_RUNTIME_CHANGE / MIXED_OR_UNSUPPORTED`**.

## Interpretation

The original diagnosis should not be simplified to “the resting entry is late, so enter immediately.” The later retouch does select a materially different subset, but the causal completed-close alternative is worse on this fixed-bracket test.

Do not:
- change `orb_false_break_fade` runtime entry;
- loosen stops or widen targets to rescue this result;
- add filters based on this test;
- promote the structural ORB false-break observer.

Any further ORB redesign would be a new strategy hypothesis and needs a new preregistration. The separate 2-2 entry-conditioning finding is not answered by this ORB test and must be tested independently if reopened.

No runtime, strategy, risk, broker, configuration, deployment, or service restart changed.
