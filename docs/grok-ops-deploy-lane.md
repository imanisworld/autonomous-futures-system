# grok-ops deploy lane

Restricted deploy account for the AFS VPS. The Ops agent SSHes in as `grok-ops` and can run only the verb allowlist below. It does not get a shell, a TTY, or forwarding. Privilege is a sudoers drop-in of exact script paths. This document is source and procedure. Installing it is an Operator action on the VPS. Nothing in this repository installs the account by itself.

The read-only Grok audit accounts (`grok-options-audit`, `grok-qa-audit`) are documented as `command=` and `restrict` users. Their installer is not in this repository at `main` `f8b044be`. This lane follows that forced-command shape, with a stricter sudoers file than the older maintenance drop-in: no wildcard and no shell.

## What the lane can do

After the Operator installs it and types a GO, `grok-ops` may run only:

| Verb | Effect |
|---|---|
| `preflight` | Report the hard gates. Writes an audit row. |
| `status` | Same report, no mutation. |
| `backup` | Runs `/usr/bin/python3 -B -m scripts.backup_proof_data` with `LOG_DIR` and `PROOF_BACKUP_DIR` from the root-owned release config. |
| `baseline-reset-dry-run` | Calls `scripts/ops/baseline_reset.sh reset --dry-run` with the merged #1257 flags. Does not consume the GO token. Does not sign an approval. |
| `baseline-reset` | Consumes a GO token, then calls `scripts/ops/baseline_reset.sh reset --target-sha <sha> --release-fingerprint <digest> --ticket <ledger-id> --approval /root/afs-maint/afs-approval.txt --signature /root/afs-maint/afs-approval.txt.sig --status-file /var/lib/afs-ops/exposure.json --shared-dir /root/afs-shared`. |
| `promote <sha>` | Consumes a GO token, then runs `scripts/atomic_release.sh promote <sha>`. |
| `verify` | Runs `scripts/atomic_release.sh verify` for `EXPECTED_LIVE_COMMIT`. |
| `rollback <release>` | Runs `scripts/atomic_release.sh rollback` only when `<release>` is the basename of `current.previous`. |

`promote` and `rollback` do not pass `--force-lock`. The sanctioned release script still performs the release. This lane does not grow a second copy of the symlink swap.

## What the lane cannot do

- Open a shell, allocate a TTY, or forward ports, agent, or X11.
- Run any sudo command other than the eight verb scripts.
- Mint its own GO token. `afs-ops-authorize` is root-only and is absent from sudoers.
- Print `.env` values. Refusal text is a gate code.
- Edit broker routing, risk limits, or `LIVE_TRADING_ENABLED`.
- Promote or reset while any hard gate is closed.
- Break a held deploy lock.
- Roll back to a release other than the one `current.previous` already names.

Any other SSH command is refused and written to the audit log as `INVALID`. The raw command is not stored, so a pasted secret does not land in the log.

## Hard gates

Every verb checks these before it acts. `preflight` and `status` report them and exit non-zero when one is closed. Mutating verbs stop before the sanctioned script.

- `LIVE_TRADING_ENABLED` must be the exact value `false`.
- `TRADOVATE_ENV` must be the exact value `demo`.
- No open position and no working order. A missing, stale, or unreadable flatness snapshot refuses the action.
- `deploy.lock` must be absent. If the lock path cannot be stated, the action refuses.
- Shadow variants must be inactive: `VARIANTS_START = None` in the live `research/trend_r3_forward/trend_variants_shadow.py`, and `VARIANTS_START` unset in the trading env file.

The flatness snapshot is the fixed path `/var/lib/afs-ops/flat.json`. The lane does not read `AFS_OPS_FLAT_FILE` or any other `AFS_OPS_*` variable. The file is JSON with `checked_at`, `positions_readable`, `open_positions`, `orders_readable`, and `working_orders`. `checked_at` older than 120 seconds is unreadable. The lane does not open a broker client. A root job the Operator already trusts has to refresh that file. Until the file is fresh, promote and reset stay refused.

