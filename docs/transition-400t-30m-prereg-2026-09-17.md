# Transition failed-breakdown reclaim — 400-tick / 30m preregistration

Status: **RESEARCH ONLY / PROMISING BUT UNPROVEN / NO RUNTIME BEHAVIOR**

Date: 2026-09-17

## Purpose

Freeze the only Transition repair cell that survived the exact preserved-corpus rerun before any executable implementation exists.

This document does **not** authorize:
- runtime strategy enablement;
- risk-rule changes;
- broker routing;
- paper/demo activation;
- changes to the global MNQ 120-tick stop cap;
- changes to the real-book $150 daily-loss floor.

The current Transition detector remains shadow/research-only.

## Frozen variant

Instrument: MNQ only.

Signal:
- existing `transition_failed_breakdown_reclaim` shadow detector;
- New York session only;
- candidate identity must match the preserved research population.

Entry:
- decision-close IOC;
- planned entry remains the IOC limit anchor;
- 32-tick MNQ IOC tolerance;
- 1 adverse entry tick;
- real `PaperBroker` bracket-validity behavior;
- 1 contract.

Exit:
- protective stop anchored **400 ticks from the planned entry**;
- otherwise exit after **six available 5-minute bars** after the decision;
- non-trading gaps do not count toward the six bars;
- therefore the timed exit may cross the CME daily maintenance break or a weekend;
- 1 adverse exit tick;
- $1.48 round-turn commission;
- stop resolves before timed exit when touched inside the holding path.

Portfolio constraints:
- max 1 open Transition position;
- max 3 filled Transition trades per session day.

No target is used by the economic variant. Any dummy target used solely to satisfy a broker-interface contract must be non-economic and must not affect outcome resolution.

## Frozen historical result

Exact rerun artifact:
`scripts/transition_repair_ioc_audit_results.json`

Research head:
`63b3ad8aa8382c785bebde1d05ba86922e4532f4`

Result artifact SHA-256:
`8cd7f1061e8e98d5046eaf51aa9bb7de5c5958576e9d9fc553d54501cb8efb3b`

### Full preserved MNQ population
- eligible candidates: 976
- sequential attempts/fills: 846 / 846
- wins/losses: 430 / 416
- net: +$3,166.92
- PF: 1.095099
- H1: +$2,895.96
- H2: +$270.96
- max drawdown: $2,465.22
- stop losses: 73
- worst trade: -$202.48

### Re-anchored audit population
- eligible candidates: 82
- sequential attempts/fills: 74 / 74
- wins/losses: 36 / 38
- net: +$697.48
- PF: 1.18402
- H1: +$402.74
- H2: +$294.74
- max drawdown: $825.36
- stop losses: 11
- worst trade: -$202.98

Both populations pass the preregistered basic-stability rule used by PR #528:
net > 0, PF > 1, H1 > 0, H2 > 0.

## Rejected policy-compatible repair

A 300-tick / $150 stop cell was rerun against the same corrected raw-bar horizon semantics.

It is rejected.

Full population:
- net +$2,011.02
- PF 1.060267
- H1 +$2,963.00
- **H2 -$951.98**

Audit population:
- **net -$611.02**
- **PF 0.853916**
- H1 +$112.24
- **H2 -$723.26**

Therefore the evidence does not support tightening the repair to the current real-book $150 risk envelope.

Do not search intermediate stop values (320/340/360/380) from these outcomes. That would be post-outcome boundary optimization.

## Risk boundary

MNQ tick value: $0.50.

400 ticks = $200 planned stop risk per contract before commission/slippage.

The current real-book controls remain:
- global MNQ max stop: 120 ticks;
- base daily loss floor: $150 for 1 contract.

The current RiskEngine daily-loss rule is a **post-realized-loss lockout**, not a pre-trade $150 dollar-risk cap. A 400-tick Transition loss can therefore exceed the real-book daily floor before the lockout acts.

This variant is **not compatible with the current real-book risk policy**.

A research/demo ledger capable of this variant would need, at minimum:
- isolated balance and daily state;
- no aggregation with real-book P&L;
- stop allowance >= 400 ticks for this strategy only;
- a predeclared daily floor defining how a normal max-stop loss is treated;
- one contract fixed;
- its own drawdown stop;
- explicit no-promotion status until canonical validation passes.

The existing `wide_stop_4k` ledger is **not reusable as-is**:
- its current cap is 300 ticks / $150;
- its current membership is 4HR only;
- its contract requires each member's documented static bracket;
- Transition's tested repair uses a six-trading-bar time exit instead.

Any Transition ledger would therefore be a distinct evidence contract, not a silent extension of `wide_stop_4k`.

## Canonicalization blocker

`transition_failed_breakdown_reclaim` currently exists as a shadow detector in `strategy/shadow_setups.py`.

It is not an executable canonical strategy wired through the full production chain.

Before Backtest -> DEMO qualification can even be attempted, a future implementation would have to prove:
- exact candidate parity with the frozen shadow detector;
- exact decision-close IOC intent;
- exact 400-tick planned-entry-anchored stop;
- exact six-available-5m-bar timed exit semantics;
- no lookahead or partial-bar dependency;
- `ReplayEngine -> DecisionEngine -> RiskEngine -> PaperBroker` path;
- runtime/replay parity;
- no unrelated strategy/risk/session/broker changes.

That future implementation is **not authorized by this document**.

## Required validation before DEMO eligibility

If an executable version is ever authorized, the validation package must satisfy the merged Backtest -> DEMO qualification gate, including:
- frozen dataset manifest and SHA-256;
- exact contract/roll identity;
- session-day identity proof;
- feed-integrity proof;
- IOC/no-fill modeling;
- pessimistic same-bar handling;
- gap handling;
- baseline 1-tick adverse slippage;

- 2-tick and 3-tick adverse slippage stress;
- untouched validation window;
- multiple months;
- chronological walk-forward;
- >=30 resolved fills in every preregistered required validation cell;
- drawdown and concentration checks;
- frozen runtime/replay parity fixtures;
- exact code SHA and `risk_rules.yaml` hash.

Because this variant's timed exit explicitly crosses maintenance/weekend gaps, **session-day / calendar identity is load-bearing**. The unresolved C14 product/session-calendar issue must not be hand-waved for this study.

## Current verdict

**PROMISING BUT UNPROVEN / RESEARCH ONLY / HOLD FOR CANONICALIZATION**

The evidence supports preserving this variant for a controlled future implementation.

It does not support:
- changing the main MNQ risk rules;
- putting Transition into the existing wide-stop ledger;
- DEMO activation;
- live activation;
- further stop tuning around 400 ticks.
