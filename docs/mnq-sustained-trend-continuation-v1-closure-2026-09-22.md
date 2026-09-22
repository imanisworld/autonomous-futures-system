# MNQ Sustained Trend Continuation v1 — Closure (2026-09-22)

**Final classification: DOES_NOT_CLEAR_RETROSPECTIVE_GATE**

This closes the v1 research question. The preregistered detector was run once from
PR #912 head `cd50f423c77a87255f3b1b19160bb9566f902011` against the frozen corpora.
CI for that exact head completed successfully after the run. No detector,
threshold, stop, target, session, or data-selection changes were made after
results were observed.

## Data integrity

- structural corpus: `data/replay_corpus_v1_market_condition_fixed/MNQ`
- trigger corpus: `data/replay_corpus_v1_5m/MNQ`
- causal 5m coverage check: **23,533 / 23,533 15m bars covered**
- coverage verdict: **PASS**
- the 2026-09-21 motivating move was not in the canonical retrospective corpus
  and is not treated as validation data

The run used absolute paths into the main checkout because the corpora are
gitignored and unavailable inside a fresh isolated worktree. Output was written
to a session scratch location rather than the worktree. Those path differences
do not change the study logic or measurements.

## Frozen gate result

| Criterion | Frozen requirement | Result | Pass? |
|---|---:|---:|---|
| terminal fills | >= 40 | **34** | **FAIL** |
| distinct filled days | >= 20 | **33** | PASS |
| net P&L after commission | > $0 | **+$576.18** | PASS |
| profit factor | >= 1.94 | **1.762** | **FAIL** |
| first-half net | > $0 | **+$410.54** | PASS |
| second-half net | > $0 | **+$165.64** | PASS |
| leave-best-day-out net | > $0 | **+$440.14** | PASS |
| leave-best-3-days-out net | > $0 | **+$247.10** | PASS |
| top-3 positive-day share | < 50% | **26.69%** | PASS |
| max drawdown | <= $450 | **$172.42** | PASS |

Eight of ten preregistered criteria passed, but the gate required **all ten**.
The terminal sample was too small and PF was below the frozen 1.94 hurdle.

## Ruling

v1 is **closed**.

Do not:

- rerun v1 with changed thresholds;
- lower the PF hurdle or sample minimum;
- alter ATR, efficiency, directionality, pullback, retrace, stop, target, or
  session rules;
- add a runner or breakeven rule;
- mirror the detector SHORT;
- create a v1.1 from the same retrospective result;
- integrate v1 into `context/`, `strategy/`, risk, broker, paper, DEMO, or live
  execution paths.

A future sustained-trend study must be a materially new hypothesis with its own
preregistration and evidence question.

## Deployment / execution status

No merge, deployment, restart, config/risk change, paper/DEMO enablement, or
broker action is authorized by this result.

**No proof, no run.**
