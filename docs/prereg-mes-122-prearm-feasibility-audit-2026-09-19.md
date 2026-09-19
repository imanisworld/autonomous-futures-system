# Preregistration — MES 1-2-2 Pre-Arm Feasibility Audit — 2026-09-19

## Scope

**AUDIT ONLY / NO EXECUTION BUILD / NO DEPLOYMENT.**

Base commit:
`bfb2340f46f99a8c6e6582635254ecfba4fb1448`

Purpose: determine whether the existing futures MES 15m 1-2-2 strategy can be
made operationally execution-parity faithful without inventing new strategy
logic.

This audit does not authorize broker orders, PaperBroker promotion, risk-rule
changes, live trading, or a new 1m lane.
## Questions to answer

1. When does the canonical 1-2-2 setup become legally knowable?
2. Before the watched 15m bar begins, are direction, trigger, stop, target,
   expiry, and setup identity already fixed?
3. Does the current runner arm any real order-equivalent state before that bar,
   or only reconstruct a hypothetical fill after bar close?
4. Could a lower-timeframe observation-only lane consume the already-frozen
   setup without discovering or modifying strategy state?
5. Are gap-through, equality, same-bar stop/target ambiguity, and invalid
   post-gap brackets already defined by the canonical state machine?
6. Does restart persistence preserve the exact armed setup and one-shot state?
7. Are live/replay/paper formulas identical for the same frozen setup and
   market path?
8. Are timestamp/session boundaries explicit and fail-closed?
## Frozen strategy identity

Do not change:
- 15m 1-2-2 pattern definition;
- precursor/setup classification;
- direction;
- trigger boundary;
- fixed stop;
- fixed target;
- next-bar-only validity;
- gap semantics;
- pessimistic stop-first rule;
- session filters;
- max contracts / max trades / daily-loss rules;
- existing PaperBroker accounting;
- any historical candidate population.

No P&L tuning or threshold changes are allowed in this audit.
## Feasibility classification

**FEASIBLE WITHOUT STRATEGY CHANGE**
only if all of the following are proven:
- setup is fully frozen before the watched bar opens;
- a lower-timeframe consumer can only observe the frozen setup;
- trigger/stop/target/expiry are identical to the canonical state machine;
- no lower-timeframe bar can create, alter, or extend a setup;
- gap-through and invalid-bracket behavior are deterministic;
- dedupe/restart can be made exact from existing persisted state;
- no broker submission is required to prove timing parity.

**NOT FEASIBLE AS SAME STRATEGY**
if any required entry information is only knowable after the watched 15m bar,
or if reproducing the historical entry requires adding a new discretionary
condition, target, stop, session exception, or strategy state.
## Evidence standard

For the MES 1-2-2 path record:
- rule source;
- canonical code source;
- precursor bar close time;
- setup-arm time;
- watched bar open/close;
- earliest legal trigger time;
- current evaluation time;
- exact trigger inequality;
- gap fill reference;
- stop/target reference;
- expiry;
- persisted fields;
- one-shot/dedupe behavior;
- live/replay/PaperBroker path;
- fail-closed conditions.

Final classification:
- FEASIBLE WITHOUT STRATEGY CHANGE
- FEASIBLE BUT PERSISTENCE/PARITY FIX REQUIRED
- NOT FEASIBLE AS SAME STRATEGY
- HOLD — MISSING PROOF

No build follows automatically.

No proof, no run.
