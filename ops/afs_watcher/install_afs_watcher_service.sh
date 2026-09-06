#!/bin/bash
# Installs afs-watcher as a systemd service so the read-only memory watcher
# re-arms itself after a VPS reboot, instead of requiring a human to open
# tmux and run supervisor.sh by hand. Mirrors futures-bot.service: systemd's
# only job is starting supervisor.sh at boot and restarting it if it ever
# exits — supervisor.sh's own 60s retry loop around the watcher process is
# unchanged and remains the actual supervision. No second watcher is built.
#
# This is a deploy action, not a code change: run it manually, on the box,
# as root. Do NOT run this while the tmux-supervised watcher is still active
# — two supervisors would both write /tmp/afs_watcher/state.json and both
# post Discord notifications. Stop the tmux one first:
#   tmux kill-session -t afs-watcher
# then install and start this unit.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "must run as root" >&2
  exit 1
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST=/root/afs-shared/afs_watcher_src
UNIT=/etc/systemd/system/afs-watcher.service

mkdir -p "$DEST"
cp -f "$SRC_DIR"/watcher.py "$SRC_DIR"/watcher_memory_guard.py \
      "$SRC_DIR"/run_ro.sh "$SRC_DIR"/supervisor.sh \
      "$SRC_DIR"/bootstrap_tmp_state.sh "$DEST/"
chmod 700 "$DEST"/*.sh

sed "s#__AFS_WATCHER_SRC__#$DEST#g" "$SRC_DIR/afs-watcher.service" > "$UNIT"
chmod 644 "$UNIT"

systemctl daemon-reload
systemctl enable afs-watcher.service

cat <<MSG
Installed and enabled afs-watcher.service (persistent source: $DEST).
NOT started. If a tmux-supervised watcher is still running, stop it first:
  tmux kill-session -t afs-watcher
Then start the service:
  systemctl start afs-watcher.service
  systemctl status afs-watcher.service
If afs-watcher.service was ALREADY running, it is still executing the previous
watcher.py from /tmp/afs_watcher; the files installed here are adopted only on
  systemctl restart afs-watcher.service
(bootstrap_tmp_state.sh re-copies them into /tmp on every start).
MSG
