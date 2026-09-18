# Structural-level P8 — one-shot P-OOS-MES holdout

Date: 2026-09-18
Status: **OFFLINE RESEARCH ONLY — ONE-SHOT HOLDOUT CONSUMED**

## Verdict

**PARTIAL REPLICATION / PROMISING BUT UNPROVEN / WAIT FOR P-OOS-PROSPECTIVE**

The preregistered P-OOS-MES holdout was opened exactly once for the frozen H1/H3 wick-reject finding.

- H1 preserved sign and barely cleared its frozen 50%-of-in-sample effect floor.
- H3 preserved sign but failed its frozen effect floor.
- Neither H1 nor H3 sign-reversed, so K5 is **not** triggered.
- Because H1 and H3 were already shown in P8 to be two views of the same underlying wick-reject event, this mixed result is **not confirmation** of the event.
- No runtime, strategy gate, paper lane, DEMO route, broker route, config, or VPS change is justified.

Do **not rerun this holdout** as a second confirmatory attempt. P-OOS-MES is consumed.

## Frozen holdout

Corpus:
- `data/replay_polygon/MES_oos_2026-07-24_2026-09-08`
- 40 MES day files
- 3,003 bars
- first bar: 2026-07-24T00:00:00Z
- last bar: 2026-09-08T18:30:00Z
- previously documented concatenated per-file-hash digest: `119d65060c057ad693948f310a123b2cd755558a7379b891e718d790d8221c82`

The corpus predates the later MANIFEST convention, so no manifest was invented. The R5 extractor legally records no corpus-manifest hash when none exists.

## Deterministic R5 regeneration

The 40 day files were replayed twice with the frozen replay path.

Both passes produced:
- 3,003 evaluated bars;
- 3,080 unique candidates;
- byte-identical `candidates.jsonl`;
- byte-identical `outcomes.sealed.jsonl`.

Pinned hashes:
- candidates: `10ffb5abc68b877b208ebf4baf463f39a26710b511ace60697ee3dacde9cb65f`
- sealed outcomes: `00c5b46086d6f167f719671013c728d93fc1821e73f26890750081ea56b80143`

The outcomes remained sealed through candidate regeneration and R6 feature construction.

## R6 structural features

R6 joined all 3,080 candidate rows with no duplicate keys.

Feature hash:
- `4b8a12a2ef98810c9e40182bb512015f831cebe5f75b0beb4124af7e75308c4f`

Frozen label census:
- H1: 396 T / 361 F / 2,323 NOT_APPLICABLE
- H3: 512 T / 126 F / 2,442 NOT_APPLICABLE

The R6 manifest records `outcome_files_read: []`.

## One-shot outcome result

Terminal population:
- 2,380 terminal outcomes
- win rate 0.2550
- mean net R −0.3788

### H1 — sweep/reclaim vs touch

Eligible terminal rows: **563** (317 T / 246 F)

- mean T: −0.3913 R
- mean F: −0.4556 R
- effect: **+0.0644 R**
- frozen OOS floor: **+0.061 R**
- floor result: **PASS, narrowly**
- permutation p: 0.43786
- Holm p: 1.0
- day-block 95% CI: [−0.1215, +0.2828]
- walk-forward folds: −0.1772 / +0.1389 / +0.2518
- walk-forward consistency: **FAIL**
- session×family-adjusted effect: +0.0163 R
- families with n≥30 sharing positive sign: 1 / 4

### H3 — wick-reject vs proximity-only

Eligible terminal rows: **491** (400 T / 91 F)

- mean T: −0.3712 R
- mean F: −0.4330 R
- effect: **+0.0618 R**
- frozen OOS floor: **+0.098 R**
- floor result: **FAIL**
- permutation p: 0.35596
- Holm p: 1.0
- day-block 95% CI: [−0.1743, +0.3257]
- walk-forward folds: −0.0541 / +0.0160 / +0.2377
- walk-forward consistency: **FAIL**
- session×family-adjusted effect: +0.0515 R
- families with n≥30 sharing positive sign: 1 / 4

## Binding interpretation

The preregistered OOS rule was sign agreement plus at least 50% of the in-sample MES effect:
- H1 threshold ≈ +0.061 R — met at +0.0644 R;
- H3 threshold ≈ +0.098 R — missed at +0.0618 R.

Both signs remain positive, so there is no OOS contradiction/K5 kill. But the single underlying wick-reject finding does **not** cleanly replicate because its two frozen views disagree on the magnitude threshold, both confidence intervals cross zero, both fail walk-forward consistency, and the conditioned T rows remain negative expectancy.

Therefore:
- do not promote this feature to a strategy gate;
- do not start a new runtime collector for it;
- do not rerun/tune against this consumed holdout;
- retain the classification **PROMISING BUT UNPROVEN**;
- next confirmatory evidence is the already-frozen **P-OOS-PROSPECTIVE** window, evaluated unconditionally after its calendar/sample-size gate is met.

## Pinned artifacts

- `docs/structural-level-p8-artifacts/p8_oos_mes_analysis.py`
- `docs/structural-level-p8-artifacts/p8_oos_mes_results.json`
- `docs/structural-level-p8-artifacts/p8_oos_mes_r5_manifest.json`
- `docs/structural-level-p8-artifacts/p8_oos_mes_r6_manifest.json`

Hashes:
- OOS analysis wrapper: `d49ed8013a8c5a9c7a93553028a2701fa6ec08a16d43c3946fc44cb7cb8ece68`
- result JSON: `51d97a33c189f9a0e08173269a1c9dbbb49dc669a738a22777c56ccc6f64a7ae`
- R5 manifest: `6ccd790401225379fe36cbc229a5bf7e1fb4796dc0d065f850f2eeb5146c839f`
- R6 manifest: `11d7bddf829a764bc80819246fb77a8375460b02f52da01f12501f80eb69b683`

The wrapper is a mechanically derived MES-only form of `scripts/structural_level_p8_outcome_analysis.py` (source hash `f8bd4242a394e2aa12464db1ccca3577624080baba275fa7bf6183cc59e9f4b9`) with only the one-instrument frozen hashes and the final pooled-summary assumption changed. The hypothesis definitions, exclusions, cost model, permutation seed/count, and analysis calculations are unchanged.
