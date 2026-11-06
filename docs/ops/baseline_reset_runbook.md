# Futures baseline reset runbook

Status: procedure only. This file does not authorize a reset, a backup, a promote, a restart, or a demo order. The Operator runs it, as root, over the existing `ssh hetzner` alias. Do not paste `.env`, account ids, or broker bodies into chat.

The reset records one exact release SHA and sets only the six Path A posture pins. After that file exists, `scripts/atomic_release.sh` accepts that SHA and refuses every other SHA. It does not edit `BROKER`, risk limits, `LIVE_TRADING_ENABLED`, or `TRADOVATE_ENV`.

`TARGET_SHA` below is the reviewed behavioral release. `PROCEDURE_SHA` is the merged commit that contains this runbook and `ops/baseline_reset.py`. Promote with the procedure checkout. The copy of `atomic_release.sh` inside `TARGET_SHA` does not know about the authorization file.

```text
PROCEDURE_SHA=<40-hex commit that contains this runbook>
TARGET_SHA=f8b044be38df8f14300bdf47465fc3ff0c2c0a62
TICKET=AFS-0508
PROC=/root/afs-maint/baseline-reset
PY=/root/autonomous-futures-system/.venv/bin/python
```

Stop on any output other than the expected block. Do not invent a flatten, a second order, or `--force-lock`.

## 1. Install the procedure without touching the live release

On the workstation, from a clean clone:

```bash
git fetch origin
git rev-parse "$PROCEDURE_SHA^{commit}"
git merge-base --is-ancestor "$PROCEDURE_SHA" origin/main
git archive "$PROCEDURE_SHA" | ssh hetzner "install -d -m 700 $PROC && tar -C $PROC -xf -"
ssh hetzner "test -x $PROC/scripts/ops/baseline_reset.sh && echo PROCEDURE_TREE_OK"
```

Expected: `PROCEDURE_TREE_OK`. Archive the whole procedure commit into `$PROC`. A partial copy cannot import `execution`, which the reset module uses for the recovery proof. The live symlink and `/root/afs-releases` are not modified. `$PY` is used only as an interpreter. `PYTHONDONTWRITEBYTECODE=1` is set by the wrapper, so it does not write bytecode into the release tree.

## 2. Preflight: is Path A already configured?

```bash
ssh hetzner "AFS_PYTHON=$PY $PROC/scripts/ops/baseline_reset.sh preflight --shared-dir /root/afs-shared"
echo "preflight_rc=$?"
```

Path A already configured (do not reset):

```text
path_a_configured=yes
reset_needed=no
authorized_sha=none
SCHEDULE_MODE=always_on_shadow
EXPECTED_PROOF_SCHEDULE_MODE=always_on_shadow
HTF_DIRECTION_MODE=off
EXPECTED_PROOF_HTF_DIRECTION_MODE=off
EXIT_MODE=static
EXPECTED_PROOF_EXIT_MODE=static
preflight_rc=0
```

`authorized_sha` may be a 40-hex SHA instead of `none`. If it is already `TARGET_SHA`, skip reset and continue at the backup. If it is a different SHA, stop: promote of `TARGET_SHA` will be refused.

Path A not configured (reset required before this behavioral promote):

```text
path_a_configured=no
reset_needed=yes
authorized_sha=none
preflight_rc=10
```

The six pin lines are printed either way. Unreadable `.env` exits 1 with `baseline_reset refused: env unreadable`.

## 3. Exposure census

The census reads the broker through the existing Tradovate client and writes counts only. `systemd-run` loads `EnvironmentFile` without printing it.

```bash
ssh hetzner "systemd-run --pipe --wait --collect \
  --property=EnvironmentFile=/root/afs-shared/.env \
  --setenv=PYTHONPATH=$PROC \
  --setenv=PYTHONDONTWRITEBYTECODE=1 \
  $PY -B -m ops.baseline_reset exposure-census \
  --shared-dir /root/afs-shared \
  --out /root/afs-maint/exposure.json"
```

