# Existing SSH: staged agent maintenance capability (proposal only)

**Verdict: AUDIT ONLY / NOT INSTALLED.** No VPS connection, SSH user/key change, sudoers edit, systemd change, restart, deploy, broker operation, or environment change was performed in preparing this proposal. Reuse the already working Cursor Cloud SSH credential and forced-command gate; add no account or connection without proven necessity. This package implements **one** bounded maintenance operation (scanner memory). Future restart/demo-deployment capabilities below are **policy proposals, not implemented commands or authorization to execute**.

## Agent responsibilities and future operation boundaries

**Do not interpret the 350M/600M helper's restrictions as permanent bans on all cloud maintenance.** The requested long-term operating model is: Cursor Cloud can perform approved maintenance and **controlled** demo/paper release operations without a Mac, while Claude and Grok can inspect, review and (only when separately authorized and technically connected) perform specifically allowlisted work. None gets a general root shell.

| Task | Cursor Cloud | Claude | Grok | Current implementation |
|---|---|---|---|---|
| Audit system, logs, status, release evidence | Existing restricted reads; request exact extra read verbs only when needed | Review evidence; use its own permitted remote reads when connectivity permits | Independently review; use its own permitted remote reads when connectivity permits | Partly available; existing forced-command route only verified by operator report |
| Set scanner MemoryMax 350M ↔ 600M | May execute **after separate operator approval and one-time privileged helper installation** | Check tests/evidence; may execute only if separately granted a comparable narrow route | Independent safety review; may execute only if separately granted a comparable narrow route | **Prepared:** exact fixed-verb helper and tests, NOT installed |
| Restart an approved service | **Potentially permitted after specific GO and separately implemented, tested fixed-service interface** | Review evidence; executor only if independently approved | Review evidence; executor only if independently approved | **NOT implemented; do not add a generic `systemctl` path** |
| Build/verify immutable demo release | May prepare/test an exact candidate after source/CI review and operator build authorization | Adversarial QA | Independent exact-SHA review | No deployment capability added by this package |
| Promote/rollback an exact demo release | **Potentially permitted**, but only with separate promotion GO, preflight/rollback gates and an independently reviewed purpose-built interface | Independent QA or separately designated executor, never sole self-approver | Independent review or separately designated executor, never sole self-approver | **NOT implemented or approved** |
| Modify trading code | PR/CI/review only, not ad-hoc VPS edits | PR/CI/review only | PR/CI/review only | Repo source workflow only |
| Place/cancel broker orders, change trade/risk state, turn on live trading | **Denied** absent a wholly separate explicit policy and verified safety program; no capability in this package | Denied | Denied | Denied |
| Unrestricted root shell or unbounded sudo | Denied | Denied | Denied | Denied |

**Source review and operation are different approvals.** No agent may approve its own implementation/review, self-write a root-owned approval record, or infer runtime permission from a GitHub CI PASS or an operator conversation. A reviewer cannot claim a VPS change was made merely by approving its source.

**For future service restarts:** require an exact service allowlist, explicit purpose/restart approval, working-order and broker-state freshness, in-flight/simulated-position/evidence continuity decisions, deploy-lock/session filters, clean stop conditions, pre/post health, rollback/recovery plan and audit logs. If any prerequisite is unknown, stop. `options-scanner.service` and `futures-bot.service` must be evaluated individually; not all service restarts are equivalent.

**For future demo releases:** use an exact immutable SHA/tree; independent trading-path review, required CI and isolated B10 test proof; approved lock/dependency and Python changes; watcher/history/collector isolation; fresh B1–B10 and near-GO broker/lock state; no unapproved open-paper-position interruption; tested rollback; separate operator build approval and separate operator promotion approval. A narrow release helper must refuse arbitrary branches, service names, shell fragments and mutable refs. Nothing here authorizes broker submission or a live route.

