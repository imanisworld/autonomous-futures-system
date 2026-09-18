# Preregistration — Observation-Only 1m MNQ 60M 3-2-2 First Live Observer — 2026-09-18

## Verdict boundary

Build only an **observation-only** lower-latency evidence lane for the existing
MNQ 60M 3-2-2 First Live rule.

This build does not authorize:
- live execution;
- Tradovate submission;
- PaperBroker fills;
- DecisionEngine or RiskEngine entry;
- risk-policy changes;
- 1m setup discovery;
- a new strategy population;
- enabling `strat_322_first_live` in the executable strategy configuration.

The lane may miss evidence. It may not create an unverified trade.

## Motivation

The preregistered historical timing A/B
(`docs/322-trigger-timing-ab-2026-09-18.md`) established that the documented
First Live break is materially earlier than a completed-5m decision clock.
The next proof is prospective observation of the real lower-latency trigger.

## Flags

Add a dedicated proof-critical flag:

`ONE_MIN_322_OBSERVER_ENABLED`

Contract:
- default OFF;
- observer is active only when both `ONE_MIN_TRIGGER_ENABLED=true` and
  `ONE_MIN_322_OBSERVER_ENABLED=true`;
- add the new flag to `PROOF_CRITICAL_RUNTIME_OVERRIDES`;
- do not change any deployed environment in this build.

## 5m arming path

The setup is established from the authoritative 5m feed, never from 1m.

Instrument:
- MNQ only.

Rule source:
- `strategy/strat_322_first_live.py`;
- `docs/strategy-rules/60M_322_FirstLive_Rules.md`.

At the close of the 09:55–10:00 ET 5m bar:
1. require all 36 expected 5m bars from 07:00 through 09:55 ET;
2. call the canonical `advance_strat_322_first_live()` formula at the 10:00
   boundary using only bars complete by that boundary;
3. persist the resulting observer state under the isolated `tf1m/` evidence
   root, not in executable `DailyState`;
4. if the canonical state is `ARMED`, record an observation-only ARM event;
5. if reference data is incomplete, fail closed and record
   `REFERENCE_DATA_INCOMPLETE`;
6. no candidate is submitted to DecisionEngine, RiskEngine, PaperBroker, or an
   external broker.

At the close of the 10:55–11:00 ET 5m bar, an untouched ARMED observer state may
be expired using the canonical 11:00 boundary.

No other 5m bar may create a setup.

## 1m trigger path

The 1m lane may observe a trigger only when the isolated observer state is:
- same trading date;
- MNQ;
- `status == ARMED`;
- direction LONG or SHORT;
- inside 10:00–11:00 ET.

Trigger semantics must match the documented rule:
- LONG: first 1m bar with `high > trigger`;
- SHORT: first 1m bar with `low < trigger`;
- equality alone is not a break;
- if the 1m bar opens beyond the trigger, mark `gap_through=true`.

The canonical bracket is evidence only:
- stop = opposite 9AM boundary already stored in the ARMED state;
- target = 8AM outer boundary already stored in the ARMED state;
- if a gap-through reference is already outside the fixed stop/target bracket,
  fail closed with `ENTRY_BRACKET_INVALID_AT_TOUCH`.

On a valid first break:
- emit exactly one `TRIGGER_TOUCH` evidence event per armed setup;
- persist observer status `TRIGGERED`;
- record 1m bar timestamp/OHLC, direction, trigger, stop, target, and gap-through;
- mark `mode=observation_only`;
- mark `trade_authorized=false`;
- mark `paper_fill_authorized=false`;
- mark `external_broker=false`.

Do not calculate realized P&L or open a simulated position.

## Isolation contract

The new module must not import:
- `strategy.signal_engine.DecisionEngine`;
- `risk.risk_engine.RiskEngine`;
- `execution.paper_broker`;
- Tradovate/broker adapters.

Observer state and evidence live under `tf1m/` only.

The existing 4HR 1m lane must remain behaviorally unchanged.

The executable `DailyState.strat_322_first_live_state` journal omission found
during this audit is not used as a shortcut. This build does not enable the
strategy or make shared trading state authoritative for the observer.

## Required tests

At minimum prove:
1. both flags are required;
2. MNQ-only;
3. 1m cannot arm a setup;
4. 09:55 completed 5m data can arm exactly at 10:00;
5. missing any required 07:00–09:55 5m bar fails closed;
6. non-3-2-2 day does not arm;
7. strict break semantics: equality is not a trigger;
8. LONG and SHORT first-live touches are observed;
9. gap-through is recorded;
10. invalid gap bracket fails closed;
11. duplicate 1m breaks emit no second evidence event;
12. touch outside 10:00–11:00 is ignored;
13. observer restart state survives from isolated state file;
14. 11:00 expiry works;
15. response remains `ONE_MIN_CONTEXT` / `FIVE_MIN_CONTEXT` with
    `fill=None`, `risk=None`, and no executable route;
16. existing 4HR one-minute tests remain green;
17. new flag is proof-critical;
18. static import test proves the observer module has no DecisionEngine,
    RiskEngine, PaperBroker, or broker adapter import.

## Promotion boundary

Passing unit/repo/CI tests authorizes merge of the observation-only code only.

It does **not** authorize VPS activation of
`ONE_MIN_322_OBSERVER_ENABLED=true`.

Activation requires a separate deployment decision after:
- exact merged SHA is known;
- live-box guard passes with the new pin;
- existing 1m/5m routes remain authenticated and healthy;
- runtime posture remains live-disabled / demo / always-on-shadow.

No proof, no run.
