# Candidate-Regeneration Compatibility Addendum — 2026-09-17

This addendum records **replay/candidate-regeneration compatibility only**. It does not change any strategy's evidence verdict, deployment status, runtime enablement, or risk posture in `Strategy_Inventory.md`.

Source: landed P2 specification in PR #618 (`b7bade85606229fc8e8e6b2040e9dbbeadfc44f1`). No outcomes were opened to create this classification.

## Compatibility classes

| Class | Meaning |
|---|---|
| `BOTH` | A candidate family exists on both live and replay paths using the shared shadow evaluator/resolver. Input parity still must be measured where noted. |
| `BOTH — input-divergent` | The family exists on both paths, but one or more live-vs-corpus inputs are known to come from different source constructions and require explicit parity proof. |
| `LIVE_ONLY` | The live family cannot currently be represented faithfully by the approved replay corpus/spec. Do not treat absence from replay as a zero-candidate result. |
| `NOT_TESTABLE` | Current evidence/spec does not permit a valid parity test. |
| `DEAD` | The detector reads payload fields that were never present in the source population. Do not represent the family as replay-capable without a separate, explicit redesign. |

## Family matrix

### BOTH

- `strat_22_continuation_observed`
- `strat_22_reversal_observed`
- `strat_312_observed`
- `strat_322_reversal_observed`
- `strat_122_observed`
- `strat_122_pullback`

Bar-type/input parity remains an R3 measurement requirement where applicable.

### BOTH — input-divergent

- `ema_pullback_trend`
- `impulse_first_pullback_observed`
- `trend_consolidation_break_observed`
- `strat_4hr_retrigger_observed`

Known issue: live/Pine inputs and rebuilt-corpus EMA/trend/regime inputs are not automatically identical merely because the evaluator and resolver are shared.

### LIVE_ONLY

- `vwap_hold_observed`
- `vwap_rejection_observed`
- `range_break_close`

The VWAP families remain additionally blocked by C14, the unresolved daily-session anchoring divergence after a CME holiday. They are not part of the admitted P3 level family.

### NOT_TESTABLE

- `transition_failed_breakdown_reclaim`

P2 observed n=12 in the relevant live snapshot and retains the MES blindness limitation under C8. Do not convert that into a replay verdict.

### DEAD

- `ovn_*_sweep_reclaim`
- `gap_fill`

The payload fields consumed by these families did not exist in the source population. They are not valid replay-capable families under the current architecture.

## Population-source exclusion

`candidate_audit` is excluded from the candidate-regeneration population. It is config-gated to `orb_breakout` and confluence-ranked; it is not the canonical shadow-candidate population used by both live and replay paths.

## Current data blocker

The existing `data/replay_polygon/*` 15m corpora predate the `london_orb_*` fields required by the frozen structural-level definition. R1/R2 must rebuild the corpus before any regeneration run. Reusing the old corpus would silently suppress London ORB-dependent candidates/events.

## Safety interpretation

This matrix is a source/path classification, not a promotion decision. It must not be used to:

- enable a family;
- infer strategy quality;
- infer zero candidates from an unsupported replay path;
- bypass C14;
- open sealed outcomes;
- start candidate regeneration before the R1/R2/R3 parity report is frozen.
