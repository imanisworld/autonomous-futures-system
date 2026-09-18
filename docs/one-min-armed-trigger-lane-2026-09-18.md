# 1m Armed Trigger Evidence Lane — 2026-09-18

## Verdict

**APPROVE CODE / HOLD DEPLOYMENT / PAPER EVIDENCE ONLY.**

Purpose: consume 1m TradingView bars only as lower-latency evidence for an
already-authorized MNQ 4HR Re-Trigger setup.

No live execution, broker route, risk override, or new setup discovery was added.

## Build

New module:
- `context/one_min_trigger.py`

New tests:
- `tests/test_one_min_trigger.py`

Core routing change:
- `webhook/runner.py`
- default-off via `ONE_MIN_TRIGGER_ENABLED`
- enabled 1m alerts return `ONE_MIN_CONTEXT` before DecisionEngine, RiskEngine,
  or any broker path.
## Safety contract

The 1m lane:
- stores bars under isolated `tf1m/`;
- accepts all roots as context only;
- emits a trigger-touch event only for MNQ;
- requires `strat_4hr_retrigger` to be enabled;
- requires a persisted MNQ 4HR state with `status == ARMED`;
- cannot create or arm a 4HR setup;
- uses only a fully completed prior 1H candle for the stop;
- deduplicates one trigger event per armed setup;
- records `trade_authorized=false`;
- records `external_broker=false`;
- returns before all order-submission code.

Disabled behavior is unchanged: 1m still returns TIMEFRAME_MISMATCH.
## Verification

Focused:
- `tests/test_one_min_trigger.py`
- `tests/test_five_min_feed.py`
- 24/24 passed.

Surrounding webhook/timeframe isolation:
- `tests/test_webhook.py`
- `tests/test_claim_bar_timeframe_isolation.py`
- 138/138 passed.

Authoritative repo suite:
- `pytest -q tests`
- **6093 passed / 7 skipped** on current `origin/main`.

A root-level `pytest -q` command is not authoritative because pytest also
discovers `private/mes598-proof/source/tests` and raises an import-path
collision against the repo's own `tests.conftest`.
## Deployment status

**Not deployed or enabled.**

The local Mac has no running futures/webhook systemd unit. Repository deployment
metadata points to the Hetzner webhook service and environment file:
`/etc/risksentinel/webhook.env`.

Required deployment setting:
`ONE_MIN_TRIGGER_ENABLED=true`

That setting must only be applied with the tested code release. Existing
5m/15m alerts must remain in place.

## Safe next step

Release only:
- `context/one_min_trigger.py`
- `webhook/runner.py`
- `tests/test_one_min_trigger.py`

Then enable `ONE_MIN_TRIGGER_ENABLED=true` on the paper/shadow webhook box,
restart/reload through the normal atomic release path, and verify incoming 1m
alerts produce `ONE_MIN_CONTEXT` plus isolated `tf1m/` records before
considering any paper-fill authority.
