# Preregistration — MNQ 60M 3-2-2 First Live Trigger-Timing A/B — 2026-09-18

## Purpose

Test whether the accepted MNQ 60M 3-2-2 historical edge survives when the
documented **First Live** entry is modeled as a causal pre-armed trigger rather
than inferred from a completed 5-minute bar.

Offline audit only. No runtime, strategy, risk, broker, collector, TradingView,
permission, or deployment change is authorized by this study.

## Frozen rule

Source:
- `docs/strategy-rules/60M_322_FirstLive_Rules.md`
- `strategy/strat_322_first_live.py`

Rule:
- setup uses completed 7AM / 8AM / 9AM 60m bars;
- 8AM must be outside relative to 7AM;
- 9AM must be directional relative to 8AM;
- 10:00–11:00 ET only;
- entry is the **first live break** of the opposite 9AM boundary;
- **no candle close required**;
- stop = opposite 9AM boundary;
- target = outer 8AM boundary;
- fixed bracket, day-only exit at exact 15:55–16:00 5m bar;
- MNQ only.

No setup/filter/stop/target/risk rule may change in this audit.

## Frozen population

Use the accepted corrected canonical baseline only:
- MNQ;
- 2024-07-02 through 2026-06-26;
- expected candidate count: **34**;
- candidate identity must cross-check between the accepted research detector
  (`research.run_322_expanded_evidence.detect_candidates()` on
  `data/replay_polygon/MNQ`) and the canonical 5m state-machine extraction on
  `data/replay_corpus_v1_5m_4hr_audit/MNQ`;
- primary 5m path source for all timing/accounting models:
  `data/replay_corpus_v1_5m_4hr_audit/MNQ`, the exact corpus used by the
  2026-09 gate-attribution audit;
- corpus range: 2024-07-02 through 2026-06-26.

Abort if either extraction does not produce exactly 34 candidates or if
candidate date/direction/trigger/stop/target differs between the accepted
research detector and current canonical state machine.

Do not add later data, remove losing candidates, or condition on outcome.

## Common accounting

All models:
- 1 MNQ contract;
- MNQ tick = 0.25;
- point value = $2.00;
- round-trip commission = **$1.48** to match the current 2026-09 gate audit;
- adverse slippage sensitivity = **1, 2, 3 ticks** on entry and exit;
- fixed documented stop/target;
- stop wins every same-bar stop+target ambiguity;
- exact day-only 15:55 bar required; missing exact bar = unresolved;
- no current account risk gates in the timing A/B (those are a separate known
  blocker);
- chronological halves = first 17 candidates / last 17 candidates.

## Entry models

### A — Legacy plan-price/backfill provenance

Purpose: reproduce the historical bracket-style ceiling, not claim executable
fidelity.

- First 5m bar in [10:00, 11:00) whose range strictly crosses the trigger.
- Exact 10:00 open beyond trigger is a gap fill at that open.
- Otherwise base entry = documented trigger.
- Apply adverse entry slippage.
- Bracket evaluation begins on the **next** 5m bar; crossing-bar high/low is not
  used after the retroactive plan fill.

This intentionally captures the optimistic historical convention.

### B — Completed-5m close + IOC32

Purpose: reproduce the current honest-fill / forward-style timing approximation.

- Same first crossing bar.
- Exact 10:00 gap: base fill = open.
- Non-gap: order arrives at crossing-bar close.
- IOC tolerance = 32 ticks from the trigger.
- If the close is beyond tolerance, no fill.
- If filled, base fill = crossing-bar close, plus adverse slippage.
- Crossing-bar earlier high/low cannot stop/target a position created at its
  close; bracket evaluation begins on the next 5m bar.
- A fill with fixed stop/target on the wrong side fails closed.

### C — Causal pre-armed First Live touch (primary)

Purpose: approximate the documented live rule using available 5m OHLC without
inventing intrabar path.

At 10:00 ET the 9AM trigger, stop, and target are already known and treated as a
resting stop-entry bracket.

For the first 5m bar that reaches the trigger:
- if the bar opens through the trigger, base fill = that bar's open
  (gap-through protection applies on **any** crossing 5m bar, not only 10:00);
- otherwise base fill = trigger;
- apply adverse entry slippage;
- reject if the slipped fill no longer lies strictly between stop and target;
- the trigger bar itself is eligible for stop/target resolution because the
  position exists intrabar;
- with only OHLC path available, any same-trigger-bar stop hit is counted before
  target, including cases where the adverse extreme could theoretically have
  occurred before the trigger. This is deliberately pessimistic;
