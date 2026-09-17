# R7 Outcome-Seal Boundary Clarification — 2026-09-18

**Status: BINDING PROCEDURAL CLARIFICATION ONLY.** This document does not run R7, open outcomes, change any hypothesis, threshold, feature definition, candidate population, strategy, runtime path, risk rule, broker path, Pine logic, VPS state, or deployment state.

## 1. Why this clarification exists

The original prereg `docs/prereg-dynamic-structural-level-attribution-2026-09-16.md` describes P8 as an independent spot-check of at least three seeded rows re-derived end-to-end, including outcome/R, plus recomputation of at least one headline statistic.

The later and more specific regeneration sequence in `docs/structural-level-p2-candidate-regeneration-spec-2026-09-17.md`, reinforced by `docs/r5-execution-runbook-2026-09-17.md` and the merged R5 record, defines a stricter ordering:

1. R5 regenerates candidates and seals outcomes.
2. R6 builds, freezes, and hashes `features.jsonl` from `candidates.jsonl` only.
3. R7 independently spot-checks candidate bracket + structural features.
4. **Only after R7 passes, and only under a separate explicit operator go, may `outcomes.sealed.jsonl` be opened and joined.**

Those two descriptions conflict if the original P8 outcome/R clause is read as part of pre-opening R7. The outcome seal wins: R7 cannot both be a prerequisite to opening outcomes and require opening outcomes.

## 2. Binding R7 scope

For this study, **R7 is outcome-blind**.

R7 must independently re-derive at least three seeded candidate rows from source bars and verify, for each selected row:

- candidate identity and source-bar/B0 alignment;
- instrument, strategy/family, direction;
- entry, stop, target, and bracket arithmetic from the frozen candidate-generation formulas / source bars as applicable;
- the P1 structural levels used by the frozen feature builder;
- event classifications;
- H1–H6 labels;
- `NOT_AVAILABLE` / `NOT_APPLICABLE` states;
- gap-contamination flags and any warnings required by P1;
- equality against the frozen R6 `features.jsonl` row for that candidate key.

R7 must **not**:

- open, print, parse, join, filter, summarize, hash by content inspection, or otherwise read `outcomes.sealed.jsonl` beyond verifying the already-recorded sealed file hash from R5 metadata;
- compute outcome, R, P&L, win rate, expectancy, hit rate, MAE/MFE, or any headline statistic;
- rank families, features, or hypotheses;
- tune thresholds or change P1 definitions;
- start the preregistered outcome analysis.

## 3. Disposition of the original P8 outcome/statistic clause

The original P8 requirements to re-derive **outcome/R** and independently recompute **at least one headline statistic** are **deferred, not deleted**.

They move to the separately authorized post-R7 outcome-opening phase. That later phase may begin only if:

- R6 = `PASS — FEATURES FROZEN`;
- R7 = `PASS — INDEPENDENT ATTESTATION`;
- the operator gives a new explicit go to open the sealed outcomes;
- the frozen R5 outcome hashes and R6 feature hashes still match.

At that point, the independent post-open validation must still satisfy the original P8 intent before any research conclusion is accepted.

## 4. Seed selection — outcome-blind and reproducible

R7 row selection must be deterministic and must not use any outcome information.

After R6 freezes `features.jsonl`, define the seed material as:

`R7|<MNQ features sha256>|<MES features sha256>`

For every frozen candidate row, compute:

`sha256(seed_material + "|" + candidate_key)`

Sort ascending by that digest and select the first rows subject to these fixed coverage constraints:

- at least **3 total rows**;
- at least **1 MNQ** row;
- at least **1 MES** row;
- no duplicate candidate key.

If the first three digests already satisfy both-instrument coverage, use exactly those three. Otherwise continue down the sorted list only until both-instrument coverage is met. Do not substitute based on family, session, label, apparent difficulty, or any later result.

Record the seed material, selected candidate keys, and selection digests in the R7 attestation before re-derivation begins.

## 5. Independence requirement

The R7 attester must be a party/session other than the author that generated the R6 feature table. The attester may use the frozen prereg, source bars, candidate row, and P1 definitions, but must independently re-derive the checked values rather than merely rerunning and trusting the R6 output.

If independence cannot be established, verdict = `R7 BLOCKED — INDEPENDENT ATTESTER NOT ESTABLISHED`.

## 6. R7 verdicts

- `R7 PASS — INDEPENDENT ATTESTATION`: every selected row agrees with the frozen candidate/features records under the frozen rules.
- `R7 FAIL`: any checked bracket, level, event, label, availability state, gap flag, or candidate identity disagrees materially with the frozen record.
- `R7 BLOCKED`: required source bars, frozen R6 artifacts/hashes, or independent attester are unavailable.

Any FAIL or BLOCKED result stops the sequence. **No outcome file may be opened.**

## 7. Stop boundary

Successful R7 authorizes nothing by itself except returning for operator review.

A separate explicit authorization is still required before opening `outcomes.sealed.jsonl` or computing any outcome-dependent statistic.