**Connectivity distinction:** Cursor's existing forced-command SSH route is reported working. Claude and Grok's cloud SSH access is currently reported blocked. Do not copy Cursor's key to them or create accounts automatically. Restore their pre-existing approved egress/access path or continue with GitHub-based independent review; future host roles are only possible after an authorized connectivity and identity check.

## Evidence boundary and smallest change

**Known from operator reports:** Cursor Cloud's existing SSH reaches a forced-command gate that accepts 14 fixed read-only verbs and returns exit 126 for shell, sudo, `/root` reads or restarts. Grok/Claude connections are a different problem. The **actual installed** gate source, location, key options and current sudoers are **not in this GitHub repository and could not be inspected from this session**. The first task for an authorized administrator is to identify these bytes read-only; no guessing or replacement of the gate.

**Smallest integration, conditional on reviewing installed dispatcher:** add three exact verbs to its existing *literal* `case "$SSH_ORIGINAL_COMMAND" in` dispatch. Keep every prior allowlisted verb and its original action unchanged. Do not change `authorized_keys`, `sshd_config`, SSH keys, authentication, host firewall, or the identity of the audit user. If the actual dispatcher uses a different implementation, do not apply the example verbatim; produce a narrow reviewed equivalent first.

```sh
# Exact additional arms inside the existing verified forced-command case:
options-memory-status)
  exec /usr/bin/sudo -n /usr/local/sbin/afs-options-memory inspect ;;
options-memory-apply-600)
  exec /usr/bin/sudo -n /usr/local/sbin/afs-options-memory apply-600 ;;
options-memory-rollback-350)
  exec /usr/bin/sudo -n /usr/local/sbin/afs-options-memory rollback-350 ;;
```

**Never** dispatch `bash`, `sh`, `sudo`, `systemctl`, a variable command, an arbitrary path, or `eval "$SSH_ORIGINAL_COMMAND"`. Deny any unrecognized verb and any appended arguments. Ensure the actual wrapper still rejects `options-memory-apply-600 extra` with exit 126. Keep any forced-command `restrict`, `no-port-forwarding`, `no-pty` and current SSH controls intact.

## Root-owned interface: exact scope

File: `ops/maintenance/options_scanner_memory.py` — install as `/usr/local/sbin/afs-options-memory`, owned by root, 0755, using existing `/usr/bin/python3`.

| Input verb | Effect |
|---|---|
| `inspect` | Read service active state, `MemoryMax`, `MemoryHigh`, current RAM/swap, restart count. No mutation. |
| `apply-600` | Only when `MemoryMax == 350 MiB`, service active, host `MemAvailable >= 800 MiB`, `MemoryHigh` compatible, parent cgroup-v2 `memory.max` leaves >=128 MiB beyond 600 MiB, AND a root-owned unexpired approval explicitly names the two allowed actions. Log attempt; call only `/usr/bin/systemctl set-property --runtime options-scanner.service MemoryMax=600M`; verify resulting exact limit and stable restart count; write root-owned receipt; log outcome. If verification/receipt/log fails, attempt automatic `350M` rollback. If rollback cannot be confirmed, HOLD and require administrator. |
| `rollback-350` | Only when `MemoryMax == 600 MiB` and a root-owned receipt proves this helper previously applied 600M; revert via `/usr/bin/systemctl set-property --runtime options-scanner.service MemoryMax=350M`, verify and log. Paired rollback remains possible if original approval expires. |

**No free-form values, arbitrary units, service names or options**. The helper cannot deploy, submit broker orders, restart services, change environment variables, touch trade logs, or alter risk rules. It uses Python stdlib, fixed `systemctl` argv, restricted env, and no shell. Concurrent calls are rejected by a root-owned lock. State and append-mode audit path are under `/var/lib/afs-maintenance/` and `/var/log/afs-options-memory-audit.jsonl`; approval is root-owned under `/etc/afs-maintenance/`. The approval file is a local root-controlled record, **not a permission for Cursor to authorize its own change**.