Expected when flat:

```text
has_open_position=false
open_order_count=0
exposure_census=ok
```

Any other combination, or `baseline_reset refused:`, is a stop. Do not reset, back up, or place the recovery order while a position, a working order, or an unreadable census is present.

Confirm the deploy lock is absent:

```bash
ssh hetzner 'if [ -e /root/afs-shared/deploy.lock ]; then echo LOCK_PRESENT; exit 1; else echo LOCK_ABSENT_OK; fi'
```

Expected: `LOCK_ABSENT_OK`.

## 4. Pre-deploy backup

The backup copies the order-submission store (`records/` and `LOCK`), the journal files, the Track B heartbeat files, the allowlisted release and posture pins, and `baseline_authorization.json` if it exists. It does not copy `.env` and it does not delete the originals. Trend-shadow history is not part of the backup and is not modified.

Dry-run:

```bash
ssh hetzner "AFS_PYTHON=$PY $PROC/scripts/ops/baseline_reset.sh backup \
  --shared-dir /root/afs-shared \
  --status-file /root/afs-maint/exposure.json \
  --ticket $TICKET \
  --operator-token \"AUTHORIZE PREDEPLOY BACKUP $TICKET\" \
  --dry-run"
```

Expected:

```text
dry_run=backup
ticket_id=AFS-0508
writes=none
order_submissions=present
journals=<count>
baseline=absent
```

`baseline=present` is expected when an authorization file already exists. `writes=none` means the dry-run created nothing.

Backup:

```bash
ssh hetzner "AFS_PYTHON=$PY $PROC/scripts/ops/baseline_reset.sh backup \
  --shared-dir /root/afs-shared \
  --status-file /root/afs-maint/exposure.json \
  --ticket $TICKET \
  --operator-token \"AUTHORIZE PREDEPLOY BACKUP $TICKET\""
```

Expected:

```text
backup=ok
ticket_id=AFS-0508
journals=<count>
baseline=absent
path=/root/afs-shared/backups/predeploy-<UTC>-AFS-0508
recorded_at=<UTC>
operator_user=<unix user>
```

Confirm the manifest and that the source journal was not removed:

```bash
ssh hetzner 'python3 - <<"PY"
import json
from pathlib import Path
root = sorted(Path("/root/afs-shared/backups").glob("predeploy-*AFS-0508"))[-1]
manifest = json.loads((root / "manifest.json").read_text())
print("manifest_ticket=", manifest["ticket_id"])
print("pins_present=", (root / "release_pins.txt").is_file())
print("store_present=", (root / "order_submissions" / "records").is_dir())
print("baseline=", manifest["baseline_sha256"])
PY'
```

Expected: `manifest_ticket= AFS-0508`, `pins_present= True`, `store_present= True`. Do not print the pin file or the submission records; they can contain operational ids.

If the command prints `baseline_reset failed: rolled back`, the `predeploy-*` directory was not published. Fix the reported refusal (`order_submissions unreadable`, `journals unreadable`, `deploy lock held`, `open position`, `open order`, `shadow variants active`) and run the backup again. Do not delete a published backup to retry; use a new ticket only under a separate decision.

## 5. Reset

Skip this section when preflight printed `reset_needed=no`.

Dry-run:

```bash
ssh hetzner "AFS_PYTHON=$PY $PROC/scripts/ops/baseline_reset.sh reset \
  --shared-dir /root/afs-shared \
  --status-file /root/afs-maint/exposure.json \
  --target-sha $TARGET_SHA \
  --ticket $TICKET \
  --operator-token \"AUTHORIZE BASELINE RESET $TARGET_SHA $TICKET\" \
  --dry-run"
```

Expected:

```text
dry_run=reset
target_sha=f8b044be38df8f14300bdf47465fc3ff0c2c0a62
ticket_id=AFS-0508
prior_baseline_sha256=none
writes=none
```

`prior_baseline_sha256` is a 64-hex digest when an authorization file already existed. `writes=none` is required. A token that does not name this SHA and this ticket exits 1 with `baseline_reset refused: operator token refused`.

