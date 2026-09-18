# Wide-stop gap-aware release — 2026-09-18

Status: **DEPLOYED / GAP-AWARE MODEL ACTIVE / LIVE EXECUTION DISABLED**

## Scope

Operator-authorized one-time freeze waiver to deploy:
- #663 — MNQ/MES BarHistory source-ticker provenance;
- #670 — wide-stop gap-through-stop pricing input.

No strategy, risk-rule, broker-permission, live-trading, or strategy-parameter change was authorized.

## Release construction

Previously deployed baseline:
`94eb7d388c02b744eed5a3d3d36b14fa724f1781`

Independently built/verified candidate:
`117d9e80064b0175f9feb56b65623c72feb87b56`

Candidate branch:
`release/minimal-94eb7d3-gap670-prov663`

Runtime delta versus baseline:
- `webhook/runner.py` — #663 provenance;
- `context/wide_stop_forward_collector.py` — #670 gap-stop realism.

Focused tests on the exact candidate:
- `tests/test_wide_stop_forward_collector.py`
- `tests/test_paper_broker.py`
- `tests/test_runner_source_ticker_provenance.py`
- **46 passed**

The candidate and deployed release both use the same `scripts/atomic_release.sh` SHA-256:
`11a6a33d176dfbad76bf0d078d9c679e5cd034d6093fc1f8593eb62bbc46cac1`

Build and isolated candidate verify:
- release integrity PASS locally;
- release integrity PASS on VPS;
- **1,070 files checked**;
- candidate broker forced to paper;
- `LIVE_TRADING_ENABLED=false`;
- health PASS.

## Pre-deploy position proof

Immediately before mutation:
- `wide_stop_4k.position = null`, filled_count 0;
- `wide_stop_6k.position = null`, filled_count 0;
- Tradovate DEMO state file absent — no persisted demo pending/position;
- main journal `open_position = null`;
- Daily 2-2 paper lane flat;
- MES 1-2-2 paper lane flat;
- `LIVE_TRADING_ENABLED=false`;
- `TRADOVATE_ENV=demo`;
- `SCHEDULE_MODE=always_on_shadow`;
- `EXIT_MODE=static`.

Therefore there was no open futures position to cross during the restart.

## Initial epoch-reset attempt

The deployment plan initially followed #568/#670's stated evidence-model requirement by setting a fresh shared epoch:

`2026-09-18T11:41:33Z`

Both were updated together:
- `WIDE_STOP_LEDGER_EPOCH_START`;
- `EXPECTED_PROOF_WIDE_STOP_LEDGER_EPOCH_START`.

Pre-edit .env backup:
`/root/afs-shared/backups/.env.pre-gap670-20260918T114133Z`

Pre-edit .env SHA-256:
`7b793a7918863aea5521e8498c5e812bdd12ad5702b86a9e631071f223198b56`

Post-edit SHA-256 before release-pin rewrite:
`ebdbd5115a1085fdfe08e7bb483260abf0a48bc27408de1afc26c24a978ccf28`

## First promotion

`117d9e80064b0175f9feb56b65623c72feb87b56` was promoted and futures-bot restarted at **2026-09-18 11:42:45 UTC**.

Running-code proof:
- #670: `NextBarOHLC(open=..., high=..., low=...)`;
- #670: incoming 5m payload open persisted as `"open": float(payload.open)`;
- #663: `source_ticker=payload.ticker`.

Release integrity PASS: 1,070 files.

## Shared-epoch coupling discovered

The first natural 5m cycle after the restart failed closed:

`context.daily_22_state_integrity.DailySwingStateIntegrityError: daily_swing_state_epoch_mismatch`

Root cause: Daily 2-2 intentionally derives its persisted-state epoch from the same `WIDE_STOP_LEDGER_EPOCH_START` used by the wide-stop family.

Daily 2-2 had real forward evidence that must not be discarded:
- persisted epoch `2026-09-09T04:21:04+00:00`;
- balance **$4,406.02**;
- peak $5,000;
- max drawdown **11.8796%**;
- one resolved loss **−$593.98**;
- position null;
- additional candidate/rejection history through 2026-09-18.

Resetting Daily merely to manufacture a fresh wide-stop epoch would destroy valid ongoing evidence. That option was rejected.

## Epoch correction