**Scope of approval:** operator's prior 350M→600M approval is for this specific service adjustment and its paired rollback; installing/changing the privileged maintenance interface or sudoers is a **separate approval**. Approval TTL is temporary (48 h in installation example). After it expires, a future *apply* needs a new authorized approval record; Cursor cannot produce that record. This avoids unrestricted delegated authority. Future operations require independent design and review, not command wildcard expansion.

**Behavior of `--runtime`:** change is immediate, **does not require a service restart**, but is not persistent across a host reboot. It does not instantaneously drain existing swap; monitor full scanner cycles before calling swap-pressure alarms resolved. Any difference in effective systemd behavior on the installed version is a stop condition.

## Offline test plan — no VPS changes

```sh
python3 -m unittest discover -s tests -p 'test_options_scanner_memory.py' -v
python3 -m py_compile ops/maintenance/options_scanner_memory.py
```

Fake `systemctl` tests cover exact arguments, inspect, apply/rollback, missing approval, low available memory, parent cap, `MemoryHigh`, inactive service, unexpected current value, unknown action, missing receipt, symlink approval rejection, and failed post-change verification with automatic rollback. No root, broker, network, `.env`, or VPS needed. **This is not a live-host validation.** Independent security review and a safe **staging/fake-box** integration test of the actual installed forced-command wrapper are mandatory before installation.

## One-time administrator procedure (PREPARED — DO NOT RUN YET)

