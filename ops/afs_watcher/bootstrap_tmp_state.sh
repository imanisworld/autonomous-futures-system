#!/bin/bash
# Recreates the watcher's tmpfs runtime directory from the persistent source
# checkout before every (re)start. /tmp/afs_watcher is deliberately tmpfs —
# state.json is lost on reboot by design (see ops/watcher_memory_guard.py) —
# but watcher.py, watcher_memory_guard.py and run_ro.sh must exist there
# because run_ro.sh hard-codes /tmp/afs_watcher/watcher.py. Idempotent: safe
# to run on every supervisor (re)start, including systemd's Restart=always.
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE="${AFS_WATCHER_TMP_STATE:-/tmp/afs_watcher}"
mkdir -p "$STATE"
cp -f "$SRC/watcher.py" "$SRC/watcher_memory_guard.py" "$SRC/run_ro.sh" "$STATE/"
chmod 700 "$STATE/run_ro.sh"