Safe correction:
- preserve Daily's existing state/evidence;
- restore the shared epoch and proof pin to `2026-09-09T04:21:04Z`;
- keep #670 code deployed;
- record the #670 fill-model boundary separately from the shared env epoch.

Pre-correction .env backup:
`/root/afs-shared/backups/.env.pre-epoch-coupling-correction-20260918T114827Z`

The shared epoch/proof pin were restored to:
`2026-09-09T04:21:04Z`

The futures bot and AFS watcher were restarted.

## Concurrent release-owner incident

During the correction window, another deployment owner replaced the first promoted release with:

`c538e2bc429d52c7d960c1d937d5e27ff4361896`

Final active release path:
`/root/afs-releases/c538e2bc429d-20260918-074655`

Final release lineage:
`94eb7d3 → #663 → #670 → c538e2b`

The concurrent release was audited before trust:
- owner process was no longer active by the time the state was reconciled;
- current symlink and release identity were explicitly re-read;
- runtime/test blobs for `webhook/runner.py`, `context/wide_stop_forward_collector.py`, and both focused test files are **byte-identical** between `117d9e8` and `c538e2b`;
- `git diff 117d9e8..c538e2b` changes only `docs/cross-instrument-evidence-quality-v1.md`.

Therefore the concurrent deployment is a **process/ownership breach**, not a runtime-code change.

Final futures-bot active time after correction:
**2026-09-18 11:48:28 UTC**

Immediate previous release:
`/root/afs-releases/117d9e80064b0175f9feb56b65623c72feb87b56`

Pre-#670 rollback release:
`/root/afs-releases/94eb7d388c02-20260917-173551`

## Final runtime proof

Final posture:
- `LIVE_TRADING_ENABLED=false`;
- `TRADOVATE_ENV=demo`;
- `SCHEDULE_MODE=always_on_shadow`;
- `EXIT_MODE=static`;
- `WIDE_STOP_LEDGER_MODE=paper_sim`;
- `WIDE_STOP_LEDGER_EXECUTION_ROUTE=tradovate_demo`;
- `WIDE_STOP_DEMO_EXECUTION_ENABLED=true`;
- shared epoch/proof pin `2026-09-09T04:21:04Z`.

Release integrity:
**PASS — 1,070 files**

AFS watcher:
active after restart/rebaseline.

Tradovate reliability:
HEALTHY.

All paper lanes:
flat after restart.

Tradovate DEMO state:
absent — no persisted position/pending submission.

No post-correction epoch-mismatch or wide-stop collection errors were observed.

## Natural feed proof

The next natural 5m bar after the correction was recorded successfully:

`bar_ts=2026-09-18T11:45:00Z`

No `daily_swing_state_epoch_mismatch`, wide-stop collection failure, traceback, or exception followed that bar.

This is the first successfully processed **gap-aware** 5m bar and is the explicit wide-stop fill-model boundary.

#663 was also naturally observed on the first post-release MNQ/MES 15m rows:
- MNQ stored `source_ticker: "MNQ1!"`;
- MES stored `source_ticker: "MES1!"`.

Root file/instrument identity remained MNQ/MES.

## Evidence boundary

Because the shared epoch cannot be reset independently without invalidating Daily's persisted campaign, the env epoch is **not** the #670 model boundary.

For wide-stop analysis use:

**gap-aware fill-model boundary: `2026-09-18T11:45:00Z`**

There were **zero wide-stop fills/outcomes before #670**, so no realized economic outcomes are mixed across fill models. Pre-boundary candidate/seen rows remain provenance only.

Do not:
- pool pre/post model rows without preserving this boundary;
- reset Daily 2-2 to create a cosmetic wide-stop epoch;
- use `WIDE_STOP_LEDGER_EPOCH_START` as a proxy for #670 model version.

Before any future wide-stop-only epoch reset, the shared-epoch coupling must be explicitly addressed.

## Final verdict

**APPROVED / DEPLOYED / GAP-AWARE WIDE-STOP MODEL ACTIVE**

No additional restart is required for #663/#670.

Remaining proof gap: a naturally occurring forward `STOP_GAP` outcome has not yet happened under the new model. The code path, tests, candidate verification, deployed runtime, and post-correction feed continuity are proven; the real gap event itself remains prospective evidence.
