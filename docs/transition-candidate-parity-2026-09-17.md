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


## Isolated decision-contract result

The preserved Transition population was then run through a single explicit strategy-scoped research contract rather than piecemeal DecisionEngine exemptions.

The contract keeps hard account/risk controls and removes only the inherited global `min_confluence_grade` selector, which was not part of the preserved Transition population. It remains default-off, paper-only, and has no runtime or external-broker route.

Exact Stage-B comparison:

### Full population
- eligible: 976
- entry parity: 976/976 exact
- resolved fills: 843
- wins/losses: 429 / 414
- net: +$3,337.36
- PF: 1.100975
- H1: +$2,863.92
- H2: +$473.44
- max drawdown: $2,262.74 (19.84%)
- drawdown halt: no
- hard-risk rejections: 51 daily-trade-limit, 3 max-daily-loss

This exactly reproduces the previously frozen $8k ledger result.

### Audit population
- eligible: 82
- exact entry parity: 77
- one-tick cross-feed entry drift: 5
- drift greater than one tick: 0
- resolved fills: 73
- wins/losses: 35 / 38
- net: +$576.46
- PF: 1.152111
- H1: +$76.72
- H2: +$499.74
- max drawdown: $824.86 (10.02%)
- drawdown halt: no

The prior frozen audit-ledger benchmark was +$575.96 / PF 1.151959. Using the current Polygon/raw-close canonical entry on the five ±1-tick feed differences changes net by only +$0.50 and leaves the 73-fill count unchanged.

Reproducer:
`python3 scripts/transition_isolated_contract_audit.py`

Pinned output:
`scripts/transition_isolated_contract_audit_results.json`

This resolves the five one-tick entry discrepancies. It does not establish DEMO eligibility. Slippage stress, timed-exit replay parity, calendar/session identity, and #644 qualification remain outstanding.


## Adverse-slippage stress — promotion blocker

The isolated decision contract was rerun with the strategy, population, $8k ledger, stop, holding horizon, daily limits, and drawdown rule frozen. Only adverse slippage changed.

Stress definition follows the repo qualification convention: **N adverse ticks on entry and exit separately**.

### 1 tick each side — baseline
Full population:
- 843 resolved fills
- +$3,337.36
- PF 1.100975
- H1 +$2,863.92
- H2 +$473.44
- max drawdown 19.84%
- no drawdown halt

Audit population:
- 73 resolved fills
- +$576.46
- PF 1.152111
- H1 +$76.72
- H2 +$499.74
- max drawdown 10.02%
- no drawdown halt

### 2 ticks each side
Full population:
- 572 resolved fills before risk halt
- +$774.94
- PF 1.035412
- H1 +$889.22
- **H2 -$114.28**
- **max drawdown 20.45%**
- **20% drawdown halt triggered**

Audit population:
- 73 resolved fills
- +$503.46
- PF 1.131496
- H1 +$40.72
- H2 +$462.74
- max drawdown 10.12%
- no drawdown halt

### 3 ticks each side
Full population:
- 563 resolved fills before risk halt
- +$496.76
- PF 1.022983
- H1 +$483.12
- H2 +$13.64
- **max drawdown 20.24%**
- **20% drawdown halt triggered**

Audit population:
- 73 resolved fills
- +$430.46
- PF 1.111267
- H1 +$4.72
- H2 +$425.74
- max drawdown 10.22%
- no drawdown halt

Pinned evidence:
`scripts/transition_isolated_contract_slippage_stress_results.json`

### Ruling

**WAIT — FAILS REQUIRED SLIPPAGE ROBUSTNESS**

The full multi-month population is the binding population. It fails the required stress because:
- 2-tick stress makes H2 negative and breaches the 20% drawdown stop;
- 3-tick stress also breaches the 20% drawdown stop.

The positive smaller audit population does not override the full-population failure.

Do not:
- tune intermediate stop values;
- increase the ledger merely to make the drawdown percentage pass;
- loosen the 20% drawdown rule;
- weaken the slippage stress;
- add DEMO/runtime activation.

Timed-exit ReplayEngine parity and C14 calendar work remain valid infrastructure questions, but they are **not required next for this Transition variant** because the variant already fails a binding promotion gate.