Reset:

```bash
ssh hetzner "AFS_PYTHON=$PY $PROC/scripts/ops/baseline_reset.sh reset \
  --shared-dir /root/afs-shared \
  --status-file /root/afs-maint/exposure.json \
  --target-sha $TARGET_SHA \
  --ticket $TICKET \
  --operator-token \"AUTHORIZE BASELINE RESET $TARGET_SHA $TICKET\""
```

Expected:

```text
baseline_reset=ok
target_sha=f8b044be38df8f14300bdf47465fc3ff0c2c0a62
ticket_id=AFS-0508
prior_baseline_sha256=none
authorization_sha256=<64 hex>
audit=appended
recorded_at=<UTC>
operator_user=<unix user>
```

The audit line in `/root/afs-shared/baseline_audit.jsonl` records the same user, timestamp, SHA, and prior hash. The previous authorization bytes, when there were any, are in `/root/afs-shared/baseline_authorization.json.bak-<UTC>`. A full `.env` copy is kept under `/root/afs-shared/baseline_reset_backups/<UTC>/.env` for this command's own rollback. That copy contains secrets. Do not print it.

On `baseline_reset failed: rolled back`, the six pins and the authorization file are back to their pre-command bytes. Do not promote. Re-run preflight and stop if it does not match the pre-reset reading.

What the reset changes, and only this:

- the six Path A pins in `/root/afs-shared/.env`
- `/root/afs-shared/baseline_authorization.json`
- one appended audit line

`BROKER`, `MAX_CONTRACTS_HARD_CAP`, `LIVE_TRADING_ENABLED`, `TRADOVATE_ENV`, `EXPECTED_LIVE_COMMIT`, `EXPECTED_RELEASE_FINGERPRINT`, `EXPECTED_LIVE_BRANCH`, and `EXPECTED_RISK_RULES_SHA256` stay as they were. The running `futures-bot` process keeps its old environment until the promote restart below. The reset command does not restart a unit.

### Observation windows

No on-disk counter is zeroed.

| Stays untouched | What changes, and when |
|---|---|
| `journal_*.jsonl`, `errors.log` | Nothing in the reset. Daily counters are rebuilt from these files the next time `futures-bot` starts. |
| `track_b_daily_heartbeat.jsonl` and `track_b_heartbeat_state/` | Nothing. Track B history is not a posture pin. The backup copies it and leaves the original. |
| Trend-shadow history and `VARIANTS_START` | Not written. An active `VARIANTS_START` refuses the reset. |
| `order_submissions/records/` | Not deleted and not rewritten. The backup copies the directory. |
| Research trial ledger and strategy inventory | Not opened by this procedure. This reset is not a new inventory epoch. |

After `futures-bot` reloads the env, new decisions use `SCHEDULE_MODE=always_on_shadow`, `EXIT_MODE=static`, and `HTF_DIRECTION_MODE=off`. `always_on_shadow` does not submit orders through the execution gate, so the operational `current` / `runner_shadow` window stops for new bars. Bars already recorded in Track B and in the journals stay recorded. Existing broker positions are not flattened.

## 6. Verify Path A

```bash
ssh hetzner "AFS_PYTHON=$PY $PROC/scripts/ops/baseline_reset.sh preflight --shared-dir /root/afs-shared"; echo "preflight_rc=$?"
ssh hetzner "grep -qx 'LIVE_TRADING_ENABLED=false' /root/afs-shared/.env && echo LIVE_still_off"
ssh hetzner "grep -qx 'TRADOVATE_ENV=demo' /root/afs-shared/.env && echo TV_still_demo"
ssh hetzner "grep -qx 'BROKER=tradovate' /root/afs-shared/.env && echo BROKER_unchanged"
```

Expected:

