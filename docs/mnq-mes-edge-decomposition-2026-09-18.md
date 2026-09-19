# MNQ/MES Edge Decomposition — 2026-09-18

> **Preserved research note / reproduction required.** The sealed P8 artifacts independently confirm the base population counts used here (MNQ 40,460; MES 39,987). The detailed MFE/MAE, entry-touch conditioning, family gross-R, and H1/H2 calculations in this note came from an uncommitted local analysis and do not yet have a hash-bound committed script/result artifact. Treat those detailed numbers as hypotheses to reproduce before using them for a strategy ruling, implementation, or promotion decision.

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

## Reproduction gate

Before acting on the entry-architecture conclusions above:

1. freeze the exact R5 candidate files and source-bar manifests used by the sealed P8 run;
2. commit a deterministic analyzer that reproduces fill-first, MFE/MAE, barrier-order, H1/H2, and instrument-separated tables;
3. require pessimistic same-bar ordering and no future bars before entry;
4. hash the result artifact and rerun byte-identically;
5. compare any apparent 4HR result against the already-audited canonical 4HR detector so structural-observer rows are not conflated with the active lane;
6. only then preregister an ORB-false-break / 2-2 entry-architecture A/B.

Until that gate passes, this note is **AUDIT ONLY / hypothesis generation**, not a replacement for the current strategy inventory.
