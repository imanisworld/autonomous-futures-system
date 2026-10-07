# Options strategy fitness / edge kill-switch (2026-10-06)

Code: `options_evidence/fitness.py`. This is a research and authority layer, separate from account
safety. It is not wired to any runtime.

## States

| State | Meaning | Who sets it |
|---|---|---|
| COLLECTING | no evidence against the epoch yet, or no reference to judge against | evaluator |
| WARNING | prospective R sits in the OOS lower tail, a fail-level tail appeared before the first review checkpoint, or the share of invalid evidence is too high | evaluator |
| FAIL_CANDIDATE | at a review checkpoint, the tail probability is below the fail threshold | evaluator |
| SUSPENDED | execution authority revoked | evaluator (automatic, on FAIL_CANDIDATE) or human |
| RETIRED | epoch closed | **human only** |

## What is compared

Only **verified canonical prospective catches** judge the epoch. A valid fitness observation meets all of these:
- same strategy and epoch
- constructed through `Observation.from_records` from a canonical #1151 signal record that passes `verify_record(..., registry=...)`
- `prospective_catch = true`
- trade basis is `executed` or `paper_equivalent` (never `counterfactual`)
- `executed` is an exact boolean and agrees with the P&L basis
- `data_integrity = VALID`
- `signal_integrity = VALID`
- if the trade was executed, `execution_integrity = VALID`
- `result_R` is a finite non-boolean number
- verified under **this** epoch definition: the signal record's `epoch_definition_sha256` equals the judged epoch's `definition_sha256` (otherwise `epoch_definition_mismatch`)
- one observation per `signal_id`: a duplicated or replayed row raises instead of counting twice

Direct `Observation(...)` construction is non-authoritative by design: it cannot set the internal canonical-provenance flag used by classification. This prevents hand-built rows from becoming fitness evidence.

A bad fill counts as an execution problem, not evidence about the edge. Exclusions are counted by reason. If the invalid share is too high, the result is a WARNING about evidence quality.

The observations are compared against the epoch's preregistered, untouched OOS R outcomes as recorded in the epoch registry. The evaluator requires the exact `StrategyEpoch` object held by the supplied registry (default: committed registry), and re-checks that epoch at use time, because a caller-built `EpochRegistry` skips the registry file's validation:
- `definition_sha256` must equal `definition_hash(definition, thresholds)`;
- the OOS reference, if present, must be a non-empty tuple of finite non-bool R outcomes.

`definition_sha256` does **not** cover `oos_reference`, and the OOS `artifact_sha256` is not re-verified against `r_outcomes` here; the OOS values are trusted as recorded in the registry. The verdict records the `epoch_definition_sha256` it was computed against. A seeded bootstrap estimates two probabilities from that OOS distribution:
- `p_cumulative_r`: the chance of a cumulative R this bad over n trades
- `p_drawdown`: the chance of a max R drawdown this deep over n trades

Profit factor is not used. A positive total with an impossible drawdown can still fail (see the test).

The verdict also reports:
- mean MAE_R and MFE_R
- executed count
- net P&L

**Policy (`FitnessPolicy`):**
- Defaults: review checkpoints 10/15/20, warn tail 0.10, fail tail 0.02, max invalid share 0.25.
- These are placeholders. They are not statistical truth. Preregister the policy with each epoch before collection starts.
- FAIL_CANDIDATE is reachable only at a checkpoint.

## Authority asymmetry

- `apply_verdict` can only revoke authority. It suspends when either of these holds:
  - the verdict is FAIL_CANDIDATE
  - the strategy holds authority but has no OOS reference
- Once a strategy is SUSPENDED or RETIRED, that status is sticky against the evaluator, and it never holds authority there: `AuthorityState` refuses that combination, and `apply_verdict` revokes it if it is ever met.
- `apply_verdict` accepts only evaluator verdict states (COLLECTING, WARNING, FAIL_CANDIDATE); a hand-built SUSPENDED/RETIRED verdict is refused, so RETIRED stays human-only.
- `apply_verdict` never sets `execution_authority` to true and never touches `observer_enabled`.
- `human_grant` is the only way to grant or restore authority. It requires all of these:
  - a named approver who is not the evaluator
  - an approval reference
  - `restore_from_suspension=True` when the strategy is SUSPENDED
- `human_grant` refuses a RETIRED epoch and a FAIL_CANDIDATE.
- A static test proves the evaluation path never references `human_grant`.
- Account safety caps ($300 per trade, $1,000 aggregate) stay in the risk gates. The module imports nothing from `risk`, `execution`, `options_manager`, `alert_ranker`, or `webhook`.

## Required proofs (tests)

1. Failure revokes: FAIL_CANDIDATE at a checkpoint moves the strategy to SUSPENDED with `execution_authority=false`.
2. Healthy cannot enable:
   - A healthy verdict leaves authority false, however many times it is applied.
   - A healthy verdict never lifts a suspension.
   - No verdict state ever sets authority to true.
3. The observer continues: `observer_enabled` stays true through suspension and retirement, and a suspended epoch keeps accumulating observations.

## Current applicability

`122-IEX-E1` has no OOS reference, and its stop and target are UNRESOLVED. Its verdict is
therefore always `COLLECTING` / `no_oos_reference`. If any authority were ever attached to it, that authority would be revoked.
No currently registered epoch can reach a fitness verdict that means anything. This module is still source-only and is not wired into runtime execution or alert authority.