**Gate 0 — verify installed route read-only first.** Use the existing authorized administrator login (not Cursor's restricted identity); substitute the *actual existing* Cursor Cloud SSH username, **not** a new user. The commands intentionally avoid printing SSH key contents:

```sh
AUDIT_USER='<existing Cursor VPS_USER from the authorized configuration>'
getent passwd "$AUDIT_USER"
sudo /usr/sbin/sshd -T -C "user=$AUDIT_USER,host=localhost,addr=127.0.0.1" | grep -i '^forcecommand '
AUDIT_HOME="$(getent passwd "$AUDIT_USER" | cut -d: -f6)"
sudo awk 'match($0, /command="[^"]*"/) { print substr($0,RSTART,RLENGTH) }' "$AUDIT_HOME/.ssh/authorized_keys"
sudo -l -U "$AUDIT_USER"
```

Locate the exact installed restricted dispatcher indicated by `command="..."` or `ForceCommand` (without displaying keys). Inspect its owner/mode and dispatch structure. Do **not** proceed if identity/path/allowlist is uncertain, if multiple conflicting match rules exist, if sudoers is unexpectedly broad, or if the current gate cannot be extended narrowly. Record a backup and hash of the gate before any later approved edit. Check that the `options-scanner.service` unit exists, uses cgroup-v2 and reports 350 MiB. Do not change it during inspection.

**Gate 1 — independently review** the helper source, fake-box test evidence, exact dispatch insertion, and sudoers command scope. The steps below are **one-time installation steps only after a separate explicit operator GO**.

```sh
# On the existing authorized administrator session, AFTER independent review and GO:
set -euo pipefail
AUDIT_USER='<existing Cursor VPS_USER, verified above>'
# Stage the reviewed archive in the existing authorized admin session first.
# Do not upload it using the restricted audit key; use existing Ops/admin tooling.
BUNDLE='/root/afs-cursor-vps-maintenance-v2.zip'
EXPECTED_SHA256='b8a8223796c246c2430006179b572831890309c795241e235c31865f2d8d558e'
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
unzip -p "$BUNDLE" ops/maintenance/options_scanner_memory.py > "$TMP"
printf '%s  %s\n' "$EXPECTED_SHA256" "$TMP" | sha256sum -c -
sudo install -d -o root -g root -m 0700 /etc/afs-maintenance /var/lib/afs-maintenance
sudo install -o root -g root -m 0755 "$TMP" /usr/local/sbin/afs-options-memory
sudo sha256sum /usr/local/sbin/afs-options-memory
sudo /usr/local/sbin/afs-options-memory inspect   # strictly read-only

# Exact sudo command + argument allowlist. Existing account ONLY.
# Create this file only if /etc/sudoers.d/afs-options-memory does not exist.
test ! -e /etc/sudoers.d/afs-options-memory
SUDOERS_TMP="$(mktemp)"
printf '%s ALL=(root) NOPASSWD: /usr/local/sbin/afs-options-memory inspect, /usr/local/sbin/afs-options-memory apply-600, /usr/local/sbin/afs-options-memory rollback-350\n' "$AUDIT_USER" > "$SUDOERS_TMP"
sudo visudo -cf "$SUDOERS_TMP"
sudo install -o root -g root -m 0440 "$SUDOERS_TMP" /etc/sudoers.d/afs-options-memory
rm -f "$SUDOERS_TMP"
sudo visudo -cf /etc/sudoers.d/afs-options-memory
sudo visudo -c
sudo -l -U "$AUDIT_USER"
```

**Gate 2 — operator-scoped approval (separate administrative step, only if still explicitly authorized):**

```sh
sudo /usr/bin/python3 - <<'PY'
import datetime as dt, json, os
path='/etc/afs-maintenance/options-memory-approval.json'
a={'schema':1,'unit':'options-scanner.service',
   'allowed_actions':['apply-600','rollback-350'],
   'expires_utc':(dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=48)).isoformat()}
fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
with os.fdopen(fd,'w') as f:
    json.dump(a,f)
    f.write('\n')
PY
sudo chown root:root /etc/afs-maintenance/options-memory-approval.json
sudo chmod 0600 /etc/afs-maintenance/options-memory-approval.json
```

**Gate 3 — SSH dispatcher integration:** add ONLY the three static case arms shown above to the installed, root-owned **existing** forced-command script after recording a verified backup and exact SHA; syntax-check and independently compare its diff. Ensure the whole preexisting allowlist still works. Because the actual installed script is **unavailable here**, a safe automated edit command cannot be specified before Gate 0; it would risk overwriting the wrong security file. Never substitute a replacement `authorized_keys`, `sshd_config`, or generic shell dispatcher.

**Gate 4 — read-only end-to-end acceptance:** from Cursor Cloud using **its existing SSH credential** run `options-memory-status`; verify 350 MiB, existing allowlist unchanged, no unexpected sudo rights, arbitrary arguments denied and there is no shell. Do not exercise `options-memory-apply-600` during the installation acceptance test. The change itself is a distinct explicitly approved maintenance action AFTER the privileged interface is installed and separately verified.

**Stop here.** Do not deploy futures/options code, restart services, change scanner memory, modify SSH keys, or relax network permissions as part of installing this interface.

## Operational rollback / cleanup

- For the **scanner limit after an approved apply**, Cursor uses only the allowlisted `options-memory-rollback-350` verb. It checks a root-owned prior-apply receipt and verifies the return to 350 MiB. If that gate fails, stop and have the administrator perform a separately authorized recovery. Never guess the state or use a generic root shell via the audit key.
- For the **maintenance interface installation** (distinct action requiring operator authorization), restore only the backed-up dispatcher file if it was modified; remove the three-command sudoers file and helper/approval once sessions are idle. Do not delete logs or change SSH keys. The original existing 14 read-only verbs must remain intact.

## Further capability request (not part of this installation)

This installation prepares only `options-memory-status`, `options-memory-apply-600`, and `options-memory-rollback-350`. For **approved restart** or **approved demo deployment**, return with the exact requested service/release and its existing authoritative runbook, then build and independently fake-box-test a **separate narrowly scoped command**. Do not implement these with a general `systemctl restart`, `afs-deploy`, unrestricted script path, or an SSH command interpreter. Never alter the trading execution path as part of maintenance permissions.

Before any administrator installation, reconcile the source and tests against the **actual installed** SSH forced-command dispatcher, user identity and sudoers; the current package has not inspected those installed files. No automatic patch instructions can be trusted until that read-only inspection is complete.
