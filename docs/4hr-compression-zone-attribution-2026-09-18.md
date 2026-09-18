# 4HR Compression / Continuation / Zone Attribution — 2026-09-18

> **ZONE-SEMANTICS SUPERSESSION NOTE — 2026-09-18:** the sequence/compression findings in this file remain valid. The zone-specific sections are superseded by `docs/4hr-zone-interaction-and-target-geometry-2026-09-18.md`, which re-derives zones with the exact `context.location_context` aggregation semantics from completed 15m bars.

## Verdict

**PROMISING BUT UNPROVEN / AUDIT ONLY / PAPER ONLY.**

This study does not change the 4HR strategy. It attributes the already-canonical MNQ 4HR Re-Trigger trades by completed higher-timeframe Strat structure and causal supply/demand context at entry.

Source execution evidence:
- canonical 5-minute 4HR detector;
- fixed completed-1H stop;
- canonical PaperBroker;
- pessimistic same-bar stop-first handling;
- $1.48 round-trip commission;
- 1/2/3-tick adverse slippage sensitivity;
- 80 resolved MNQ baseline trades.

Machine-readable artifact:
scripts/4hr_compression_zone_attribution_2026-09-18.json
sha256 282a9f9feb2185a97218be28a9fc49fc50ded7ae3479705e7776d24463356494.

## Baseline

MNQ 4HR canonical baseline:
- 80 resolved;
- 49 wins / 31 losses;
- 61.3% win rate;
- +$2,886.60 at 1 tick;
- +$2,831.10 at 2 ticks;
- +$2,775.60 at 3 ticks;
- H1 +$1,768.80;
- H2 +$1,117.80.

MES remains separate and is not used to derive these tags because its canonical 4HR evidence failed the walk-forward/slippage gate.
## Causal compression finding

Count of completed 4H inside bars among the five most recent completed 4H classifications at entry:

- 0 inside bars: n=20, +$1,196.90, +$59.85/trade.
- 1 inside bar: n=39, +$398.28, +$10.21/trade.
- 2+ inside bars: n=21, +$1,291.42, +$61.50/trade.

The 2+ compression cell remained positive in both halves:
- H1 n=14, +$1,056.28, +$75.45/trade.
- H2 n=7, +$235.14, +$33.59/trade.

It also survives 3-tick slippage:
- 1 tick +$1,291.42;
- 2 ticks +$1,278.42;
- 3 ticks +$1,265.42.

Interpretation: repeated recent 4H compression is a useful descriptive context, but it is not monotonic because the zero-inside group is also strong. Do not convert 'more inside bars = always better' into a rule.

## Immediate 1→2 precursor

An immediate completed-4H inside→directional precursor was not helpful:
- n=11;
- net −$105.78;
- expectancy −$9.62/trade.

The same test on 1H was worse:
- n=6;
- net −$653.88;
- expectancy −$108.98/trade.

Therefore simply requiring an immediate 1→2 before the existing 4HR entry is not supported.
## Strongest common completed-4H sequence

**4H 2→2 continuation** was the strongest adequately populated sequence:

- n=29;
- 69.0% win rate;
- +$2,085.08;
- +$71.90/trade;
- H1 n=12, +$652.24, +$54.35/trade;
- H2 n=17, +$1,432.84, +$84.28/trade.

Slippage:
- 1 tick +$2,085.08;
- 2 ticks +$2,066.08;
- 3 ticks +$2,047.08.

By contrast, 4H 2→2 reversal:
- n=15;
- +$182.80 overall;
- H1 +$272.14;
- H2 −$89.34;
- remains negative in H2 at 2- and 3-tick slippage.

So continuation is the stronger current lead; reversal is not walk-forward stable.

## Compression + continuation interaction

2+ recent 4H inside bars AND current 4H 2→2 continuation:
- n=5 only;
- +$775.60;
- +$155.12/trade;
- H1 +$407.06;
- H2 +$368.54;
- 3-tick net +$769.60.

This is directionally consistent with the compression→expansion thesis but is far too small to promote or tune around.

## Supply / demand context

Existing causal 1H/4H zone definitions were reused; no new zone algorithm was invented.

For 4H 2→2 continuation:
- aligned zone context: n=12, +$1,376.74, +$114.73/trade;
  - H1 +$1,003.60;
  - H2 +$373.14;
  - 3-tick +$1,361.74.
- against zone context: n=10, +$475.20 overall, but H1 −$51.92 / H2 +$527.12.
- neutral: n=7, +$233.14 overall, but H1 −$299.44 / H2 +$532.58.

Aligned context is the cleanest of these cells. Against/neutral cells are temporally unstable.
## Opposing zone in the path

The simple hypothesis 'continuation into opposing supply/demand should fail' is not supported as a blanket rule.

For 4H 2→2 continuation:
- target blocked by opposing zone: n=18, +$1,442.36, +$80.13/trade;
- target not blocked: n=9, +$566.68, +$62.96/trade.

Both were positive in both halves.

Room to nearest opposing zone:
- <0.5R: n=12, +$1,121.74, +$93.48/trade;
- 0.5–1R: n=3, +$316.56;
- 1–2R: n=7, +$719.14, +$102.73/trade;
- >=2R: n=5, −$148.40.

These cells show that merely encountering supply/demand does not imply rejection. The useful question is likely how price behaves at the zone — acceptance versus rejection — not whether a zone exists.

## Lookahead boundary

Causal at entry:
- completed 4H/1H bar types;
- recent inside-bar count;
- current completed sequence;
- zone alignment;
- nearest opposing zone and room.

Future descriptive only:
- whether the next completed bar later forms a 1-2-2 continuation;
- whether the next completed bar later forms a failed-2 / 1-2-2 reversal.

Future-resolution labels are not valid entry filters and must not be used to claim predictive performance.
## Classification

- MNQ canonical 4HR Re-Trigger: **PROMISING BUT UNPROVEN**.
- 4H 2→2 continuation context: **PROMISING BUT UNPROVEN**.
- 2+ recent 4H inside-bar compression: **PROMISING BUT UNPROVEN**.
- 2+ compression + 2→2 continuation: **WAIT — n=5 only**.
- 4H 2→2 reversal: **WAIT / temporally unstable**.
- immediate 1→2 requirement: **BROKEN as a proposed simple gate**.
- opposing-zone presence alone: **NOT A BLOCKING SIGNAL**.

## Safe next step

Do not modify live or paper execution.

Next offline test:
1. keep the canonical MNQ 4HR population and execution model unchanged;
2. examine **zone interaction behavior** after entry: reject/reclaim versus accept/hold through the nearest opposing 1H/4H zone;
3. keep continuation and reversal populations separate;
4. require temporal splits and 1/2/3-tick slippage;
5. only after that decide whether a zone-response tag is worth prospective collection.

No broad threshold sweep and no strategy rewrite are justified by this result.
