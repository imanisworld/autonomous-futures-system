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
# optional read-only advisory lane (watcher imports it defensively; absent = disabled)
[ -f "$SRC/watcher_triage.py" ] && cp -f "$SRC/watcher_triage.py" "$STATE/"
# optional Discord card layout (watcher imports it defensively; absent = plain text)
[ -f "$SRC/discord_card.py" ] && cp -f "$SRC/discord_card.py" "$STATE/"
# optional plain-English wording (watcher imports it defensively; absent = simple local wording)
[ -f "$SRC/plain_english.py" ] && cp -f "$SRC/plain_english.py" "$STATE/"
chmod 700 "$STATE/run_ro.sh"