```text
path_a_configured=yes
reset_needed=no
authorized_sha=f8b044be38df8f14300bdf47465fc3ff0c2c0a62
SCHEDULE_MODE=always_on_shadow
EXPECTED_PROOF_SCHEDULE_MODE=always_on_shadow
HTF_DIRECTION_MODE=off
EXPECTED_PROOF_HTF_DIRECTION_MODE=off
EXIT_MODE=static
EXPECTED_PROOF_EXIT_MODE=static
preflight_rc=0
LIVE_still_off
TV_still_demo
BROKER_unchanged
```

## 7. Promote

Run `scripts/atomic_release.sh` from a checkout of `PROCEDURE_SHA`, not from the live release tree. Build and the CI proof are unchanged and still required. The gate now reads the authorization file before it returns success on Path A.

A different SHA is refused even on Path A:

```text
promotion refused: baseline authorization does not accept <other-sha>
```

The matching SHA prints:

```text
baseline authorization matches f8b044be38df8f14300bdf47465fc3ff0c2c0a62
```

and does not run the behavior-neutral diff. An unreadable authorization file also refuses. There is no bypass flag.

Promote still restarts services. That restart is not performed by the reset command. Order inside `promote_release`, after the gate accepts the SHA:

1. Acquire `/root/afs-shared/deploy.lock`.
2. Rewrite only `EXPECTED_RELEASE_FINGERPRINT`, `EXPECTED_LIVE_BRANCH`, `EXPECTED_LIVE_COMMIT`, and `EXPECTED_RISK_RULES_SHA256`.
3. Write `current.previous` and move `/root/autonomous-futures-system` to the new release.
4. Write the `futures-bot` drop-in, `systemctl daemon-reload`, then `systemctl restart futures-bot`.
5. Require the service cwd, `/health`, and release integrity.
6. Append `/root/afs-shared/release_history.txt`.
7. If `afs-watcher.service` exists, copy the watcher files into its shared working directory and then `systemctl restart afs-watcher.service`.

`options-scanner.service` is not restarted. These oneshots are not restarted either. Their unit files use `WorkingDirectory=/root/autonomous-futures-system`, so the next time they start they read the new symlink:

- `afs-paper-collection-eod`
- `afs-paper-collection-eow`
- `options-122-prospective`
- `options-setup-capture`
- `afs-day-only-exit`
- `afs-external-heartbeat`
- `afs-wide-stop-demo-eod-fallback`

If promote exits non-zero after it has changed `.env`, the symlink, or a service, stop. Do not run `rollback` and do not run the posture restore unless a separate Operator GO names that command.

After a clean promote, the journal must show the startup reconcile before the new marker:

```bash
ssh hetzner "journalctl -u futures-bot --since '5 min ago' --no-pager | grep -F 'ACCEPTING ENTRIES:' | tail -1"
```

Expected:

```text
ACCEPTING ENTRIES: startup reconciliation complete
```

That line is logged only after `reconcile_submissions_before_entries` returns and before the process accepts requests. A missing line is a stop.

## 8. Restore the prior posture

This is the undo for a reset that has not been promoted, or a posture undo under its own GO. It does not move the symlink and it does not restart units. It writes the six pins back to the values stored in the authorization record and puts the previous authorization file back. When there was no previous file, it removes the one the reset created. It does not copy the secret `.env` backup over the live file, so a later promote's four identity pins are left alone.

```bash
ssh hetzner "AFS_PYTHON=$PY $PROC/scripts/ops/baseline_reset.sh restore \
  --shared-dir /root/afs-shared \
  --status-file /root/afs-maint/exposure.json \
  --ticket $TICKET \
  --operator-token \"AUTHORIZE BASELINE RESTORE $TICKET\" \
  --dry-run"
```

Expected: `dry_run=restore`, `writes=none`. Then the same command without `--dry-run`.

Expected:

```text
baseline_restore=ok
ticket_id=AFS-0508
prior_baseline_sha256=none
audit=appended
recorded_at=<UTC>
operator_user=<unix user>
```

