# Options trigger-time refetch comparison — 2026-09-18

> **Current-state note:** PR #761 later classified the separate pre-registered first-sight family-persistence lane as `NO LONGER SHOWING EXCESS`. Current overall 212R classification is **UNPROVEN / WAIT**. The causal trigger-time comparison below remains valid and is preserved as study-local evidence.

## Study-local verdict

**PROMISING BUT UNPROVEN / WAIT**

The close-classified `cov-v0.1` study is not a faithful entry clock for Strat triggers, but the frozen five-session 2-1-2 reversal population is structurally stable under a causal first-break replay.

This report does **not** validate option profitability, market-context eligibility, target geometry, contract selection, or DEMO/live promotion.

## Evidence identity

Frozen observer reference:

- file: `options_coverage_observer.sqlite`
- observer version: `cov-v0.1`
- SHA-256: `edba1ab55e859357f38db843c48b9942e685c1b148db82e2cefafaca632df286`
- primary-20 directional events from 2026-09-09 through 2026-09-15: **944**
- the original five daily outcome runs were bound to release `771b6cfb9039f5e01d3d85a2d2bdbfaf124c95e5`

The original observer package did not preserve raw 5-minute bars. It is therefore impossible to prove first-break ordering from the frozen DB alone.

A separate, explicitly new historical SIP refetch was materialized with:

- snapshot identity: `OPTIONS_TRIGGER_BAR_SNAPSHOT / trigger-bars-v0.1`
- snapshotter commit/release: `bf26ca9ed67077fcb6f38f13eb73a0f8887fe087`
- study epoch: `NEW_REFETCH_NOT_FROZEN_COV_V0_1`
- provider/feed: Alpaca consolidated `sip`
- universe: primary 20
- sessions: 2026-09-09, 09-10, 09-11, 09-14, 09-15
- files: **10** canonical JSONL files (30m + 5m per session)
- total rows: **28,204**
- primary manifest SHA-256: `8d9cd8a4674827abc11bf60805d53ca812c738bb28417b672a10e3f98790dc1b`
- repeat manifest SHA-256: `d8839ead568075bfa61c79b615b2740bcba9e7a3ab673602349ea758e999f1db`
- committed source manifest: `docs/options-trigger-time-refetch-source-manifest-2026-09-18.json`
- committed repeat proof: `docs/options-trigger-time-refetch-repeat-proof-2026-09-18.json`

A second independent refetch produced the same 28,204 rows and **all 10 bar-file SHA-256 values matched byte-for-byte**. Its manifest differs only because `fetched_at` is intentionally part of the manifest.

## Trigger-time result

Across the primary-20 five-session refetch:

- armed 30-minute watch bars: **1,268**
- TRIGGERED: **560**
- CANCELLED: **447**
- NO_TRIGGER: **241**
- AMBIGUOUS: **20**
- first trigger bucket is resolved only to the first crossing **5-minute bar**; no exact intrabar tick-time claim is made.

Of the 560 triggered rows, 54 have no frozen close-classified directional event for that same symbol/bar. Every one of those 54 bars later finished as an outside bar. This is evidence that close classification can erase an earlier causal boundary break; it is not evidence that those triggers were profitable.

The 80 family-label changes among rows that do have a frozen old event are all outside-bar timing cases:

- 67: `OTHER:strat_outside_continuation` -> `STRAT_32_CONTINUATION`
- 13: `OTHER:strat_outside_continuation` -> `STRAT_32_REVERSAL`

No comparable triggered row changed direction.

## Frozen 2-1-2 reversal population

The frozen close-classified population contains **81 `STRAT_212_REVERSAL` rows**.

Causal first-break replay recovers:

- **81 / 81** as TRIGGERED
- **81 / 81** with the same `STRAT_212_REVERSAL` family
- **81 / 81** with the same direction
- **81 / 81** with the exact same trigger level and invalidation level
- **0** ambiguous
- **0** of those original 81 later become outside bars

First crossing 5-minute bucket within the watched 30-minute bar:

| Offset from watched-bar start | Rows |
| ---: | ---: |
| 0 min | 42 |
| 5 min | 16 |
| 10 min | 8 |
| 15 min | 6 |
| 20 min | 8 |
| 25 min | 1 |

Measured from the **start of that first crossing 5-minute bucket**, the old observer first-sight timestamp occurred **22.95 to 47.95 minutes later**; median **47.95 minutes**.

This proves the original 81-row 2-1-2 reversal structural population is not an artifact of different trigger boundaries. It also proves the old first-sight clock materially lagged the causal break clock.

## Additional causal 2-1-2 reversals

The trigger-time replay identifies **89 total `STRAT_212_REVERSAL` triggers**:

- 81 are the frozen close-classified rows above.
- **8 additional rows** were absent from the frozen close-classified directional population.
- all 8 additional rows later became outside bars.

These 8 are structurally real first-break observations under the trigger-time model. They are **not** yet approved setup additions and no expectancy claim is made. Their trigger-time market alignment, target geometry, option contract, fill, and outcome still require separate evidence.

## Remaining blockers

This comparison does not retire the backtest gate. Before trusting a trigger-time options backtest, the system still needs:

1. trigger-time SPY/QQQ/hourly/daily context recomputed causally;
2. target geometry and remaining-R:R recomputed at the trigger boundary;
3. a decision-time option-chain source for required selector fields, especially historical Delta/open interest and exact underlying price for the historical population;
4. the approved/pre-registered fill-cost/slippage policy;
5. outcomes for the 8 newly exposed 2-1-2 reversal triggers under the same causal/fill rules;
6. replay/forward parity on the final combined evidence packet.

## Safe next step

Use the now-frozen refetch bytes to recompute **market context at the trigger boundary**. Do not re-fetch those bars again for that analysis. Do not promote or tune 2-1-2 based on this timing result alone.
