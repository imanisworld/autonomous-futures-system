#!/bin/bash
# Installs the watcher evidence archiver (archive_events.py + 5-minute timer).
# Deploy action, run manually on the box as root. Does NOT touch afs-watcher.service
# or the running watcher; safe to run while it is up. Idempotent.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "must run as root" >&2
  exit 1
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST=/root/afs-shared/afs_watcher_src

mkdir -p "$DEST" /root/afs-shared/afs_watcher_archive
cp -f "$SRC_DIR/archive_events.py" "$DEST/"
chmod 700 "$DEST/archive_events.py"

for unit in afs-watcher-archive.service afs-watcher-archive.timer; do
  sed "s#__AFS_WATCHER_SRC__#$DEST#g" "$SRC_DIR/$unit" > "/etc/systemd/system/$unit"
  chmod 644 "/etc/systemd/system/$unit"
done

systemctl daemon-reload
systemctl enable --now afs-watcher-archive.timer
# first pass now, so the current tmpfs contents are on disk before the next tick
systemctl start afs-watcher-archive.service

cat <<MSG
Installed afs-watcher-archive.timer (every 5 min) -> /root/afs-shared/afs_watcher_archive/
  systemctl list-timers afs-watcher-archive.timer
  journalctl -u afs-watcher-archive.service -n 3
MSG
