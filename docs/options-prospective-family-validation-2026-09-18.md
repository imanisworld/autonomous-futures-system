# Options prospective family validation — third-session result — 2026-09-18

## Verdict

**PRE-REGISTERED THREE-SESSION CHECK COMPLETE.**

This is the first point at which the frozen prospective rule can classify the two follow-up 30m families.

- `STRAT_212_REVERSAL`: **NO LONGER SHOWING EXCESS**
- `OTHER:strat_122`: **PERSISTING POSSIBLE SIGNAL**

This is structural first-sight evidence only. It is **not** option expectancy, strategy approval, production readiness, or trade authorization.

## Frozen rule

Before any prospective session existed, the lane fixed this requirement for a family to remain a possible signal:

- at least 30 prospective episodes;
- at least 3 prospective sessions;
- both directions represented;
- ex-opening first-sight >=1R excess versus the matched baseline of at least **+5 percentage points**.

Otherwise the result is `NO LONGER SHOWING EXCESS` or `INSUFFICIENT PROSPECTIVE SAMPLE`.

Retrospective sessions remain 2026-09-09 through 2026-09-15 and are not re-scored. Prospective sessions are 2026-09-16, 09-17, and 09-18.

## Methodology integrity

The 2026-09-18 collector cycle completed and the validation script passed its fail-closed methodology checks:

- sessions agree across events and outcomes: 2026-09-09 through 2026-09-18;
- observer `cov-v0.1`;
- reducer `ep-v0.1`;
- outcome study `out-v0.1`;
- first-sight delay remains **17.9 minutes**;
- no missing rows;
- no provider errors;
- collector source SHA is in the pre-approved collector set.

Input hashes are preserved in
`data/options_prospective_family_validation_2026_09_18/result.json`.

## Primary-20 prospective result

| Family | Episodes | Sessions | L / S | First-sight 1R | Matched baseline | Diff | Ex-opening diff | 2R | Stop first | Median MFE | Status |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 2-1-2 reversal | 47 | 3 | 26 / 21 | 34.9% | 40.4% | -5.5 pp | **-4.2 pp** | 18.6% | 44.2% | 0.452R | **NO LONGER SHOWING EXCESS** |
| 1-2-2 | 38 | 3 | 16 / 22 | 45.9% | 20.8% | +25.1 pp | **+20.9 pp** | 35.1% | 16.2% | 0.950R | **PERSISTING POSSIBLE SIGNAL** |
| inside break | 7 | 3 | — | — | — | — | — | — | — | — | PASSIVE_ONLY |

## Interpretation

### 212R

The retrospective 212R first-sight excess was +11.9 pp. Across the first three untouched prospective sessions, the family now has 47 episodes with both directions represented, but the matched-baseline excess is **negative** and the ex-opening result is **-4.2 pp**.

Under the exact pre-registered vocabulary, 212R is therefore:

**NO LONGER SHOWING EXCESS**

This does **not** mean "proven bad option strategy." It means the specific structural first-sight excess that justified the follow-up did not persist under the frozen three-session prospective test.

That distinction matters because separate 212R work corrected the actual trigger clock and source geometry. This observer study uses the original frozen first-sight framework to answer only whether the discovered family-level excess persists. It does not replace the trigger-time/option-side research lane.

### 1-2-2

1-2-2 reaches 38 episodes over 3 sessions, both directions, with an ex-opening first-sight excess of **+20.9 pp**.

Under the pre-registered rule, it is:

**PERSISTING POSSIBLE SIGNAL**

That status is still hypothesis-level evidence. It does not authorize a detector, V1 rule change, contract selection, DEMO entry, or live trade.

## What changes

1. The original prospective family-validation question is no longer waiting on sample count.
2. 212R no longer has prospective support from this particular first-sight matched-baseline study.
3. 1-2-2 is now the only one of the two preregistered follow-up families that preserves the defined excess threshold.
4. Do not combine this result with the separate corrected 212R trigger-time option study as though they measure the same entry clock.
5. No V1 tuning follows automatically from either status.

## What does not change

- 212R option-side expectancy remains unproven.
- The dedicated 212R prospective collector / source-policy work remains a separate lane.
- Historical exact 212R option replay remains blocked on causal historical Delta and contract-level OI.
- 1-2-2 still requires its own causal entry/stop/target definition and option-side evidence before any strategy qualification.
- No scanner, runtime, risk, DEMO, broker, or live behavior changes.

## Reproduction

The frozen prospective script is:

`data/options_demo_gate_212R_2026_09_18/underlying/prospective_validation.py`

The 2026-09-18 run used the latest collector aggregate through 2026-09-18 and produced:

- `status.json`
- `prospective_family_summary.csv`
- `prospective_baseline_comparison.csv`
- `prospective_concentration.csv`
- `prospective_family_population.csv`

Only the compact result + input hashes are committed here; the production observer database and raw collector artifacts remain outside the repo.

**No proof, no trade.**