Then preflight again. `path_a_configured` matches the pre-reset reading. A ticket that is not the one stored in the authorization file exits 1 with `baseline_reset refused: ticket does not match the baseline record` and writes nothing. A failure after a write prints `baseline_reset failed: rolled back` and the pins and authorization bytes match the pre-restore file.

Release rollback is a different command and needs a separate GO:

```bash
./scripts/atomic_release.sh rollback
```

It restarts `futures-bot` and then `afs-watcher` when that unit exists, and it rewrites the same four identity pins. It does not restore the six posture pins. Run the posture restore only when the exposure census is flat.

## 9. Controlled DEMO recovery proof

Do this only under a separate GO, after promote, while `LIVE_TRADING_ENABLED=false` and `TRADOVATE_ENV=demo`. It places one 1-lot bracket through the existing `TradovateBroker.execute_bracket` path, then the Operator restarts `futures-bot` once. It is not a strategy signal and it does not change risk limits. Quantity other than 1 is refused. A stop on the wrong side of entry is refused. Do not run it twice: `do_not_retry=1` means the send was unconfirmed and a second place can double-send.

Refresh the census first and require `open_order_count=0`.

Replace the example prices with the current demo quote before running. The sample below is a shape, not a market.

```bash
ssh hetzner "systemd-run --pipe --wait --collect \
  --property=EnvironmentFile=/root/afs-shared/.env \
  --setenv=PYTHONPATH=$PROC \
  --setenv=PYTHONDONTWRITEBYTECODE=1 \
  $PY -B -m ops.baseline_reset recovery-proof place \
  --shared-dir /root/afs-shared \
  --status-file /root/afs-maint/exposure.json \
  --ticket $TICKET \
  --operator-token \"AUTHORIZE DEMO RECOVERY PROOF $TICKET\" \
  --instrument MNQ \
  --direction LONG \
  --entry 21000 \
  --stop 20990 \
  --target-price 21030 \
  --quantity 1"
```

Expected:

```text
quantity=1
instrument=MNQ
recovery_order_result=OPEN
client_order_id=AFSREC<ticket><12 hex>
do_not_retry=0
```

`UNKNOWN` with `do_not_retry=1` is not a failure to ignore and not a reason to place again. Continue to the restart and the check. `baseline_reset refused:` before a client id means nothing was sent; do not restart for this proof.

Restart only `futures-bot`:

```bash
ssh hetzner "systemctl restart futures-bot && systemctl is-active futures-bot"
```

Expected: `active`. Then:

```bash
ssh hetzner "journalctl -u futures-bot --since '5 min ago' --no-pager | grep -F 'ACCEPTING ENTRIES:' | tail -1"
ssh hetzner "systemd-run --pipe --wait --collect \
  --property=EnvironmentFile=/root/afs-shared/.env \
  --setenv=PYTHONPATH=$PROC \
  --setenv=PYTHONDONTWRITEBYTECODE=1 \
  $PY -B -m ops.baseline_reset exposure-census \
  --shared-dir /root/afs-shared \
  --out /root/afs-maint/recovery-status.json"
ssh hetzner "AFS_PYTHON=$PY $PROC/scripts/ops/baseline_reset.sh recovery-proof check \
  --shared-dir /root/afs-shared \
  --status-file /root/afs-maint/recovery-status.json \
  --ticket $TICKET \
  --operator-token \"AUTHORIZE DEMO RECOVERY PROOF $TICKET\""
echo "recovery_rc=$?"
```

Passing proofs, exit 0:

```text
recovery_proof=ADOPTED
```

ADOPTED means the census shows an open position and a working stop, and the submission store is trusted and not blocking.

```text
recovery_proof=BLOCKED
```

BLOCKED means the store is trusted and entries are blocked or recovery is required. The record was not cleared.

Anything else prints `recovery_proof=INCONCLUSIVE` and exits 2. That includes a flat account whose submission row disappeared. A disappeared row is not proof that the reconcile adopted the order. Stop. Do not place another order and do not resume normal demo entries.

`afs-watcher` and `options-scanner` are not restarted for this proof.
