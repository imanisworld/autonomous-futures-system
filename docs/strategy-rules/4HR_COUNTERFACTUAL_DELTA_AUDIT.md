# 4HR Counterfactual Delta Audit

## Purpose

Perform a narrow audit of the new 4HR market-entry/TRENDING counterfactual
package. Do not reread or rerun the three-strategy baseline study.

## Files in scope

- `research/replay_4hr_retrigger_honest.py`
- `research/replay_4hr_market_counterfactual.py`
- `tests/test_replay_4hr_retrigger_honest.py`
- `tests/test_replay_4hr_market_counterfactual.py`
- `docs/strategy-rules/HONEST_FILL_REPLAY_RESULTS.md`
- `docs/strategy-rules/4HR_ReTrigger_Rules.md`
- `docs/strategy-rules/Strategy_Inventory.md`

## Required checks

1. **Accounting**
   - Confirm exactly 32 baseline `IOC_CANCELLED` crossings.
   - Confirm market replay produces 22 valid fills plus 10 invalid brackets.
   - Confirm all ten invalid market brackets are
     `TARGET_ALREADY_PASSED`, not non-protective stops.
   - Confirm combined valid fills equal 41 original IOC + 22 market = 63.

2. **Market-entry bracket validity**
   - Entry is the completed crossing-bar close plus/minus two adverse ticks.
   - Stop is the last completed 1H boundary at the actual entry timestamp and
     remains fixed.
   - Target remains the resolved prior-4PM structural boundary.
   - Enforce `stop < fill < target` for LONG and
     `target < fill < stop` for SHORT.
   - Invalid brackets fail closed and never enter P&L.

3. **Causal TRENDING gate**
   - Join the cached historical market-condition label at the exact completed
     crossing-bar timestamp.
   - Confirm the gate uses only information available when the entry decision
     is made.
   - Preserve the qualification that Polygon-derived labels are a historical
     replay proxy, not proven exact TradingView/Pine parity.

4. **Policy boundary**
   - Confirm the ordinary executable-style 4HR result remains the 41-fill IOC
     result: +$1,960.16, PF 2.33.
   - Confirm the 22 market fills and the combined 45-fill TRENDING result remain
     explicitly research-only.
   - Confirm no runtime, broker, configuration, deployment, or entry-policy
     change promotes market entry.

## Acceptance result

Return only:

- PASS or FAIL for each of the four checks;
- exact discrepancies with file and line references;
- confirmation that no executable market-entry policy was introduced.

Do not propose new performance filters or rerun unrelated strategies.
