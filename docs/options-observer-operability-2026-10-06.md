# Options prospective observer — operability and runtime-integrity (2026-10-06)

Status: **source only, NOT installed, NOT deployed.** No box access was used
in this session (no `claude-audit` route from this environment), so every
runtime statement below is either cited from the repo record or marked
UNVERIFIED.

## 1. Read-only status tool

`ops/options_observer_status.py` — stdlib-only, runs with system `python3`,
prints one JSON document (`schema: options-observer-status-v1`).

| Need | Field |
|---|---|
| timer/unit status | `units[]` (`ActiveState`, `SubState`, `Result`, `ExecMainStatus`, `LastTriggerUSec`, `NextElapseUSecRealtime`, `NRestarts`, `DropInPaths`) |
| service health | `units[].health` (`OK` / `DEGRADED` / `UNKNOWN`); oneshot units judged by last `Result` |
| which code tree runs | `units[].runtime_tree.classification` = `PINNED_RELEASE` / `LIVE_TREE` / `MIXED` / `UNKNOWN`, plus `release` SHA |
| journal existence / size / hash | `journals[].exists`, `size_bytes`, `sha256`, `lines`, `malformed_lines` |
| capped/redacted tail | `journals[].tail` (≤ 20 rows, allowlisted keys only, strings redacted) |
| last event timestamp | `journals[].last_event_at`, `last_event_age_seconds`, `record_type_counts` |
| trigger-monitor heartbeat | `heartbeats[]` — reports `NOT_PRESENT` until the trigger monitor (Cursor's early-capture work) writes `options_prospective_trigger_monitor_heartbeat.json` |

Safety properties (each has a test in `tests/test_options_observer_status.py`):

- `systemctl show` is called with an explicit property allowlist that excludes
  every `Environment*` / credential property; ExecStart argv values are
  dropped (only interpreter path + module are kept).
- Only allowlisted units and allowlisted file names under
  `/root/afs-shared/logs` are touched; symlink escapes are refused; no
  caller-supplied path is opened. `.env` is never read.
- Tail rows are projected to known keys; URLs, bearer tokens, `key=value`
  secrets and long opaque tokens are redacted (lowercase hex SHAs kept).
- Writes nothing; exit code 0 = all OK, 1 = something degraded.

### Proposed install for `claude-audit` (operator action, not done)

Mirror the existing `grok-audit` pattern (sudo-restricted single wrapper):

```sh
install -o root -g root -m 0755 ops/options_observer_status.py /usr/local/sbin/afs-options-observer-status
# /etc/sudoers.d/claude-audit-observer-status  (visudo -cf before install)
claude-audit ALL=(root) NOPASSWD: /usr/local/sbin/afs-options-observer-status, /usr/local/sbin/afs-options-observer-status --tail [0-9], /usr/local/sbin/afs-options-observer-status --tail [0-9][0-9]
```

Root is needed only because `/root/afs-shared/logs` is under `/root`. The
wrapper takes no path arguments. Install the exact reviewed blob and record
its sha256.

## 2. Runtime-integrity question: pinned release vs `/root/autonomous-futures-system`

Evidence from the repo:

- The tracked unit `ops/systemd/options-122-prospective.service` sets
  `WorkingDirectory=/root/autonomous-futures-system` and
  `ExecStart=/root/autonomous-futures-system/.venv/bin/python …`. That path is
  the **mutable live symlink that follows futures-bot releases**.
- The deployment record (`docs/options-122-prospective-deployment-2026-09-18.md`)
  and the handoff (§ "Collector release") say a box-only drop-in
  `10-release.conf` pins the immutable collector release
  (`36e73f1…`, then `db9bc7e2c00559fc969af7be0e2cb12b00a1454c` since
  2026-09-22 22:52Z). That drop-in is **not tracked in Git**.
- The 2026-10-02 box fix (`docs/agent-work-state.md`) lists
  `options-122-prospective` among "every unit referencing the live path" when
  adding `no-bytecode.conf`. That phrase is consistent with the base unit
  referencing the live path; it does not prove the effective ExecStart does.

Classification: **real configuration-integrity risk, effective runtime
UNVERIFIED.**

- If `10-release.conf` overrides both `WorkingDirectory` and `ExecStart` (with
  an empty `ExecStart=` reset), the collector runs pinned — the deployment doc
  says it does.
- If it overrides only one of them, or if the drop-in is ever lost (it was
  already involved in a `203/EXEC` incident on 09-21), the collector silently
  runs whatever futures release the live symlink points to — a code change
  inside the frozen `122-IEX-E1` epoch with no record.
- Reinstalling the tracked unit file alone would produce the live-tree
  posture.

Prepared correction (safe, not applied):

- `ops/systemd/options-122-prospective.service.d/10-release.conf.template`
  — tracked copy of the pin shape: `WorkingDirectory`, `PYTHONPATH`, and an
  `ExecStart=` reset + pinned ExecStart. A test proves its argv is identical
  to the tracked unit except for the interpreter tree, so applying it cannot
  change the frozen collector arguments.
- The status tool flags `LIVE_TREE` / `MIXED` as `runtime_integrity.ok=false`.

Verification (operator or installed wrapper):
`afs-options-observer-status --unit options-122-prospective.service` must show
`runtime_tree.classification = PINNED_RELEASE` and `release = db9bc7e2…`
(or the currently approved pin). If it shows `LIVE_TREE`/`MIXED`, apply the
template **only with operator GO**, since the epoch is frozen.

Not changed: the base unit file, the collector code, the epoch, the box.
Cursor owns the collector/trigger-timing code; this PR touches neither.
