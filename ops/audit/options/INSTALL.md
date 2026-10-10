# Options scanner audit scripts — install notes (Option B only)

These files are for Operator review. This pull request does not install them.
It does not restart the scanner, change the environment, deploy a release, or
change any trading path.

A shared-group or ACL install (previously called Option A), which would have
let `grok-options-audit` read the scanner database directly, was rejected for
least-privilege reasons.

The scripts take no arguments (`sys.argv` is ignored), read no environment
variables, and open the scanner database only as
`file:<path>?mode=ro` with `PRAGMA query_only=ON`. The only path constant is
`ROOT = "/root"`.

## What gets installed

| Item | Value |
| --- | --- |
| Directory | `/usr/local/libexec/afs-options-audit/` |
| Owner | `root:root` |
| Directory mode | `0755` |
| File mode | `0644` (or `0755`; they are started by `python3`, not executed directly) |
| Files | the four `scanner_*.py` scripts from this directory |
| Account | `grok-options-audit` may run them only through the sudoers lines below |

The directory and the files are not writable by `grok-options-audit`.

```sh
install -d -o root -g root -m 0755 /usr/local/libexec/afs-options-audit
install -o root -g root -m 0644 \
  scanner_alerts_today.py \
  scanner_suppression_detail.py \
  scanner_signa_summary.py \
  scanner_script_drift.py \
  /usr/local/libexec/afs-options-audit/
```

## sudoers

Install with `visudo -f /etc/sudoers.d/afs-options-audit`. One command per
line. No wildcards. No extra arguments.

`""` is sudoers syntax for "this command takes no further arguments". It is
not a literal argv token. sudoers matches the exact command line, so dropping
`-I` or `-B`, or appending anything, does not match.

```sudoers
Defaults:grok-options-audit env_reset
Defaults:grok-options-audit !setenv
grok-options-audit ALL=(root) NOPASSWD: /usr/bin/python3 -I -B /usr/local/libexec/afs-options-audit/scanner_alerts_today.py ""
grok-options-audit ALL=(root) NOPASSWD: /usr/bin/python3 -I -B /usr/local/libexec/afs-options-audit/scanner_suppression_detail.py ""
grok-options-audit ALL=(root) NOPASSWD: /usr/bin/python3 -I -B /usr/local/libexec/afs-options-audit/scanner_signa_summary.py ""
grok-options-audit ALL=(root) NOPASSWD: /usr/bin/python3 -I -B /usr/local/libexec/afs-options-audit/scanner_script_drift.py ""
```

Each line runs as root, with `NOPASSWD`, `env_reset`, and `!setenv`.

## Forced-command wrapper

The wrapper that already restricts the audit SSH login is not in this
repository. Add exact-match arms. Do not pass user input through. Do not use
a wildcard or `"$@"`.

```sh
case "$SSH_ORIGINAL_COMMAND" in
  scanner-alerts-today)
    exec timeout 120 sudo -n /usr/bin/python3 -I -B /usr/local/libexec/afs-options-audit/scanner_alerts_today.py
    ;;
  scanner-suppression-detail)
    exec timeout 120 sudo -n /usr/bin/python3 -I -B /usr/local/libexec/afs-options-audit/scanner_suppression_detail.py
    ;;
  scanner-signa-summary)
    exec timeout 120 sudo -n /usr/bin/python3 -I -B /usr/local/libexec/afs-options-audit/scanner_signa_summary.py
    ;;
  scanner-script-drift)
    exec timeout 120 sudo -n /usr/bin/python3 -I -B /usr/local/libexec/afs-options-audit/scanner_script_drift.py
    ;;
  *)
    echo "denied" >&2
    exit 1
    ;;
esac
```

The command after `sudo -n` is the exact sudoers command. `timeout 120` is
outside sudoers; it is the wrapper's own limit.

## SQLite sidecar caveat

The scripts run as root and open the database `mode=ro`. If the file is in
WAL mode and `-wal` / `-shm` are absent, a read-only open may try to create
those sidecars or fail with "attempt to write a readonly database".
`ALLOW_IMMUTABLE_FALLBACK` is `False`, so the scripts do not switch to
`immutable=1`. They exit 3, print an `ERROR:` line, and print
`RESULT_INCOMPLETE=true`. That is intended. There is no best-effort result.

This repository never sets `PRAGMA journal_mode=wal`. A rollback journal is
the likely mode on the server. A rollback-journal reader holds a SHARED lock
for each statement, and that lock blocks the scanner's commit until the
statement finishes (the scanner's busy timeout is 5 seconds). Keep each run
short. The id floor avoids a full scan of `scans`. If id order versus
timestamp order cannot be proved inside the bounded budget, the script prints

```
ERROR: ROWID_TIMESTAMP_ORDER_NOT_MONOTONIC
RESULT_INCOMPLETE=true
```

and exits 6. It does not fall back to a long table scan.

## Pre-install checks

From a checkout of the reviewed commit:

```sh
cd ops/audit/options
sha256sum -c SHA256SUMS
```

After copying onto the server, the installed files must match those digests:

```sh
cd /usr/local/libexec/afs-options-audit
sha256sum -c /path/to/reviewed/SHA256SUMS
```

`sudo -l -U grok-options-audit` must show only the four command lines above
(plus the two `Defaults` lines). Nothing else.

Record the interpreter the sudoers line will actually run:

```sh
/usr/bin/python3 -I -c 'import sys, sqlite3; print(sys.version); print(sqlite3.sqlite_version)'
```

The server's Python and SQLite versions are not known from this repository.

## Smoke test

```sh
sudo -u grok-options-audit sudo -n \
  /usr/bin/python3 -I -B /usr/local/libexec/afs-options-audit/scanner_script_drift.py
```

A complete run ends with `end_of_report exit=0`. A missing path or an
unreadable database ends with `ERROR:` and `RESULT_INCOMPLETE=true` and a
non-zero status. Do not restart the scanner because a smoke test failed.

Repeat for the other three forced-command names only after the drift script
has been checked. Each run should stay well under the 120 second wrapper cap.

## Rollback

```sh
rm -f /etc/sudoers.d/afs-options-audit
rm -rf /usr/local/libexec/afs-options-audit
```

Remove the four `case` arms from the forced-command wrapper. No scanner
restart, no environment change, no deploy, and no trading-path change is
required: this install does not touch those, and removing it does not either.