## GO token

`promote` and `baseline-reset` require a one-time token.

```bash
sudo /usr/local/sbin/afs-ops-authorize <40-hex-sha> <ledger-id> <64-hex-release-fingerprint>
```

The fingerprint is the Operator's approved-manifest digest. The lane stores that value and passes it to `--release-fingerprint`. It does not hash the staged release tree. The command prints a token id. The token expires two hours after creation, binds to that exact SHA and fingerprint, and is consumed on first use. A second use, an expired token, a SHA that does not match the token, or a fingerprint that does not match the target manifest is refused before the token is consumed. The lane does not treat the SHA as an ancestor of `origin/main`. `scripts/atomic_release.sh` would, and a git-less release is refused before that script runs. The agent does not pass the token id on the SSH command. The verb script consumes the single open token for that SHA (`promote`) or the single open token (`baseline-reset`). Two open tokens for the same action refuse as ambiguous.

Create the token only after the Operator has typed GO for that SHA and ledger id. The token is the record of that GO.

## Install

From the reviewed commit, as root. Both paths are absolute, and neither path may contain a symlink:

```bash
sudo bash /root/afs-releases/<sha>/scripts/ops/install_grok_ops.sh --public-key /root/grok-ops.pub
sudo bash /root/afs-releases/<sha>/scripts/ops/grok_ops_self_test.sh
sudo visudo -c -f /etc/sudoers.d/grok-ops
```

The installer creates system user `grok-ops` with shell `/usr/sbin/nologin`, a locked password, and this authorized_keys line:

```text
restrict,command="/usr/local/sbin/afs-grok-ops",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding
```

followed by the public key. It writes `/etc/sudoers.d/grok-ops` only after `visudo -c` accepts the file. It creates `/var/lib/afs-ops/audit.log` and `/var/lib/afs-ops/tokens` (mode `0700`, root). It writes `/etc/afs-ops/release.env` if that file is missing. Set `AFS_BOX` there to the sanctioned release target before the first promote. Do not put trading secrets in that file.

The self-test prints `35/35` when the allowlist, key guard, sudoers policy, reset argv, and `visudo -c` all hold. Root runs use `/usr/bin/python3` and `env -i`. `AFS_OPS_PYTHON` is ignored. A root install also ignores `AFS_OPS_ROOT`.

Example session after install:

```bash
sudo /usr/local/sbin/afs-ops-authorize <sha> <ledger-id> <release-fingerprint>
ssh -i grok-ops-key grok-ops@<vps> preflight
ssh -i grok-ops-key grok-ops@<vps> promote <sha>
```

## Revoke the key

Either delete the line in `/var/lib/afs-ops/.ssh/authorized_keys`, or remove the whole privilege set:

```bash
sudo bash /root/afs-releases/<sha>/scripts/ops/uninstall_grok_ops.sh
```

Uninstall removes the sudoers drop-in, the verb scripts, the dispatcher, and the authorized_keys file. It locks the account again. It leaves `/var/lib/afs-ops/audit.log` in place.

## Audit log

`/var/lib/afs-ops/audit.log` is append-only JSONL written with `O_APPEND`. Each row has `ts`, `actor`, `verb`, `sha`, `token_id`, `ledger_id`, and `result`. The actor is `grok-ops` plus the client address when `SSH_CLIENT` is an address. Results are `ok`, `failed`, or `refused:<code>`.

## Baseline-reset dependency

`baseline-reset` and `baseline-reset-dry-run` call this exact path and no other:

```text
scripts/ops/baseline_reset.sh
```

Merged #1257 put that script on `main`. The lane calls this interface and no other flags:

```text
scripts/ops/baseline_reset.sh reset --target-sha <sha> --release-fingerprint <digest> --ticket <ledger-id> --approval /root/afs-maint/afs-approval.txt --signature /root/afs-maint/afs-approval.txt.sig --status-file /var/lib/afs-ops/exposure.json --shared-dir /root/afs-shared
```