- then continue through later 5m bars normally.

This model is a conservative 5m-OHLC approximation. It still does not know the
exact tick/1m path inside the trigger bar.

## Required outputs

For each model and each 1/2/3-tick sensitivity:
- 34 candidates accounted for;
- fills / no-fills / unresolved;
- wins / losses;
- net P&L;
- expectancy per candidate and per resolved fill;
- profit factor;
- max drawdown;
- H1 / H2 net;
- LONG / SHORT net;
- no-fill reasons;
- same-trigger-bar resolutions;
- same-trigger-bar stop/target ambiguity count;
- completed-close detachment from trigger in ticks:
  median / p90 / max and share > 32 ticks;
- model-by-model per-candidate ledger.

## Reproduction gates

The audit is invalid unless:
1. population = 34;
2. completed-5m IOC at 1 tick reproduces the 2026-09 gate-attribution ceiling
   within rounding:
   - fills = 20;
   - net ≈ +$1,859.40;
   - H1 ≈ +$1,068.68;
   - H2 ≈ +$790.72;
3. legacy plan model at 1 tick reproduces the documented gate-attribution
   bracket within rounding:
   - net ≈ +$2,532.66;
   - H1 ≈ +$1,383.34;
   - H2 ≈ +$1,149.32.

If a reproduction gate fails, stop and investigate before interpreting model C.

## Timing-survival classification

This is **not** a strategy-validation rule.

Classify the corrected trigger mechanism as **TIMING EDGE SURVIVES / PROMISING
BUT UNPROVEN** only if pre-armed model C at 3 adverse ticks:
- total net P&L > 0;
- H1 net > 0;
- H2 net > 0.

Classify **TIMING RESULT UNSTABLE / WAIT** if total net > 0 but either half is
<= 0.

Classify **TIMING EDGE DOES NOT SURVIVE** if total net <= 0 at 3 ticks.

Regardless of result:
- sample remains only 34 candidates;
- current real-account stop/R:R constraints remain separate blockers;
- no live/demo/paper-fill authority is granted.

## Prohibited after outcome

Do not:
- alter the 32-tick IOC tolerance;
- change stop/target;
- add 50% breach;
- filter by direction, month, regime, Signa, zones, VWAP, or outcome;
- remove same-bar pessimism;
- choose a different slippage level as the headline because it looks better;
- add 1m historical reconstruction after reading results.

Any follow-up is a new preregistered study.

## ERRATUM 2026-09-24

The sealed reproduction-gate lines in "Reproduction gates" above are preserved
unchanged. They embedded a resolver defect. Day-only `resolve_bracket` in
`scripts/edge_decomposition_audit.py` walked same-ET-date bars after the exact
15:55 ET bar. On 2025-01-20 the cash session halted at 13:00 ET and Globex
reopened at 18:00 ET the same calendar day, so there is no 15:55 ET bar. The
walk booked a 19:50 ET target on all three fill models. The frozen contract
(execution/day_only_exit.py; "Common Day-Only Exit" in
`docs/strategy-rules/60M_322_FirstLive_Rules.md`) resolves stop and target only
up to and including that exact bar, then fails closed as UNRESOLVED /
EOD_BAR_MISSING when the bar is absent. No earlier bar is a substitute, and
evening bars are not walked.

Original sealed text, still in place above:

- completed-5m IOC at 1 tick: fills = 20; net ≈ +$1,859.40; H1 ≈ +$1,068.68;
  H2 ≈ +$790.72;
- legacy plan model at 1 tick: net ≈ +$2,532.66; H1 ≈ +$1,383.34;
  H2 ≈ +$1,149.32.

Corrected expected values, derived from exit timestamps in
`scripts/322_trigger_timing_ab_2026-09-18.json`, pending corpus re-run. Filled
counts are unchanged. 2025-01-20 is in the first chronological half, so H2 is
unchanged.

- legacy plan at 1 tick: filled 33; net 2293.64; H1 1144.32; H2 1149.32;
- completed-5m IOC at 1 tick: filled 20; net 1622.38; H1 831.66; H2 790.72.

`scripts/322_trigger_timing_ab_2026_09_18.py` `EXPECTED_PLAN` and
`EXPECTED_IOC` follow these corrected values. The original sealed numbers
remain visible in that file's comment. The Operator approves this erratum at
merge review. Corpus re-run: HOLD. A differing row other than the nine
2025-01-20 cells (3 models × 3 slippages) is a finding, not a silent update.
