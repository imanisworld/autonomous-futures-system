# afs-watcher deploy artifacts

`watcher.py`, `watcher_memory_guard.py` (a deploy-local byte-identical copy of
`ops/watcher_memory_guard.py`; the watcher itself uses only the sampling and
evaluation primitives), and `run_ro.sh`/`supervisor.sh` are a verbatim
capture of the box's live, hand-deployed `/tmp/afs_watcher/` watcher as of
2026-09-03 (sha256 of `watcher.py`: `292bdcc4f43b0cb8d031f74e5283bc3bb672cf61d2874d2aa9fcf2ede4ea582c`).
They previously existed only on the box, deployed by ad hoc SSH across
sessions with no version history; capturing them here makes them reviewable
and lets `install_afs_watcher_service.sh` deploy them from a known source.

`bootstrap_tmp_state.sh` and `afs-watcher.service` add reboot survival: the
watcher currently only restarts itself (via `supervisor.sh`'s retry loop)
after a tmux session is started by hand, so a VPS reboot silently leaves it
down until an operator notices. `afs-watcher.service` mirrors the existing
`futures-bot.service` systemd unit — it starts `supervisor.sh` at boot and
restarts it if it exits; `supervisor.sh`'s own loop is untouched, and no
second watcher process type is introduced. `bootstrap_tmp_state.sh` copies
the three files `run_ro.sh` hard-codes a `/tmp/afs_watcher/` path for into
that (tmpfs) directory before each start — this is required because tmpfs
does not survive a reboot.

Restart detection and the sanctioned deploy. The watcher keeps a process
`baseline` (pid, `ActiveEnterTimestamp`, `NRestarts`, and the release it was
recorded against) in `state.json`. A new pid is adopted as the new baseline
on its own ONLY when it is provably the release wrapper's restart. The
decision is taken as the LAST step of the runtime check, after every other
finding of the tick is in (the provisional `unexpected_restart` BLOCK is
raised the moment the restart is seen and only withdrawn on proof): the tick
has no other BLOCKED finding, the `.env` pins are coherent and name the
release this watcher verified at startup (commit, fingerprint, epoch), the
release link and the live pid's `/proc/<pid>/cwd` are that release, the
service is active, `NRestarts` is unchanged, and the baseline was recorded
under a DIFFERENT release. That last condition is the discriminator: a hand
`systemctl restart futures-bot` on the same release still BLOCKS as
`unexpected_restart`, a crash still BLOCKS as `service_crash_restart`, and
anything ambiguous fails closed. Adoption is logged, written to
`events.jsonl` as `REBASELINED`, posted once to the Discord error route, and
keeps the previous baseline under `adopted_from`. A baseline recorded before
release tracking is stamped with the current release only on a clean tick
while its pid still matches and its cwd is the verified release, so the next
deploy can be proven; across a restart it is never stamped.

`install_afs_watcher_service.sh` is a deploy action (copies these files to
`/root/afs-shared/afs_watcher_src/`, installs and enables the systemd unit).
It must be run manually, as root, after stopping the tmux-supervised watcher
(`tmux kill-session -t afs-watcher`) to avoid two supervisors racing on the
same state file. It does not start the service. Re-running it on a box where
the unit is already installed is idempotent, but the running watcher keeps
executing the old `/tmp/afs_watcher/watcher.py` until
`systemctl restart afs-watcher.service` (bootstrap re-copies on start).

## Evidence archive (`archive_events.py`, `afs-watcher-archive.timer`)

`/tmp/afs_watcher` is tmpfs and the watcher runs with `/root` remounted
read-only, so the watcher's own `events.jsonl` (every BLOCKED raise,
REBASELINED adoption, DAILY verdict) and the snapshots BLOCKED rows point at
could never reach durable storage — on 2026-09-21 the file only reached back
to the last reboot and the 2026-09-14 feed outage had no on-box evidence
left. `archive_events.py` runs from a host-side 5-minute systemd timer,
reads `/tmp/afs_watcher` only, appends lines it has not archived yet to
`/root/afs-shared/afs_watcher_archive/events.jsonl` (dedupe by exact line —
every row carries its own UTC stamp) and copies unseen snapshots alongside.
It never writes under `/tmp`, never signals the watcher, and is not part of
`afs-watcher.service`. Loss window after an abrupt reboot is at most one
interval. Install with `install_events_archive.sh` (root, on the box; safe
while the watcher is running).


## Server resource diagnostics (repo-only, not deployed)

The watcher also records a read-only resource snapshot every five minutes so
memory incidents can be attributed instead of inferred. It observes both
`futures-bot` and `options-scanner` process RSS/swap, thread count, open file
descriptors and soft descriptor limit, plus each process's cgroup v2
`memory.current` / `memory.events` when available. Host context includes
`/proc/pressure/memory`, the five largest RSS processes, the watcher tmpfs
usage, and the shared-log filesystem usage.

Descriptor use at or above 75% of the process soft limit is a WARNING only.
Watcher tmpfs at or above 80% and shared-log storage at or above 90% are also
WARNING only. These observations do not stop, restart, signal, deploy, or
otherwise modify either runtime.

`memory.jsonl` and `watcher.log` are non-durable telemetry under the
RAM-backed watcher state directory. They are now size-bounded (2 MiB and
4 MiB respectively) so the monitor cannot grow those files indefinitely while
diagnosing a low-memory host. Durable event/snapshot evidence is unchanged and
continues through the existing archive timer.

The deploy-local `ops/afs_watcher/watcher_memory_guard.py` is intentionally
byte-identical to the canonical `ops/watcher_memory_guard.py`; regression
coverage fails if they drift.
