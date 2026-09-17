# Post-Freeze Futures Runtime Release Gate — 2026-09-17

**Status:** PRE-DEPLOY CHECKLIST ONLY / PAPER ONLY / NO RESTART AUTHORIZED

Purpose: define the smallest safe futures runtime release after the current no-release/no-restart freeze. This document does not authorize deployment.

## Proven deployed baseline

Last verified deployed release: `8fd8b215063c83428fa15028ae76f0e7f6d25a8e`.

Current repository `main` at checklist creation: `4d75226f1ca40591ebd0756950dffed941c68ed6`.

The repository is ahead of the deployed baseline by research, docs, reporting, options-collector, replay, and futures-runtime changes. A future futures deployment must not treat every changed file as an activation requirement.

## Runtime allowlist for the futures defect release

The known runtime defect repair that must eventually reach the VPS is PR #612:

- `webhook/runner.py`
- `strategy/signal_engine.py`

Behavioral contract:

1. Shared-journal daily capacity and consecutive-loss lockout remain execution limits.
2. Once execution capacity is exhausted, setup evaluation continues only for observation/audit.
3. The runner records the execution block and observed decision/candidate.
4. The blocked branch returns before RiskEngine/broker/proof-paper execution.
5. `BLOCKED_OPEN_POSITION` remains authoritative and unchanged.
6. Daily-loss logic remains in the RiskEngine and is unchanged.
7. No limit values are widened.

`replay/replay_engine.py` changed for #621, but that repair is an offline replay-parity correction and is not required to activate the futures runtime defect repair on the VPS.

## C8 bundling rule

If C8 (`market_condition` journal normalization) is merged before the sanctioned release, it may be bundled only if its final diff is limited to journal/audit persistence plus regression tests and proves:

- no DecisionEngine behavior change;
- no strategy/gate/risk/broker change;
- existing non-null top-level `market_condition` is never overwritten;
- null top-level may be filled only from the same row's `context.market_condition`;
- both-null remains null.

Otherwise deploy #612 without C8 and schedule C8 separately.

## Pre-deploy hard gates

Before any sanctioned restart:

- confirm exact repo SHA intended for deployment;
- compare intended SHA against deployed `8fd8b21...` and review every changed futures-runtime file;
- confirm `LIVE_TRADING_ENABLED=false` from the running service environment and target env;
- confirm broker mode/routing remains paper-safe for the main futures path;
- confirm collection-only roots M2K/MGC/MCL/MBT still cannot enter DecisionEngine/RiskEngine/PaperBroker/Tradovate;
- confirm no open position whose lifecycle would be disrupted by restart, or follow the sanctioned position-preservation procedure;
- confirm release-history recording path is healthy;
- capture pre-restart service PID, cwd/release SHA, environment safety flags, open-position state, journal tail, and collection state.

Missing proof blocks the restart.

## Post-deploy proof

After restart, verify before calling the release healthy:

1. running `/proc/<pid>/cwd` resolves to the intended release;
2. `LIVE_TRADING_ENABLED=false` in the process environment;
3. service/status endpoints healthy;
4. no external broker orders were created by the restart;
5. one ordinary paper decision still journals normally;
6. on a capacity-blocked fixture/repro path, the journal records the observed candidate but no risk/broker/order stage occurs;
7. collection-only root routing remains observation-only;
8. Discord/monitoring output remains clear;
9. release history records the deployed SHA.

## Stop conditions

Stop and roll back/hold if any of the following is observed:

- target release differs from expected SHA;
- live-trading flag or broker route is ambiguous;
- capacity-blocked setup reaches RiskEngine or a broker;
- collection-only root enters the trading runner;
- journal/status behavior regresses;
- open-position state cannot be reconciled;
- restart changes campaign epoch/state without explicit authorization.

**No proof, no run.**