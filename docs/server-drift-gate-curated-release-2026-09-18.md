# Server drift gate — curated-release semantics

Date: 2026-09-18

## Problem

The VPS cron gate at `/root/bin/afs-drift-gate.sh` cloned repository `main` and compared every filtered runtime file directly against the live curated release.

That comparison conflated two different conditions:

1. **actual release drift** — a deployed file was edited/missing or the release identity changed unexpectedly;
2. **intentional release lag** — `main` contains merged work that was not selected for the curated VPS release.

On 2026-09-18 the old gate raised 19 `UNEXPECTED drift` items while the live release itself passed `ops.release_integrity` across 1,070 files. The alert included options-manager, replay, research, and qualification files that were intentionally absent from the futures release.

## Ruling

The red alarm source of truth is the immutable release itself, not repository `main`.

The server gate must fail/red-alert only when:
- `ops.release_integrity` fails against the live `release_manifest.json`;
- `EXPECTED_LIVE_COMMIT` is missing or does not equal the manifest commit;
- a configured `EXPECTED_RELEASE_FINGERPRINT` does not equal the manifest fingerprint;
- the live release tree or manifest is unavailable.

Repository `main` is still compared with the live tree, but only as an **informational merged-but-unshipped report**. Main-ahead differences do not change the gate exit code and do not trigger the red Discord alert.

## No seeding

The server curated-release gate intentionally refuses `--seed` with exit 64.

A manifest/pin mismatch must be fixed by reconciling or redeploying the release. It must never be hidden by accepting the current drift set.

The older operator-side `scripts/afs-drift-gate.sh` remains available for explicit ref-vs-box comparison. The VPS cron source is now tracked separately as:

`scripts/afs-server-drift-gate.sh`

## Safety properties

The server gate is read-only. It contains no:
- `systemctl restart`;
- deployment/promotion command;
- `git pull`;
- release mutation;
- drift allowlist or seed write.

Focused regression set:
- `tests/test_afs_server_drift_gate.py`;
- `tests/test_afs_drift_gate.py`;
- `tests/test_release_integrity.py`.

Result before deployment: **32 passed**.
