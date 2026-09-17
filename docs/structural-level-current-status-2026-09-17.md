# Structural-Level Research — Current Status

_As of 2026-09-17. Documentation/state only. No runtime, strategy, risk, env, broker, deployment, replay, candidate-regeneration, or outcome action is authorized by this file._

## Verdict

**PAPER ONLY / RESEARCH ONLY.**

P3 is closed. P2 specification is complete and landed. The next authorized work is limited to the R1/R2 corpus rebuilds and R3 read-only parity extraction/tooling. Stop and report before any candidate-regeneration or outcome run.

## Landed repository state

- PR #617 merged as `df18532a2054ad11d1c233601b590317f46949f4`.
  - prereg v1.3
  - P1 structural-level feature builder
  - P3 parity tooling/report
  - all admitted P3 checks pass on eligible rows
  - VWAP remains diagnostic-only / not admitted
  - C14 replay defect fixed offline in #646 (`bfcfc52`); `vwap_*` live/replay parity still unproven until the corpus is rebuilt and P3 rerun (own go)
- PR #618 merged as `b7bade85606229fc8e8e6b2040e9dbbeadfc44f1`.
  - P2 candidate-regeneration specification
  - family compatibility matrix
  - sealed-outcome extraction rule
  - parity-corpus gates
  - finding that the existing 15m replay corpora predate required `london_orb_*` fields

## P3 status

**CLOSED.**

Do not reopen P3 unless later P2/R3 evidence proves an actual contradiction in an admitted level definition.

Binding points:

- §9.4-contaminated rows are excluded from the admitted parity denominator but remain counted/reported separately.
- PWH/PWL remain admitted and pass on eligible rows.
- VWAP is `NOT_ADMITTED`, remains diagnostic, and does not contribute to P3 pass/fail.
- C14 (TradingView/Pine daily-session anchoring after a CME holiday) is **fixed offline in #646** with a general CME trade-date rule proven against Pine `time_tradingday` — no tolerance widening, no holiday-specific rule. The P3 numbers above were measured before that fix; the rebuilt-corpus parity has not been rerun.

## P2 status

**SPEC COMPLETE — CORPUS REBUILD REQUIRED BEFORE REGENERATION.**

Both the live runner and replay engine use the same `strategy/shadow_setups.evaluate_shadow_setups` candidate evaluator and the same `resolve_shadow_candidate` resolver. Remaining parity risk is therefore primarily input-source parity, not separate bracket-generation logic.

The existing `data/replay_polygon/*` 15m corpora predate `london_orb_*`, which the frozen definition requires. Reusing those corpora would silently omit London ORB-dependent events. They are not acceptable for the next regeneration stage.

## Family compatibility status

| Classification | Families / notes |
|---|---|
| `BOTH` | `strat_22_continuation_observed`, `strat_22_reversal_observed`, `strat_312_observed`, `strat_322_reversal_observed`, `strat_122_observed`, `strat_122_pullback`; bar-type/input parity still must be measured where applicable |
| `BOTH — input-divergent` | `ema_pullback_trend`, `impulse_first_pullback_observed`, `trend_consolidation_break_observed`, `strat_4hr_retrigger_observed`; Pine/live vs corpus EMA/trend/regime inputs require explicit parity measurement |
| `LIVE_ONLY` | `vwap_hold_observed`, `vwap_rejection_observed` (campaign-env / MNQ-NY; C14 replay fix landed in #646 but corpus not rebuilt / parity not rerun); `range_break_close` |
| `NOT_TESTABLE` | `transition_failed_breakdown_reclaim`; current P2 population n=12 and MES blind under C8 |
| `DEAD` | `ovn_*_sweep_reclaim`, `gap_fill`; required payload fields were never present, so they must not be represented as replay-capable populations |

`candidate_audit` is not a population source for this study: it is config-gated to `orb_breakout`, confluence-ranked, and excluded by the P2 spec.

## Frozen P-LIVE exclusion

Exclude the 62 Asian-session `orb_false_break_fade` rows built from the stale pre-2026-09-04 NY ORB. Classification: `STALE_ORB_CONTAMINATED`.

## Authorized next work

Only:

1. R1/R2 — rebuild the pinned corpora with manifests/hashes and the frozen builder.
2. R3 — read-only extractor + parity tooling.
3. Report per-family firing counts, overlap/agreement, bracket parity, input mismatch reason, and compatibility verdict.
4. Stop before candidate regeneration and before opening outcomes.

## Explicit holds

Do not:

- run candidate regeneration yet;
- open or score sealed outcomes;
- tune parameters or filters;
- widen parity tolerances;
- change strategy/risk/runtime code;
- change env or deployment state;
- activate any family because of this research status;
- treat `LIVE_ONLY`, `NOT_TESTABLE`, or `DEAD` as replay-capable.
