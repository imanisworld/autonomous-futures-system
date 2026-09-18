# Transition candidate identity parity — 2026-09-17

Status: **AUDIT ONLY / RESEARCH ONLY**

Purpose: determine whether the current Transition detector reproduces the preserved 3,292-candidate MNQ population used by the 400t/30m study.

## Inputs

Saved population:
- `logs/missed_move_transition_MNQ_costed.json`
- candidates: 3,292 unique timestamps
- SHA-256: `e51cf8c4ec9c63f73a5967e66ffab61fc1a1ca771fe7e517cb9de388a769d043`

Preserved 5m corpus:
- `data/replay_polygon_5m/MNQ`
- files: 621
- bytes: 167,835,450
- tree SHA-256: `a290d0c45089ce49d7750adccce0b20f6a56725d740a51e2382649701b7e9fbd`

Reproducer:
`python3 scripts/transition_candidate_parity_audit.py`

## Result

### Legacy shadow contract

The current shadow wrapper requires `market_condition in {RANGE_BOUND, CHOPPY, TRANSITION}`.

Against ReplayEngine's current market-condition representation:
- detected: 28
- intersection with preserved population: 28
- false positives: 0
- preserved misses: 3,264
- recall: 0.8505%
- precision: 100%

ReplayEngine labels at the 3,292 preserved timestamps:
- TRENDING: 3,011
- CONSOLIDATING: 253
- CHOPPY: 28

### Objective price/volume geometry

Running the exact sweep/reclaim/hold/expansion geometry without the representation-dependent market-condition label:
- detected: 3,292
- intersection with preserved population: 3,292
- false positives: 0
- preserved misses: 0
- recall: 100%
- precision: 100%

## Ruling

**GEOMETRY_EXACT_CONDITION_LABEL_DIVERGES**

The preserved candidate population is fully determined by the objective price/volume geometry on this corpus. The market-condition label is not a portable selection variable between the preserved source and current replay representation.

Therefore:
1. the legacy shadow detector keeps its label filter unchanged for continuity;
2. the new 400t/30m research strategy uses the objective geometry directly;
3. this is not outcome-selected parameter tuning — it is candidate-identity reconciliation against the pre-existing preserved population;
4. the research variant still does not gain permission to bypass the normal full DecisionEngine market-condition gates in runtime;
5. any strategy-specific DecisionEngine exemption remains a separate, explicit policy decision and is not authorized by this audit.

This resolves historical candidate-identity blocker #1 for the research variant, but does not by itself satisfy #644 replay/runtime parity.


## Full DecisionEngine gate attribution

Using the canonical research geometry on all 3,292 preserved timestamps with the isolated $8k/400t risk envelope but **without adding any DecisionEngine exemption**:

- TRADE: 0
- NO_TRADE: 3,292

Terminal blocker distribution:
- TREND_STRENGTH_BELOW_REQUIRED: 956
- SIGNAL_BAR_VOLUME_TOO_LOW: 815
- EMA_STACK_NOT_ALIGNED: 750
- STRATEGY_NOT_PAPER_ELIGIBLE: 610
- MARKET_CONDITION_NOT_TRENDING: 127
- MARKET_CONDITION_NOT_TRADABLE: 34

Therefore the next blocker is not one market-condition flag. Reproducing the historical population through full DecisionEngine would require multiple strategy-specific policy exceptions or a separately defined decision contract.

No such carve-out is authorized by this audit. The isolated executor remains a research bridge only, and PR #659 must not be treated as #644-qualified or DEMO-eligible.
