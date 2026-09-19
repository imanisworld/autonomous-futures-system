# Options Daily/4H horizon compatibility test — 2026-09-18

## Verdict

**CONTROLLED HORIZON TEST COMPLETE / NO BLANKET HIGHER-TIMEFRAME EXTENSION IS SUPPORTED.**

This is a read-only research result on the Daily and `4H_RTH` subset of the frozen clean V1-EPOCH-2 shadow cohort. It does not tune V1, authorize a new holding rule, or validate 212R.

The test changes **holding horizon only**:

- baseline: same RTH session close;
- comparison: close of exactly **one additional RTH session**.

Recorded `target_1`, entry, underlying invalidation, premium stop, selected contract, quantity, ASK-entry/BID-exit accounting, and observed price path are held fixed. No alternate target geometry is introduced.

The one-extra-session comparison is the shortest common extension available for every row. This is not a sweep for the best horizon.

## Frozen population

Source: the exact 47 `AHEAD` rows from
`OPTIONS_SHADOW_TARGET_GEOMETRY_CONTROLLED_V0_1`.

Daily/4H subset:

- total rows: **24**
- Daily (`1D`): **10**
- `4H_RTH`: **14**
- unique structural keys: **21**
  - Daily: **7** structural keys across 10 rows
  - `4H_RTH`: **14** structural keys across 14 rows

The repeated Daily rows are retained because the prior clean-shadow study froze row membership. They are not treated as independent evidence in the interpretation.

Prior geometry input SHA-256:

`0074cd80b659ea84766737b3199f9bce1c689c4cbf09c9306f43da13f3c364ea`

Horizon-study selected-input/path SHA-256:

`d3a55e8a217a49e7283550b319cbd10427072cfa74c9aaf5683c0a773074135e`

## Controlled result

### Combined 24 rows

| Horizon | Target | Stop | Censored | Forced-horizon P&L | Avg / row |
|---|---:|---:|---:|---:|---:|
| Same session | 6 | 3 | 15 | **-$571** | -$23.79 |
| +1 RTH session | 12 | 7 | 5 | **-$539** | -$22.46 |

The extra session reduces censoring from 15 to 5 but changes total P&L by only **+$32**. The combined population remains negative.

Among the 15 rows censored at the same-session boundary:

- 6 become target resolutions;
- 4 become stop resolutions;
- 5 remain censored after the additional session.

### Daily only — 10 rows / 7 structural keys

| Horizon | Target | Stop | Censored | Forced-horizon P&L | Avg / row |
|---|---:|---:|---:|---:|---:|
| Same session | 0 | 2 | 8 | **-$358** | -$35.80 |
| +1 RTH session | 4 | 2 | 4 | **+$137** | +$13.70 |

On this small Daily subset, one additional RTH session changes P&L by **+$495** and converts four previously censored rows into target resolutions without adding a new stop.

That is **suggestive** that same-session resolution can truncate Daily setups. It is not sufficient to freeze a Daily overnight-hold rule: only 10 rows are present, representing 7 structural keys.

### `4H_RTH` only — 14 rows

| Horizon | Target | Stop | Censored | Forced-horizon P&L | Avg / row |
|---|---:|---:|---:|---:|---:|
| Same session | 6 | 1 | 7 | **-$213** | -$15.21 |
| +1 RTH session | 8 | 5 | 1 | **-$676** | -$48.29 |

For `4H_RTH`, the extra session reduces censoring but **worsens** P&L by **-$463**. Of the seven same-session-censored rows, the extension produces four additional stops, two additional targets, and one still-censored row.

This does not support applying a blanket next-session hold to `4H_RTH` in this cohort.

## What this proves

For these exact 24 rows under the retained execution path, a single universal "higher timeframe = hold one more session" rule is not supported.

The effect is materially different by timeframe:

- Daily improves in this small sample;
- `4H_RTH` deteriorates;
- combined P&L remains negative.

Therefore the earlier statement that the same-session horizon may be distorting higher-timeframe results is **partly supported for Daily in this sample, not for 4H as a blanket rule**.

## What this does not prove

- It does not establish an optimal Daily or 4H holding period.
- It does not establish positive Daily expectancy; the Daily sample is only 10 rows / 7 structural keys.
- It does not authorize overnight V1 holds, a V1 policy change, or strategy promotion.
- It does not change target geometry, contract selection, premium stop, or transaction-cost assumptions.
- It is not a 212R-specific options study.
- Scanner/contract snapshots do not reveal intra-snapshot ordering.
- A target resolution can still have negative option P&L because the option is exited at executable BID, not at an assumed theoretical value.

## Reproduction

`PYTHONPATH=. python scripts/options_shadow_horizon_compatibility.py --db <read-only options_scanner snapshot> --output <result.json>`

Committed result:

`data/options_shadow_horizon_compatibility_2026_09_18/result.json`

The production database is not committed. The harness requires the exact prior 47-row AHEAD population, the exact 24-row Daily/4H subset, and post-close evidence for every row censored at the baseline horizon. Missing evidence fails closed.

## Next boundary

Do **not** tune V1 from this sample.

The clean-shadow target-geometry and Daily/4H one-session horizon tests are both complete. The evidence now argues against one blanket fix: target widening did not rescue the cohort, and a longer horizon has opposite effects on Daily versus 4H.

For 212R specifically, the prospective option-evidence lane is still blocked on recent consolidated-SIP access, while exact historical option replay remains DATA BLOCKED on causal historical Delta and contract-level open interest.

**Current overall 212R classification: UNPROVEN / WAIT.** PR #761 later classified the separate first-sight family-persistence lane as `NO LONGER SHOWING EXCESS`. This mixed-cohort horizon study remains unchanged and is not a 212R-specific expectancy test.
