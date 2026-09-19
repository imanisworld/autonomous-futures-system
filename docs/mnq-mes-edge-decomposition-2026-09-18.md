# MNQ/MES Edge Decomposition — 2026-09-18

> **REPRODUCED / AUDIT ONLY.** The original local analysis is now reproduced deterministically from the exact sealed R5 candidate bytes and exact hash-pinned `replay_polygon_v2` source corpus. `scripts/r5_entry_conditioning_reproduction.py` fails closed on the frozen candidate/manifests/day-file hashes and reproduces the headline MFE/MAE, fill rates, family gross-R, and 4HR H1/H2 figures below. The result reran byte-identically with SHA-256 `dd452fc369cf37c4148790b37f40798278285d5d021929492ea18c89502177b2`. This proves the diagnostic; it does **not** promote any strategy or authorize a runtime change.

## Verdict

**AUDIT ONLY / PAPER ONLY.**

The current broad MNQ/MES structural candidate population does not fail for one universal reason. Three distinct failure modes are visible: weak raw directional signal, entry-conditioned edge loss, and stop/target geometry that is not strong enough after fills.

No runtime, strategy, risk, broker, deployment, env, collector, or execution path was changed.

## Population

Frozen R5 candidates:
- MNQ: 40,460
- MES: 39,987
- window: 2024-10-01 through 2026-06-26
- timeframe: 15m
- corpus: replay_polygon_v2
- current candidate entry, stop and target preserved

Price-path diagnostics were recomputed directly from source bars rather than inferred from P8 headline results.

## Pass 1 — raw excursion from candidate entry

At 16 bars:
- MNQ mean MFE 2.844R vs mean MAE 2.778R
- MES mean MFE 2.348R vs mean MAE 2.442R
- current stop was reached first on about 61–62% of all candidates
- current target was reached first on about 25–27%

Aggregate directionality is therefore weak. The full population is not one clean directional edge hidden by costs.

## Pass 2 — symmetric barriers before enforcing fill

Several families showed apparent directional movement before accounting for whether the resting entry actually filled:
- 4HR re-trigger: strongest raw directional asymmetry
- ORB false-break fade: clear raw asymmetry
- 1-2-2 pullback: moderate raw asymmetry
- 2-2 continuation / reversal: small raw asymmetry
- impulse-first-pullback and trend-consolidation-break: strongly adverse

This pass alone is not sufficient because it treats the candidate entry as immediately available.
## Pass 3 — require entry touch first

After requiring the candidate entry to be touched before evaluating post-entry movement, much of the apparent edge disappears.

### Clear signal failures

**impulse_first_pullback_observed**
- MNQ fill 78.1%, current gross R/all −0.174
- MES fill 77.8%, current gross R/all −0.208
- symmetric 1R good-first only ~41% MNQ / 39% MES after fill

**trend_consolidation_break_observed**
- MNQ gross R/all −0.151
- MES gross R/all −0.200
- symmetric 1R good-first ~45% / 41%

These are not primarily target-geometry problems. The post-fill directional behavior itself is poor.

### Entry-conditioning problem

Families that looked better before fill but lost most of that advantage once entry touch was required include:
- orb_false_break_fade
- strat_22_continuation_observed
- strat_22_reversal_observed
- strat_312_observed

This means the resting-entry condition is selecting a worse subset of the original signal path. These should be investigated as **entry architecture** problems before changing targets or adding filters.

ORB false-break is the clearest example:
- strong pre-fill symmetric directional edge
- post-fill symmetric edge falls near coin-flip
- current 2.5R geometry gives only +0.011 gross R/all on MNQ and −0.042 on MES before costs

## Current bracket gross expectancy

Simplified 16-bar, entry-touch-required, conservative same-bar stop-first calculation; unresolved and no-fill rows contribute 0R.

Broad families remain negative on both instruments:
- 2-2 continuation: −0.076 MNQ / −0.094 MES
- 2-2 reversal: −0.067 / −0.090
- EMA pullback trend: −0.066 / −0.095
- impulse first pullback: −0.174 / −0.208
- trend consolidation break: −0.151 / −0.200
- 3-2-2 reversal observed: −0.130 / −0.111
- 1-2-2 observed: −0.143 / −0.156
- 3-1-2 observed: −0.163 / −0.110

Transition remains slightly negative under its current sub-1R average target geometry:
- MNQ −0.047R/all
- MES −0.034R/all
### Only cross-instrument positive lead in this diagnostic

**strat_4hr_retrigger_observed**
- MNQ: +0.159 gross R/all
- MES: +0.026 gross R/all
- no-fill rate: 26.2% MNQ / 24.2% MES
- current target: 2R
- raw post-entry result is positive before costs in both instruments

Temporal split:
- MNQ H1 +0.259R, H2 +0.059R
- MES H1 −0.059R, H2 +0.109R

