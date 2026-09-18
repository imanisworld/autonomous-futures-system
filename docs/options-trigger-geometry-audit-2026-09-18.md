# Options trigger-time geometry audit — 2026-09-18

## Verdict

**PROMISING BUT UNPROVEN / WAIT.**

The causal trigger-time correction is now separated from target geometry. For the exact frozen 81-row 2-1-2 reversal population, trigger/invalidation structure is stable, but the existing generic >=1R target-floor rule is **not equivalent** to source-defined Strat magnitude.

This audit is research/evidence only. It does not change the scanner, production target policy, contract selection, risk, broker/order routes, DEMO eligibility, or live trading.

## Evidence identity

Trigger-bar snapshot:

- snapshot id: `OPTIONS_TRIGGER_BAR_SNAPSHOT`
- version: `trigger-bars-v0.1`
- feed: Alpaca consolidated SIP
- sessions: 2026-09-09 through 2026-09-15
- manifest SHA-256: `8d9cd8a4674827abc11bf60805d53ca812c738bb28417b672a10e3f98790dc1b`

Frozen observer reference:

- observer version: `cov-v0.1`
- source DB SHA-256: `edba1ab55e859357f38db843c48b9942e685c1b148db82e2cefafaca632df286`
- universe for this audit: the primary 20 symbols carried by the trigger snapshot

The audit refuses either evidence source when its SHA-256 does not match. Frozen 2-1-2 reversal membership is joined on symbol, session date, watched-bar start, direction, trigger, and invalidation.

**81 / 81 expected frozen 2-1-2 reversal rows matched exactly.**

The eight additional trigger-time 2-1-2 reversals identified by #722 remain a separate descriptive population; they are not silently added to the frozen 81.

## External rule basis

Primary rule references:

- https://thestrat.ai/docs/types-of-reversals/
- https://thestrat.ai/docs/3-2/
- https://thestrat.ai/docs/trading-continuation/
- https://thestrat.ai/docs/2-2-2-continuation/

For the geometry encoded here:

- 2-1-2 reversal: trigger at the inside-bar break, stop at the opposite side of the inside bar, magnitude at the far extreme of the preceding 2;
- 3-1-2 reversal: same inside-bar risk box, magnitude at the far extreme of the preceding 3;
- 2-2 / 3-2-2 reversal: source magnitude can be identified, but the entry-bar stop is not inferred from the 30m precursor;
- direct 3-2: no magnitude of its own; a far-side-of-3 stop is not substituted;
- unproven continuation/1-2-2 geometry stays unresolved.

Equality is handled explicitly. Because the shared Strat classifier treats an equal high/low as not breaking that side, an inside bar may share an extreme with its parent. When the source magnitude then equals the trigger, the row is labeled `TARGET_CONSUMED_AT_ENTRY`, not a wrong-side target and not a win.

## Frozen 81-row 2-1-2 reversal result

Structural binding:

- frozen rows expected: **81**
- exact rows matched: **81**
- source stop matches retained trigger invalidation: **81 / 81**
- wrong-side source targets: **0**
- source magnitude already consumed at entry: **3**

Source-defined magnitude:

- median reward/risk: **0.3333R**
- source magnitude below 1R: **67 / 81**
- source-defined underlying path:
  - target first: **60**
  - stop first: **13**
  - unresolved at close: **5**
  - target consumed at entry: **3**

These are underlying-path geometry observations, not option expectancy.

### Existing nearest structural target versus source magnitude

| Relation | Rows |
|---|---:|
| Match source magnitude | 53 |
| Before source magnitude | 24 |
| Beyond source magnitude | 3 |
| Generic invalid | 1 |

The nearest-level finder often coincides with source magnitude, but not universally.

### Existing >=1R floor target versus source magnitude

| Relation | Rows |
|---|---:|
| Match source magnitude | 8 |
| Before source magnitude | 5 |
| **Beyond source magnitude** | **57** |
| Generic invalid | 11 |

The >=1R floor therefore reaches beyond the source-defined 2-1-2 reversal magnitude on **57 / 81** frozen rows. That floor is a separate trade-management hypothesis; it must not be represented as the source-defined Strat target.

## Broader trigger-time corpus

The frozen trigger snapshot produces **560** currently admitted first-break rows across the supported research families. Of those, **332** have a source-defined target under the reversal rules.

Across those 332 rows:

| Generic target rule | Match source | Before source | Beyond source | Generic invalid |
|---|---:|---:|---:|---:|
| Nearest structural target | 205 | 120 | 3 | 4 |
| >=1R floor target | 47 | 29 | **202** | 54 |

This broader table is diagnostic only. It does not make every family eligible for backtesting or promotion. Family-specific stop/target rules that are not proven remain unresolved.

## Interpretation for 212R backtesting

The original 81-row structural population survives the causal trigger-time correction, but the target semantics need to be explicit before option-side outcome testing.

For an honest 2-1-2 reversal backtest, preserve these as separate concepts:

1. **source magnitude / exhaustion target** — the preceding 2's far extreme;
2. **farther structural runner target** — if studied, a separate management hypothesis;
3. **inside-bar invalidation** — the opposite side of the inside bar.

Do not silently replace source magnitude with a >=1R floor. Doing so changes the strategy being tested.

The three `TARGET_CONSUMED_AT_ENTRY` rows have no remaining source magnitude at the trigger and must not be counted as ordinary target wins.

## What remains blocked

This audit does **not** retire the options backtest gate. Remaining blockers include:

- historical decision-time option Delta/open interest and exact underlying-price provenance for the trigger-time population;
- a frozen/pre-registered fill-cost/slippage policy and aggregate stress result;
- option contract/fill outcomes keyed to the corrected trigger-time decision clock;
- final replay/forward parity for the combined strategy + selector + fill + risk packet;
- untouched chronological / multi-month validation.

The eight additional later-outside trigger-time reversals are not added to the frozen 81 until their full trigger-time context, contract evidence, fill path, and outcomes are evaluated under the same rules.

## Safe next step

Keep 212R **WAIT**.

Use the SHA-bound trigger-time population and this source-defined geometry to define the decision boundary for future option-side evidence. Do not tune production V1, widen targets, or promote the family from these underlying-path counts alone.
