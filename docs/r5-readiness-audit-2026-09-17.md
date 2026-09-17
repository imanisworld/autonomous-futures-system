# R5 Readiness Audit — 2026-09-17

**Status:** AUDIT ONLY / PAPER ONLY. This document does not run R5, open outcomes, change strategy/risk/runtime/config, deploy, restart, or authorize live execution.

**Repo state audited:** `9e06e033779539c75601f94306651aa5285ebb3e`.

## Verdict

**R5 candidate regeneration is READY FOR AN EXPLICIT OPERATOR GO for the admitted MNQ/MES P-REPLAY population only.**

This is narrower than saying the structural-level study is ready for outcome analysis. R5 is the offline regeneration/extraction/seal step defined in `docs/structural-level-p2-candidate-regeneration-spec-2026-09-17.md`. The sealed outcomes must remain unopened through R6 and R7; outcome opening still requires a separate explicit go after those steps pass.

M2K is not admitted to historical P-REPLAY. MGC/MCL/MBT tranche-2 work is separate and does not join this R5.

## R1–R4 gate status

| Step | Required state | Current ruling |
|---|---|---|
| R1 | Rebuilt MNQ/MES P-REPLAY corpora for 2024-10-01 through 2026-06-26 with pinned builder/manifests | **PASS / COMPLETE** |
| R2 | MNQ/MES parity corpus through the 2026-09-14T22:00Z roll cut | **PASS / COMPLETE** |
| R3 | P2-X candidate/outcome-seal extractor and P2-P firing/bracket parity tooling with tests | **PASS / COMPLETE** |
| R4 | Live-vs-replay firing/bracket parity run and final family dispositions | **PASS FOR ADMITTED R5 INPUTS / COMPLETE** |
| R5 | Regenerate P-REPLAY candidates, split/seal outcomes, run integrity report | **NOT RUN — READY FOR EXPLICIT GO** |

The post-#621 R4 rerun materially repaired the two day-boundary-affected families while preserving the admitted family dispositions. `ema_pullback_trend` remains split into `REPLAY_ONLY` and `LIVE_ONLY` strata rather than pooled. `transition_failed_breakdown_reclaim` remains `NOT_TESTABLE`. Those dispositions are inputs to R5, not blockers requiring threshold changes.

## Exact R5 population boundary

R5 may use only the admitted historical P-REPLAY population:

- instruments: `MNQ`, `MES`;
- corpus: `data/replay_polygon_v2/{MNQ,MES}`;
- confirmatory window: 2024-10-01 through 2026-06-26, with the already-defined warm-up;
- timeframe: 15m;
- candidate source: the frozen shadow-candidate regeneration path;
- canonical VWAP observers remain OFF in P-REPLAY as preregistered;
- no M2K historical rows;
- no MGC/MCL/MBT rows;
- no `LIVE_ONLY`, `DEAD`, or `NOT_TESTABLE` family is promoted into a pooled historical statement;
- `ema_pullback_trend` remains split by provenance, never pooled across its bracket conflict.

## Items that do NOT block MNQ/MES R5

### C8

C8 is a real live-journal field defect, but `transition_failed_breakdown_reclaim` is already classified `NOT_TESTABLE` for the live comparison and is not allowed to contaminate the confirmatory pooled population. C8 still must be fixed for future live/regime evidence, but it does not require rewriting R5's frozen MNQ/MES historical population.

### C14 / VWAP

VWAP is not admitted to the confirmatory family. `vwap_*_observed` remains `LIVE_ONLY`, with C14 still under separate parity/root-cause work. C14 therefore does not block regeneration of the non-VWAP P-REPLAY population.

### M2K >=5-session parity

M2K is prospective/parity-only and has no admitted historical P-REPLAY corpus because historical roll provenance is `ROLL_PROVENANCE_UNKNOWN`. The future M2K P3/P5 rerun therefore does not block MNQ/MES R5.

### MGC/MCL/MBT tranche-2

Those instruments require product-specific definitions and X0 source/roll proof. They are a separate expansion lane and are not prerequisites for the frozen MNQ/MES study.

## R5 execution contract

When explicitly authorized, R5 must do only the preregistered offline sequence:

1. pin the exact code SHA and hashes required by the regeneration spec;
2. run the MNQ and MES P-REPLAY regeneration against the admitted R1 corpora;
3. run P2-X;
4. emit `candidates.jsonl` with no outcome fields;
5. emit `outcomes.sealed.jsonl` keyed only by the frozen candidate key;
6. record and freeze the sealed-outcomes SHA256 without opening the file for analysis;
7. emit the integrity report and fail closed on duplicate conflicts, roll-ledger failure, population mismatch, or other preregistered integrity failures;
8. stop.

No P&L analysis, hypothesis scoring, threshold tuning, feature-conditioned result, or outcome inspection belongs in R5.

## What comes after R5

R5 completion does **not** authorize outcome analysis.

- **R6:** run P1 structural feature construction over `candidates.jsonl`; freeze and hash `features.jsonl` before any sealed outcome is opened.
- **R7:** independent seeded spot-check of at least three candidate rows, re-deriving brackets/features from source bars; produce attestation.
- **Separate go only after R7:** open `outcomes.sealed.jsonl` and join by the frozen candidate key for the preregistered P-REPLAY analysis.

If R5 integrity fails, stop before R6. If R6 or R7 fails, sealed outcomes remain unopened.

## Current safety ruling

- **R5 regeneration:** `READY_FOR_OPERATOR_GO`, MNQ/MES only.
- **R5 actually run:** `NO`.
- **Outcomes opened:** `NO`.
- **R6:** `BLOCKED_ON_R5`.
- **R7:** `BLOCKED_ON_R6`.
- **Outcome analysis:** `HOLD — requires R5 + R6 + R7 + separate explicit authorization`.
- **M2K historical R5:** `BLOCKED — no admitted historical roll provenance`.
- **MGC/MCL/MBT:** `SEPARATE TRANCHE-2`.
- **Runtime/live execution:** unchanged; nothing in this audit authorizes deployment or execution.

## Safe next step

Do not invent another prerequisite. The next action in this lane, when explicitly authorized and when a repo-local execution environment with the admitted corpora is available, is the frozen **MNQ/MES R5 regeneration-and-seal run only**, followed by a stop for integrity review.