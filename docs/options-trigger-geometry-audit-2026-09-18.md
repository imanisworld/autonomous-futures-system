# Options trigger-time geometry audit — 2026-09-18

## Status

**PARTIALLY PROVEN / WAIT.**

The trigger-time clock is now separated from the old delayed first-sight clock, but the target/stop rules are not uniform across Strat families. This audit compares source-defined family geometry with the existing generic structural target finder on the frozen primary-20 SIP snapshot.

Snapshot manifest SHA-256: `8d9cd8a4674827abc11bf60805d53ca812c738bb28417b672a10e3f98790dc1b`.

No runtime, scanner activation, broker path, option order, or promotion decision is changed.

## External rule basis

Primary source:
- https://thestrat.ai/docs/types-of-reversals/
- https://thestrat.ai/docs/3-2/
- https://thestrat.ai/docs/trading-continuation/

The reversal rule defines the trigger as the previous bar's high/low and the target as the far side of the bar before the trigger bar. For 2-1-2 and 3-1-2, the stop is the other side of the inside bar. For 2-2 / 3-2-2 reversals, the stop is entry-bar based and cannot be reconstructed from the 30m precursor alone.

The 3-2 source explicitly says it has no magnitude of its own and that the stop should be tight at the trigger line, not the far side of the outside bar.

## Audit result

The trigger-time corpus contains **560** currently admitted first-break rows. Of those, **332** have a source-defined target under the reversal rules.

Across those 332 rows:

| Target rule | Match source magnitude | Before source magnitude | Beyond source magnitude | Generic invalid |
|---|---:|---:|---:|---:|
| Existing nearest structural target | 205 | 120 | 3 | 4 |
| Existing >=1R floor target | 47 | 29 | **202** | 54 |

The >=1R floor therefore frequently skips the canonical Strat magnitude and reaches for a farther structural level. It must not be described as canonical Strat target geometry.

## 2-1-2 reversal

Trigger-time population: **89**.

- source stop defined: 89/89
- existing generic invalidation matches source stop: **89/89**
- source target defined: 89/89
- median source target reward/risk: **0.3333R**
- source target below 1R: **75/89**
- source target on invalid/non-favorable side: **3/89**

Nearest structural target versus source target:
- match: 60
- before source magnitude: 25
- beyond source magnitude: 3
- invalid: 1

>=1R floor target versus source target:
- match: 8
- before source magnitude: 5
- beyond source magnitude: **62**
- invalid: 14

Underlying-only source-geometry path result:
- target first: 61
- stop first: 20
- unresolved at close: 5
- invalid geometry: 3

This is not option expectancy. It proves that the old >=1R target-floor rescue is not equivalent to the source-defined 2-1-2 reversal magnitude.

## Other family adjustments

- **3-1-2 reversal:** source target and inside-bar stop are defined. In this corpus 15 reversal rows had complete source geometry; median source magnitude was 0.7128R and 10/15 were below 1R.
- **2-2 reversal / 3-2-2 reversal:** source target is defined, but the stop is entry-bar based. A 30m far-side stop must not be silently substituted.
- **2-1-2 continuation:** inside-bar stop is defined; standalone continuation target remains unresolved in this audit.

- **1-2-2:** target/stop are not sufficiently frozen here; keep unresolved.
- **3-2:** no own magnitude and no far-side-of-3 stop. It needs higher-timeframe magnitude plus a tight trigger-line stop.
- **same-direction 2-2-2 / 3-2-2 continuation:** already fail closed as entry families on current main; treat them as run/context unless a separate lower-timeframe entry rule is proven.

## Consequence

The trigger-time correction is not just a latency repair. It exposes a second modeling problem: a single generic target finder cannot stand in for every Strat family's source-defined magnitude and stop logic.

For 2-1-2 reversal specifically, the entry and stop are structurally stable, but the target rule used in prior >=1R-floor tests is materially different from the source-defined magnitude. Any future 212R option backtest should carry both facts explicitly:
1. source magnitude / exhaustion target;
2. any farther structural runner target as a separate management hypothesis, never as a replacement for magnitude.

## Still missing

- lower-timeframe entry-bar stop definition for 2-2 / 3-2-2 / 1-2-2 families;
- causal option contract and quote evidence at the trigger timestamp;
- option costs/slippage applied to the final family-specific geometry;
- prospective evidence under the corrected timing + geometry rules.

No strategy is promoted by this audit.
