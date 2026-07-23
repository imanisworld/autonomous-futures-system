# Post-fill execution correction deployment handoff — 2026-07-15

## One-time exception

- Approved candidate SHA: `1011b6bf84735a894adeaefa27e0ae747281bd62`
- Required live/rollback SHA: `043fd686719a3f30cfb4166eb826663ded169bc7`
- Reviewed `git diff --name-status` digest (sorted, SHA-256): `f6ddd89f503efaaa90169d3fab271a2760187614156db18a588acf354510f592`
- Scope: a local, uncommitted branch in `scripts/atomic_release.sh`, requiring exact equality for all three values above.
- Expiration: the promotion command ran under an exit trap that restored `scripts/atomic_release.sh`. A post-command `git diff --exit-code -- scripts/atomic_release.sh` passed. The exception no longer exists and was never committed or deployed.

Exact reviewed file set:

```text
A docs/post_fill_execution_audit_2026-07-15.md
M execution/broker_interface.py
M execution/paper_broker.py
A execution/post_fill_validation.py
M execution/tradovate_broker.py
M journal/journal_logger.py
M main.py
M replay/replay_engine.py
A scripts/post_fill_execution_audit.py
A tests/test_post_fill_validation.py
M tests/test_tradovate_bracket_verify.py
M tests/test_tradovate_entry_fill.py
M webhook/app.py
M webhook/runner.py
```

## Deploy result

- Result: successful.
- Active release: `/root/afs-releases/1011b6bf84735a894adeaefa27e0ae747281bd62`
- Release integrity: OK, 567 files checked.
- Service became active at `2026-07-16 01:59:28 UTC`; `ActiveState=active`, `SubState=running`, `NRestarts=0`.
- Health returned `ok=true`, `paper_mode=false`, `live_trading_enabled=false`, and `broker=tradovate`.

## Safety verification

- Before restart: Tradovate `demo`, zero working orders, zero nonzero positions.
- After restart: Tradovate `demo`, zero working orders, zero nonzero positions. Therefore the restart created no broker order or position.
- Runtime posture preserved: `SCHEDULE_MODE=current`, matching expected pin; `EXIT_MODE=runner_shadow`, matching expected pin.
- Broker posture preserved: `BROKER=tradovate`, `TRADOVATE_ENV=demo`, `PAPER_MODE=false`.
- Real-money execution remains disabled: `LIVE_TRADING_ENABLED=false` and health reports `live_trading_enabled=false`.
- Strategy/config/risk protected-path diff is empty for `strategy/**`, `strategies/**`, `config/**`, `risk/**`, `risk_rules.yaml`, and `.env*`.
- Risk rules are unchanged: both current and rollback manifests use `8163cb04a9726f69da0e0f8c258a235022ae5db494930f3f8f138e921d27f711`.
- `/status/today.post_fill_execution.enabled_for_external_broker=true` after deployment.
- The deployed validator exercised both direction branches. An adverse LONG fill and an adverse SHORT fill each returned `accepted=false` with `actual_rr=1.5925925925925926` and failed `actual_rr_minimum`.
- Exact historical check: requested LONG entry `29603.50`, actual fill `29610.50`, stop `29583.50`, target `29653.50` is rejected under the deployed code (`accepted=false`, actual RR `1.5925925925925926`).
- The Tradovate runtime sets `post_fill_validation_required` for every external-broker order without a direction exception, fetches the exact broker fill, applies the shared validator, and routes a failed result to the controlled-flatten handler while protective OSO children remain verified.

## Rollback

- Rollback SHA: `043fd686719a3f30cfb4166eb826663ded169bc7`
- Rollback path: `/root/afs-releases/043fd686719a-20260715-211946`
- `/root/afs-shared/current.previous` points to that path, and the directory exists.

No strategy, configuration, risk rule, broker mode, session mode, or exit mode was changed by this exception.
