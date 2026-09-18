# Options clean-shadow controlled target-geometry test — 2026-09-18

## Verdict

**CONTROLLED TEST COMPLETE / TARGET WIDENING DOES NOT RESCUE THIS SAMPLE.**

This is a read-only research result on the frozen clean V1-EPOCH-2 shadow cohort. It does not tune V1, prove a strategy edge, or authorize 212R.

The test changes **target geometry only**. Entry, underlying invalidation, premium stop, selected contract, ASK entry, BID exit, observed quote cadence, quantity, and same-session horizon are held fixed. The separate Daily/4H horizon question is intentionally not changed here.

## Frozen population

Epoch boundary: `2026-09-16T16:47:46` through the end of `2026-09-17`.

The stored 117 shadow rows reproduce the repaired entry-geometry partition exactly:

- `AHEAD`: **47**
- `TARGET_CONSUMED_AT_ENTRY`: **58**
- `STOP_CONSUMED_AT_ENTRY`: **12**

Only the 47 `AHEAD` rows enter the controlled comparison.

Canonical selected-input SHA-256:

`0074cd80b659ea84766737b3199f9bce1c689c4cbf09c9306f43da13f3c364ea`

## One-variable comparison

| Target rule | Target first | Stop first | Censored at same-session horizon | All 47 forced-horizon P&L | Avg / row |
|---|---:|---:|---:|---:|---:|
| Recorded `target_1` | 14 | 9 | 24 | **-$840** | -$17.87 |
| Fixed 1.5R | 6 | 8 | 33 | **-$783** | -$16.66 |
| Fixed 2.0R | 4 | 8 | 35 | **-$748** | -$15.91 |

For the all-row comparison, rows still unresolved at the fixed same-session horizon are marked to the last observed executable **BID** by that same horizon. They are not treated as wins, zeros, or silently dropped.

Wider targets improve this sample's fixed-horizon P&L slightly relative to the recorded target (+$57 at 1.5R; +$92 at 2R), but **all three variants remain negative**. Widening also sharply increases event censoring: 24/47 with the recorded target, 33/47 at 1.5R, and 35/47 at 2R.

## What this proves

For these exact 47 clean shadow rows, under the same observed entry/contract/stop/cost/horizon path, simply replacing the recorded target with 1.5R or 2R does **not** turn the sample positive.

That weakens the hypothesis that target width alone explains the negative option result in this cohort. It does not establish that the recorded target is optimal.

## What this does not prove

- This population is a mixed clean counterfactual shadow cohort, **not a 212R-specific option population**.
- It does not answer whether Daily/4H needs a longer holding horizon. That remains a separate test.
- It does not reveal intra-snapshot price order; observations are scanner/contract snapshots, so unobserved path remains unknown.
- It does not freeze a new target policy, runner rule, fee/slippage policy, or V1 tuning.
- It does not prove positive expectancy, DEMO eligibility, or live readiness.

## Reproduction

Script:

`PYTHONPATH=. python scripts/options_shadow_target_geometry_controlled.py --db <read-only options_scanner snapshot> --output <result.json>`

Committed aggregate/row evidence:

`data/options_shadow_target_geometry_controlled_2026_09_18/result.json`

The production database is not committed. The script selects the frozen epoch/time boundary, requires the exact 117 / 47 / 58 / 12 population identity, hashes the selected causal inputs, and fails closed if that identity drifts.

## Next boundary

Do **not** tune V1 from this result.

The previously separate Daily/4H horizon-compatibility question remains open. For 212R specifically, prospective option evidence remains blocked on recent consolidated-SIP access, and historical exact option replay remains DATA BLOCKED on causal historical Delta and contract-level open interest.

**212R remains WAIT / promising but unproven.**
