# 4HR Pre-Armed Touch A/B — 2026-09-18

## Verdict

**PROMISING BUT UNPROVEN / AUDIT ONLY / PAPER ONLY.**

Population: same 81 canonical MNQ 4HR Re-Trigger candidates.
No signal population, target, commission, or candidate dates were changed.
Three entry models were compared at 1/2/3 adverse ticks:

1. old trigger-price backfill after completed 5m detection;
2. current-style completed-5m close IOC with 8-tick tolerance;
3. pre-armed stop-market touch on the first 5m bar whose range crosses the trigger.

The pre-armed model also fixes the stop anchor to the last 1H bar that was genuinely complete before the triggering 5m bar began.
## 1-tick results

Old trigger-price backfill:
- 80 fills / 1 excluded
- 49 wins / 31 losses
- +$2,886.60
- PF 1.696
- H1 +$1,768.80
- H2 +$1,117.80

Completed-5m close IOC8:
- 37 fills / 44 no-fill-or-excluded
- 18 wins / 19 losses
- +$1,266.24
- PF 1.846
- H1 +$43.38
- H2 +$1,222.86
- 38 ENTRY_NOT_FILLED
- 5 ENTRY_BRACKET_INVALID_AT_FILL

Pre-armed stop-touch:
- 80 fills / 1 excluded
- 40 wins / 40 losses
- +$1,414.60
- PF 1.299
- H1 +$861.80
- H2 +$552.80
- 36 same-trigger-bar pessimistic resolutions
## Slippage robustness

Pre-armed stop-touch remained positive at all tested costs:
- 1 tick: +$1,414.60, PF 1.299
- 2 ticks: +$1,354.60, PF 1.284
- 3 ticks: +$1,294.60, PF 1.269

Both chronological halves remained positive at 3 ticks:
- H1 +$802.80
- H2 +$491.80

The pre-armed model earns materially less than the old trigger-price study, which confirms the old study overstated executable edge, but it retains positive expectancy under the conservative same-bar treatment.
## Timing / stop-anchor defect

Two MNQ candidates closed their triggering 5m bar exactly at 10:00 ET and received a different stop when stop selection was made causally from information available before the trigger bar:

- 2025-01-14 SHORT: old stop 21129.25 vs causal 21139.00 (39 ticks different)
- 2026-03-10 SHORT: old stop 25081.00 vs causal 24999.50 (326 ticks different)

This confirms the completed-5m implementation can use a 1H candle that was not complete at the actual intrabar trigger time.

## Interpretation

The five-minute close is not required by the documented 4HR trigger rule. A pre-armed touch model substantially increases executable participation relative to decision-close IOC and remains profitable in both halves under 1/2/3-tick slippage.

This does not authorize a runtime change. The result supports building a lower-latency paper trigger path and replay parity test.
## TradingView implication

The higher-timeframe setup should remain based on its existing completed structural bars. Only the trigger lane needs lower latency.

Preferred next paper-only implementation:
- keep 4H/1H setup logic unchanged;
- arm the exact trigger level once the setup is valid;
- receive a 1-minute or intrabar crossing event for the armed level;
- do not wait for a 5-minute close;
- calculate the stop from the last 1H candle genuinely completed at trigger time;
- keep existing fail-closed bracket, max-trades, loss, and paper/demo isolation controls.

A 3-minute lane may be tested later, but 1-minute provides the cleaner latency reduction and is the first candidate for parity testing.

Machine-readable artifact: `scripts/4hr_prearmed_touch_ab_2026-09-18.json`
sha256 `e19671fc9c5402aa96ba20f2fd29e0c1fc6d129b1bca88aef2f8331b76da074e`.
