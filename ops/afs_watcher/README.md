# afs-watcher deploy artifacts

`watcher.py`, `watcher_memory_guard.py` (a standalone measurement-only copy —
not the same module as `ops/watcher_memory_guard.py`, which is the runtime
package's consumer-side gate), and `run_ro.sh`/`supervisor.sh` are a verbatim
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
on its own ONLY when it is provably the release wrapper's restart: the tick
has no other BLOCKED finding, the `.env` pins are coherent and name the
release this watcher verified at startup (commit, fingerprint, epoch), the
release link and the live pid's `/proc/<pid>/cwd` are that release, the
service is active, `NRestarts` is unchanged, and the baseline was recorded
under a DIFFERENT release. That last condition is the discriminator: a hand
`systemctl restart futures-bot` on the same release still BLOCKS as
`unexpected_restart`, a crash still BLOCKS as `service_crash_restart`, and
anything ambiguous fails closed. Adoption is logged, written to
`events.jsonl` as `REBASELINED`, and keeps the previous baseline under
`adopted_from`. A baseline recorded before release tracking is stamped with
the current release while its pid still matches, so the next deploy can be
proven; across a restart it is never stamped.

`install_afs_watcher_service.sh` is a deploy action (copies these files to
`/root/afs-shared/afs_watcher_src/`, installs and enables the systemd unit).
It must be run manually, as root, after stopping the tmux-supervised watcher
(`tmux kill-session -t afs-watcher`) to avoid two supervisors racing on the
same state file. It does not start the service. Re-running it on a box where
the unit is already installed is idempotent, but the running watcher keeps
executing the old `/tmp/afs_watcher/watcher.py` until
`systemctl restart afs-watcher.service` (bootstrap re-copies on start).