`baseline-reset-dry-run` appends `--dry-run`. There is no `--operator-token` and no `--env-file`. The approval file and the signature are the copies Imani signed on the Mac and installed at those two paths. This lane passes the paths through. It does not write the approval, does not call `ssh-keygen`, and does not mint a signature. A missing script still exits `78` with audit result `refused:dependency-missing` and does not consume a token. The fingerprint is the value the Operator passed to `afs-ops-authorize`. The lane does not hash the staged tree to invent it. Do not point the verb at a different script.

The root wrappers `cd /` and run `/usr/bin/python3 -B -P`. `-P` keeps the caller cwd off `sys.path`. Promote and baseline-reset open `atomic_release.sh` or `baseline_reset.sh`, and `deploy_lock.sh`, from the target release `/root/afs-releases/<sha>`. That tree is authorized only when `/root/afs-shared/release-complete/<sha>` matches `release_manifest.json` `fingerprint_sha256` and `repo.commit` is that SHA, and the GO token's fingerprint is that same digest. A directory name or an environment variable is not that proof. `scripts/atomic_release.sh` is byte-identical to `main`. It sets `ROOT` from `git rev-parse --show-toplevel`, so promote, verify, and rollback are fail-closed on a git-less release: they audit `unsupported-release-model` and do not consume a GO token. They cannot deploy a git-less release until a separate change to that script is approved by Imani. Directories are opened from `/` with `openat` and `O_NOFOLLOW`. A symlink, including a symlinked ancestor or a symlinked release directory, is refused as `release-symlink` before mode or owner bits are checked. The runner then execs the checked path. That exec is still by path, so a root process can replace the file in the window after the fd check and before exec. The pin is re-checked immediately before exec, and the program has to stay under the pinned directory.

## Threat model

The account is least privilege because each layer drops something the previous layer still had.

1. SSH `restrict` plus explicit `no-pty`, `no-port-forwarding`, `no-agent-forwarding`, and `no-X11-forwarding` removes an interactive session and forwarding. `command=` pins the session to `/usr/local/sbin/afs-grok-ops`.
2. The dispatcher accepts eight verbs and rejects `;`, `&`, backticks, `$()`, and any other character outside the verb grammar. It never uses a shell.
3. sudoers allows those eight verb paths plus `/usr/local/sbin/afs-ops-record-refusal`, each followed by `""` so sudo passes no arguments. It sets `env_reset` and a fixed `secure_path`. The refusal recorder accepts only the codes `injection`, `unknown-verb`, and `malformed`. A wildcard, a shell, a bare path, or an empty path in that file fails `validate_sudoers_text` and `visudo -c`. The audit log is root-owned. `grok-ops` does not have write permission on it.
4. The verb scripts re-check posture, flatness, the deploy lock, and shadow variants. They refuse closed when a check cannot be read.
5. `promote` and `baseline-reset` additionally require a root-minted token, a SHA match, a fingerprint match, a two-hour lifetime, and single use. The token authorizes the target release under `/root/afs-releases/<sha>`, proven by the release-complete record and the manifest, which may be a different tree from the release that is currently running. Promote, verify, and rollback refuse a git-less tree before the token is consumed. `scripts/atomic_release.sh` is unchanged from `main` and is not run on that tree.

A stolen `grok-ops` key can request the eight verbs. It cannot mint a GO, cannot pass the hard gates while the account is live or not flat, and cannot obtain a shell from this sudoers file. It still depends on the Operator keeping `release.env`, the flatness snapshot, and the baseline-reset script as root-owned paths. Compromise of root is outside this lane.

This lane does not authorize a deploy. A deploy still needs a reviewed SHA, a green exact-head CI proof for `atomic_release.sh`, a fresh flatness snapshot, and an Operator GO that mints the token.
