# Preregistration — MNQ Daily 2-2 Trigger-Timing / Entry-Architecture Audit — 2026-09-18

## Scope

Audit only. No runtime, strategy, risk, broker, collector, environment, or
deployment change is authorized by this work.

Question: does the active Daily 2-2 paper lane's completed-5m decision-close IOC
representation materially differ from a causally implementable first-break
architecture?

This is not a profitability-validation exercise. It is a mechanics audit.

Base repository commit before outcomes:
`3fbd20c7a28494320ef1fc4f080ec0de0229a3c4`.

Rule source:
`context/daily_22_swing_collector.py`.

Activation source for historical-baseline reproduction:
`9caaaa3e2fbe5cb6ff20941229e3498794bcb6de` (#545).
## Frozen data

Primary corpus:
`data/replay_corpus_v1_5m_4hr_audit/MNQ`.

- 621 daily files;
- 2024-07-02 through 2026-06-26;
- frozen tree SHA-256:
  `7f09a7f82ee282e892f5db9b86be3130e6a5c1210d86828a56b9666bb06afd35`.

Do not add later forward bars or another corpus after seeing results.

The corpus contains the 5m OHLC plus the existing reconstructed context fields
used by the Daily collector: market condition, trend strength/direction,
relative volume, EMA 9/21/55, and volume metadata.

## Rule identity

The following activation functions are unchanged at the prereg base:
- `_candidate_for_current_bar`;
- `_context_gate`;
- `_expected_ioc_fill`;
- `_actual_rr`;
- `_broker`;
- `_resolve_position`.

Only trading-day/state/provenance mechanics changed later (#775/#778).
## Gate 0 — historical baseline reproduction

Before interpreting any new timing model, reconstruct the original activation
contract using the legacy #545 trading-day identity:

- 17:00-18:00 ET maintenance gap excluded;
- timestamp >=18:00 ET maps to local date + 1;
- otherwise local date;
- previous completed Daily bar must classify 2U or 2D;
- current trading day uses its first break of the prior Daily high/low;
- same 5m bar breaking both sides fails closed;
- continuation only when break direction matches the prior Daily 2 direction;
- strict context bundle unchanged;
- 8-tick IOC from decision close;
- 1 adverse entry tick;
- actual-fill R:R >=2.0;
- planned risk <=$1,750;
- one open swing maximum;
- static stop/2R target, multi-day hold;
- $1.48 commission and pessimistic same-bar handling.

Required historical reference:
- 34 non-overlapping trades;
- net +$13,885.18;
- PF 2.02;
- both chronological halves positive;
- 2024/2025/2026 positive;
- max drawdown 25.15%.
If the reconstructed activation baseline does not reproduce those values within
rounding tolerance, stop outcome interpretation and classify:
`BASELINE PROVENANCE MISMATCH / HOLD`.

Do not tune logic to force the reference result.

## Gate 1 — current trading-day identity

Rebuild the structural population with the current #775
`cme_trading_day(..., "MNQ")` identity while preserving the 17:00-18:00 ET
maintenance gap.

Report:
- candidate count under legacy identity;
- candidate count under current identity;
- dates added/removed/changed;
- whether any difference is solely a holiday/weekend trade-date correction.

All later timing comparisons use the current #775 identity.

## Structural population

For every current-identity trading day:
1. require at least two completed prior Daily sessions with >=200 5m bars each;
2. classify prior Daily relative to two-back;
3. require prior Daily type 2U or 2D;
4. find the first current-day bar that strictly breaks prior high or prior low;
5. fail closed if that same bar breaks both boundaries;
6. continuation requires break direction to match prior Daily type.

The first boundary break consumes the day's opportunity even if later gates
reject it.
## Diagnostic A — raw close detachment

This diagnostic is outcome-independent.

For every structural continuation first-break bar, report:
- direction;
- prior-day boundary;
- planned entry (boundary +/- 1 tick);
- trigger-bar open/high/low/close;
- close minus planned entry in signed ticks;
- favorable vs adverse close detachment;
- gap-through at bar open;
- whether completed-close IOC8 would be marketable;
- actual-fill R:R at completed-close IOC reference.

Report the same statistics for:
- all structural continuation first breaks;
- the subset passing the existing trigger-bar-close context bundle.

Primary detachment summaries:
- median / p90 / max absolute detachment;
- median / p90 / max adverse detachment;
- share adverse >8 ticks;
- share whose completed-close fill changes R:R admission;
- share whose completed-close IOC cancels.
## Model A — current production evidence architecture

Use the current collector contract exactly:
- context is evaluated from the completed trigger 5m bar;
- TRENDING;
- STRONG;
- reconstructed/direct relative volume >=0.8;
- aligned trend direction;
- aligned EMA 9/21/55 stack;
- IOC market reference = trigger-bar close;
- 8-tick tolerance;
- 1 adverse tick;
- actual fill R:R >=2.0;
- planned risk <=$1,750;
- one open swing at a time;
- static stop / 2R target;
- multi-day hold;
- pessimistic same-bar handling;
- $1.48 round-turn commission.

This model is causal only at the completed 5m decision time. It is the baseline
execution representation, not a true-touch model.

## Model B — causally pre-armed architecture

A true first-break fill cannot use the trigger bar's final context values.
Therefore Model B must not select entries using trigger-bar-close context.
For the same current-identity structural first-break population:

- setup geometry is known from completed prior Daily sessions;
- evaluate the unchanged strict context bundle on the immediately preceding
  completed 5m bar;
- only if that prior bar passes context may a resting stop be armed for the next
  5m bar;
- if the first Daily boundary break occurs while not armed, the day's
  opportunity is consumed and no later entry is allowed;
- LONG stop-entry = prior high + 1 tick;
- SHORT stop-entry = prior low - 1 tick;
- ordinary touch fill = stop-entry plus 1 adverse tick;
- gap-through fill = trigger-bar open plus 1 adverse tick in trade direction;
- reject if slipped fill is outside its fixed stop/target bracket;
- reapply actual-fill R:R >=2.0 and planned risk <=$1,750;
- allow trigger bar itself to resolve the position;
- same-bar stop+target ambiguity = pessimistic stop-first;
- no EOD flatten.

Model B is an architecture counterfactual, not an authorization to change the
live collector.
## Slippage sensitivity

For Model B, score adverse entry slippage at:
- 1 tick;
- 2 ticks;
- 3 ticks.

Do not alter stops, targets, context thresholds, IOC tolerance, or risk gates.

## Required comparisons

Per model report:
- structural days;
- context-approved opportunities;
- fills / cancels / risk rejects;
- LONG / SHORT;
- wins / losses;
- net P&L;
- PF;
- chronological H1 / H2;
- yearly 2024 / 2025 / 2026;
- max drawdown;
- top-three-month P&L concentration;
- same-trigger-bar resolutions;
- same-trigger-bar stop+target ambiguities.

Also produce a candidate-level parity table showing every admission difference
between Model A and Model B and its exact cause.
## Interpretation rules

1. If Gate 0 fails, verdict = `BASELINE PROVENANCE MISMATCH / HOLD`.
2. Any Model B row selected with trigger-bar final context is invalid lookahead.
3. Any same-bar ambiguous outcome must resolve stop-first.
4. Do not infer that a stronger Model B proves the strategy; this audit tests
   implementability and timing only.
5. If Model B materially changes candidate admission, fills, or outcome sign,
   classify the existing completed-close evidence as timing-sensitive.
6. If Model B loses aggregate sign or either chronological half at required
   1-tick mechanics, classify the causal architecture as unsupported.
7. 2/3-tick results are robustness diagnostics, not tuning inputs.
8. Do not change the 8-tick production IOC tolerance after seeing results.
9. Do not add/remove context gates after seeing results.
10. Do not use LC_ZONE or any new target rule.

## Safety / operational ruling

Regardless of result:
- no live execution;
- no Tradovate order path;
- no paper-fill promotion;
- no Daily ledger reset;
- no VPS restart;
- no environment change;
- no collector rewrite.

Any implementation change requires a separate explicit authorization after the
audit result is reviewed.

No proof, no run.
