#!/bin/bash
# Detached supervisor (lives in tmux session "afs-watcher"): keeps the read-only
# watcher running; restarts it after 60 s if it ever exits. State/logs: /tmp/afs_watcher
STATE=/tmp/afs_watcher
mkdir -p "$STATE"
while true; do
  echo "$(date -u +%FT%TZ) supervisor pid=$$ launching watcher in read-only namespace" >> "$STATE/supervisor.log"
  bash /tmp/afs_watcher/run_ro.sh >> "$STATE/watcher.stdout.log" 2>&1
  rc=$?
  echo "$(date -u +%FT%TZ) supervisor: watcher exited rc=$rc — restarting in 60s" >> "$STATE/supervisor.log"
  sleep 60
done
