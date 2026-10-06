# Options contract quality gate — audit and fail-closed repair (2026-10-06)

Advisory / paper authority only. No broker submission, no scanner (`alert_ranker`)
change, no V1 rule change.

## Canonical authority

`options_manager/validation/contract_quality_gate.py` (`evaluate_contract_quality`,
`check_contract_quality_intake`). It feeds `advisory_decision` and
`plans/proof_adapter`; its validated values become `ContractPlanSnapshot`.
The production V1 scanner has its own frozen selector (`alert_ranker/paper_v1.py`),
which this PR does not touch.

## Required-field coverage

| Requirement | Status before this PR |
|---|---|
| expiration, DTE, strike, premium, bid, ask, spread %, volume, OI | present, required at intake |
| IV/event risk, theta risk | present (`none/low/moderate/high`; high blocks, moderate warns) |
| premium stop | optional on the gate; **missing stop is blocked by the canonical portfolio intake** (`test_missing_numeric_premium_stop_blocks`) |
| max contracts, max dollar risk | present; planned risk = (premium − premium_stop) × 100 × contracts must be ≤ stated max and ≤ $300 |
| distance to target | present, < 2% blocks |
| realistic target feasibility | **missing** → added optional `expected_move_percent`; target beyond it warns |
| low liquidity / wide spread must not pass quietly | blocks (volume < 100, OI < 500, spread > 10%) — **but see fail-opens below** |
| missing contract data fails closed | yes for absent fields; **no for NaN/inf** |
| 45+ DTE swings preferred / 14–44 warn / 0DTE exceptional only | present and pinned by tests |

## Fail-opens found and fixed

1. **Non-finite numbers passed.** `float("nan")` is accepted by intake coercion
   and compares False against every threshold, so a NaN `spread_percent`,
   `premium`, `bid`, `ask`, `distance_to_target`, `max_dollar_risk`, or
   `premium_stop` produced PASS. Now any non-finite numeric field blocks
   before thresholds run.
2. **Understated spread passed.** `spread_percent` was taken from the caller
   without checking it against the quote. The wide-spread rule now uses the
   wider of the supplied and quote-implied spread. No threshold changed.
3. **Crossed quote passed.** `ask < bid` now blocks.

33 of the 44 new tests fail on the base gate; all pass after the fix. Existing
options/advisory/plan/portfolio tests: 2844 passed, 1 skipped.

## Not changed

Thresholds, DTE policy, risk caps, the premium-stop optionality at gate level
(the canonical intake already blocks a missing stop), the V1 scanner selector,
and any broker/order path.
