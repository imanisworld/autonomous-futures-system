# 4HR Re-Trigger MNQ — Gate Ablation Handoff

_As of 2026-09-07. This supersedes the old Batch-1 continuation instructions for the current MNQ gate-diagnostic question. Historical 4HR handoff files remain provenance only._

## Verdict

**AUDIT ONLY / PROMISING SIGNAL-BRACKET / CURRENT GATING FAILS / NO RULE CHANGE YET.**

The current evidence no longer supports the blanket statement that 4HR MNQ itself is broken. The documented signal/bracket is profitable on the current 81-candidate population, while production gating removes almost all of that population before execution.

This is not authorization to loosen risk controls. The next task is attribution only.

## Completed gate-attribution result

Population: 81 MNQ candidates.

- raw signal positive at all tested horizons;
- documented bracket: **+$3,069.60, PF 1.774**;
- structurally rejected candidates alone: **+$3,076.02, PF 1.797**;
- 80 candidates rejected before execution;
- 1 approved candidate, IOC no-fill;
- IOC over all candidates: 44 fills, **+$1,731.86, PF 2.001**;
- IOC over structural survivors: 2 fills, both losses, **-$37.46**.

### First engine disposition

| Blocker | Count |
|---|---:|
| `MARKET_CONDITION_NOT_TRENDING` | 38 |
| `RR_BELOW_MINIMUM` | 19 |
| `stop_too_wide` | 11 |
| `ENTRY_DETACHED_FROM_PRICE` | 8 |
| `WEAK_BAR_CLOSE` | 4 |
| IOC no-fill | 1 |

### Overlapping gate diagnostics

- `stop_too_wide`: 75 candidates, +$3,180.98, PF 1.847
- `rr_below_minimum`: 61 candidates, +$1,253.70, PF 1.409
- `min_confluence_grade`: 12 candidates, -$573.76, PF 0.423
- `target_too_close`: 8 candidates, -$51.34, PF 0.609

## Current interpretation

The evidence supports three statements only:

1. The documented 4HR MNQ signal/bracket contains measurable edge on this population.
2. Current production gating rejects almost all candidates before execution.
3. Stop width and R:R are large overlapping rejection populations, but first-blocker counts show the trend gate is the largest immediate blocker.

The evidence does **not** yet prove which single gate should be changed, because the diagnostics overlap and a candidate can fail several gates.

## Next exact test — one gate at a time

Use the **same exact 81 candidates** and unchanged execution assumptions.

Run the baseline current gate stack, then five independent ablations:

1. disable only `MARKET_CONDITION_NOT_TRENDING`;
2. disable only `RR_BELOW_MINIMUM`;
3. disable only `stop_too_wide`;
4. disable only `ENTRY_DETACHED_FROM_PRICE`;
5. disable only `WEAK_BAR_CLOSE`.

Do not alter thresholds. Do not remove multiple gates together. Do not change entry, stop, target, session, signal detection, costs, slippage, fill logic, or candidate population.

## Execution assumptions

Use the same assumptions as the completed gate-attribution audit so results are directly comparable:

- IOC-faithful execution;
- pessimistic same-bar resolution;
- current commission/slippage assumptions from the audit;
- one contract;
- no lookahead;
- same chronological ordering and sessions.

## Required output per cell

Report:

- candidates admitted past structural gating;
- IOC fills / no-fills;
- net P&L;
- PF;
- expectancy per fill;
- max drawdown;
- H1 and H2;
- Asian / London / New York split;
- delta versus the full current-gate baseline;
- delta versus IOC over all 81 candidates.

Also report which previously rejected candidate IDs/timestamps become executable under each single-gate ablation. This is needed to detect overlap and to avoid mistaking count changes for independent causal effects.

## Decision rule after the ablation

- If one gate removal admits a robust positive population with both halves positive and materially improves execution without relying on concentration, classify that gate as a **candidate architecture defect** requiring a separate safety review before any code change.
- If removing one gate does not recover robust execution, do not change it.
- If only multi-gate combinations appear necessary, stop. That becomes a new strategy/risk-policy question and requires explicit authorization before testing combinations.

## Do not do

- no stop widening sweep;
- no R:R threshold tuning;
- no entry-distance tuning;
- no runner experiment;
- no session filtering;
- no parameter search;
- no strategy rewrite;
- no config change;
- no deployment;
- no promotion.

## Current local artifacts

Reported on 2026-09-07:

- `docs/4hr-mnq-gate-attribution-2026-09-07.md`
- `scripts/four_hr_mnq_gate_attribution_2026-09-07.json`

These artifacts were reported from the local worktree and are not assumed to exist on `main` until committed.

## Stop condition

After the five single-gate cells are reported, stop and classify the evidence. Do not automatically proceed to Miyagi or another 4HR variant.