Interpretation: promising mechanism candidate, but not temporally stable enough to call validated. MNQ decays materially; MES changes sign. This structural observer population must also not be conflated with any separately audited active 4HR lane.

MES strat_122_pullback is also positive in this simplified calculation (+0.090R/all) but n=155 and MNQ is negative (−0.053R/all), so it is not a cross-instrument lead.

## What this changes

Do not treat the system as one generic losing strategy.

The current evidence supports three work queues:

1. **Drop / park for now:** impulse-first-pullback, trend-consolidation-break, and other families with poor post-fill directionality.
2. **Entry-architecture investigation:** ORB false-break and the 2-2 family, where pre-entry directional structure weakens after the resting entry condition.
3. **Focused robustness work:** structural 4HR re-trigger, because it is the only family in this pass with positive gross bracket expectancy on both instruments.

## Next safe test

For structural 4HR re-trigger only:
- reproduce exact canonical fill semantics instead of this simplified entry-touch model;
- include commission + calibrated slippage;
- compare the current entry with signal-close / alternative already-existing entry definitions only if those definitions are already present in the codebase or prior prereg work;
- run H1/H2 and instrument-separated results;
- do not change runtime or promote anything unless positive expectancy survives those checks.

For ORB false-break:
- decompose why entry-touch selects a worse path before changing stop/target geometry.

No broad parameter sweep is justified by this audit.

## Reproduction proof

The preservation/reproduction gate is complete for this diagnostic.

Frozen inputs:
- MNQ R5 candidates: 40,460 rows; SHA-256 `148768cd9a7b9b6f48d003d91cdf73b757b3018dca1343dc9a56b7e4f91c1a7a`;
- MES R5 candidates: 39,987 rows; SHA-256 `e50fa5525422690ceff69a4a115c78517eb9cdec51f8dee5ee004733045a7ebe`;
- MNQ corpus manifest: SHA-256 `1f16b81b232f0275753c295bbca4686dcc675eec6f0b488cff3099693103d8ac`;
- MES corpus manifest: SHA-256 `ca4481502b4f9be33de6bdb611c02ac462c36eab1b759a0e62dab45c10594de3`;
- each corpus: 543 manifest-listed day files / 40,907 bars, with every listed day-file SHA and row count rechecked by the analyzer.

Recovered method, now frozen:
1. exclude the signal bar;
2. raw excursion/current-barrier view uses the next 16 completed 15m bars;
3. the fill-first view requires physical entry touch (`low <= entry <= high`) within those next 16 bars — a gap over the entry is not a fill;
4. after fill, start a new 16-bar bracket horizon **including the fill bar**;
5. if stop and target are both reachable in one bar, resolve stop first;
6. no-fill and unresolved rows contribute 0R to gross-R/all;
7. symmetric diagnostic barriers are entry ±1R;
8. H1/H2 split is chronological candidate order at `floor(n/2)`.

The reproduction script has explicit regression tests for no gap-over fills, adverse same-bar ordering, and the fresh post-fill horizon. The same full result was generated twice byte-for-byte at SHA-256 `dd452fc369cf37c4148790b37f40798278285d5d021929492ea18c89502177b2`.

The entry-conditioning conclusion is therefore no longer a loose hypothesis: **ORB false-break has a strong pre-entry 1R directional effect that collapses toward coin-flip after the current resting entry is required**. On resolved symmetric 1R paths:
- MNQ ORB false-break: **66.7% good-first before fill -> 51.1% after fill**;
- MES ORB false-break: **64.1% -> 53.5%**.

For comparison, impulse-first-pullback and trend-consolidation remain poor even after fill, supporting their classification as signal/directional failures rather than merely entry-timing defects.

### Next gate — completed

The preregistered one-variable ORB false-break entry-architecture A/B is now complete (`docs/orb-false-break-entry-architecture-ab-2026-09-18.md`).

Holding the original absolute stop/target fixed and changing only the entry from the later resting retouch to the completed signal-bar close made both instruments worse:

- MNQ: +0.0111R/all -> -0.0596R/all;
- MES: -0.0416R/all -> -0.0847R/all;
- both chronological halves were negative under signal-close on both instruments;
- 1/2/3 adverse-tick signal-close stress worsened the result further.

All five preregistered support conditions failed. Classification: **NO RUNTIME CHANGE / MIXED_OR_UNSUPPORTED**.

Therefore the entry-conditioning diagnosis remains real, but “enter at signal close” is rejected as the fix. Any further ORB redesign is a new strategy hypothesis. The separate 2-2 entry-conditioning finding remains unanswered and requires its own preregistered test if reopened.

The structural 4HR observer result remains diagnostic only and must not be conflated with the separately audited canonical active 4HR lane.
