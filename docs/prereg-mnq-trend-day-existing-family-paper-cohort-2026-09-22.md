# MNQ Trend-Day Existing-Family Paper Cohort — Preregistered Evidence Contract

**Date:** 2026-09-22  
**Status:** PAPER ONLY / DEFAULT OFF / NO PROMOTION PATH  
**Motivation:** the 2026-09-21 sustained MNQ advance was visible in shadow observations while the normal executable 15m set remained empty. The move is an audit prompt, not an optimization sample.

## Question

Can any EXISTING MNQ 15m LONG shadow family produce realistic, repeatable entries on sustained trend days under the system's actual entry/fill discipline?

This cohort does **not** create a new strategy and does not alter any existing detector.

## Frozen families

Four independent hypothetical sub-lanes:

1. `ema_pullback_trend`
2. `impulse_first_pullback_observed`
3. `strat_22_continuation_observed`
4. `trend_consolidation_break_observed`

The lanes are alternatives. Their P&L must never be summed into one portfolio result.

Existing 4HR Re-Trigger and 60M 3-2-2 First Live evidence lanes remain separate and are not duplicated here.

## Frozen population and mechanics

- MNQ only.
- Authoritative 15-minute decision bars only.
- LONG candidates only.
- Candidate source = existing `shadow_candidates` already produced by `strategy.shadow_setups.evaluate_shadow_setups`.
- Existing entry, stop, and target are copied unchanged.
- No new trend threshold, regime override, cross-instrument filter, Signa gate, target geometry, stop width, or ranking rule.
- No use of the failed fast-5m-regime rule.
- One open position at a time **per independent family lane**.
- Maximum 3 filled trades per observation day **per independent family lane**.
- No averaging down.
- Identical geometry is deduped within the observation day.

## Fill and resolution contract

- `PaperBroker` only.
- `ioc_limit` at the decision-bar close.
- MNQ tolerance = 32 ticks.
- 1 adverse tick entry slippage.
- Original static bracket.
- Pessimistic stop-first handling when bar order is unknowable.
- No breakeven trail.
- No runner.
- No target/stop rewrite.
- Positions resolve only on strictly later bars.
- Open positions expire at CME observation-day roll.

These mechanics deliberately mirror the accepted canonical paper helpers already used by the Asia D+EMA and session_22c evidence lanes.

## Activation safety

Repository default:

```
MNQ_TREND_DAY_PAPER_MODE=off
MNQ_TREND_DAY_PAPER_EPOCH_START=
```

Activation requires both:

```
MNQ_TREND_DAY_PAPER_MODE=paper_sim
MNQ_TREND_DAY_PAPER_EPOCH_START=<offset-aware ISO timestamp>
```

Both variables are proof-critical and require matching `EXPECTED_PROOF_*` pins before any runtime activation.

No Tradovate/demo/live token exists.

## What this can prove

For each family independently, prospective evidence can answer:

- candidate count;
- fills / no-fills / busy skips / daily-cap skips;
- WIN / LOSS / EXPIRED;
- PF and expectancy after the lane's modeled entry realism;
- H1/H2 stability;
- day concentration and leave-best-day-out;
- maximum drawdown;
- whether the family actually participates on future sustained MNQ trend days.

## What this cannot prove

- that the 2026-09-21 move would have been captured without prospective replay of that exact day;
- that a family is valid because it had one strong day;
- that four family lanes can be combined;
- that a shadow result authorizes demo/live execution;
- that a regime or context filter should be added post hoc.

## Decision rule

Until a family has a sufficiently large prospective sample with positive expectancy, realistic fills, temporal stability, controlled drawdown, and no single-day concentration problem, classification remains **PROMISING BUT UNPROVEN** at best and execution remains closed.

Broad families already classified BROKEN remain BROKEN unless this separately registered prospective population supplies enough contradictory evidence to justify a new audit. This lane does not reopen them by itself.

## Do not touch

- `risk_rules.yaml`;
- executable strategy permissions;
- broker routing;
- live/demo arming;
- stop/target definitions;
- fast-regime logic;
- H6/H7;
- Asia D+EMA;
- 4HR/3-2-2 definitions;
- instrument universe.

## Implementation

- `context/mnq_trend_day_paper_cohort.py`
- `webhook/runner.py` additive hook after existing shadow candidates are computed
- `config/settings.py` default-OFF fail-closed mode + epoch
- `ops/live_box_guard.py` proof-critical pins
- `tests/test_mnq_trend_day_paper_cohort.py`

No deployment or activation is part of this change.
