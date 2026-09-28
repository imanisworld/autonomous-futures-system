#!/bin/bash
# Detached supervisor (lives in tmux session "afs-watcher"): keeps the read-only
# watcher running; restarts it after 60 s if it ever exits. State/logs: /tmp/afs_watcher
#
# Both tmpfs diagnostic logs are bounded. watcher.stdout.log is streamed through
# one dedicated writer so trimming never races an open append fd; supervisor.log
# uses the same helper for its infrequent status lines.
STATE=/tmp/afs_watcher
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${AFS_WATCHER_LOG_PYTHON:-python3}"
LOG_SINK="$SRC/bounded_log_pipe.py"
WATCHER_STDOUT_MAX=$((4 * 1024 * 1024))
WATCHER_STDOUT_KEEP=$((2 * 1024 * 1024))
SUPERVISOR_LOG_MAX=$((1024 * 1024))
SUPERVISOR_LOG_KEEP=$((512 * 1024))
STORM_WINDOW_SECONDS=600
STORM_BURST=5
STORM_EXIT_CODE=75
restart_times=()

mkdir -p "$STATE"

append_supervisor_log() {
  printf '%s\n' "$1" | "$PYTHON_BIN" "$LOG_SINK" \
    --path "$STATE/supervisor.log" \
    --max-bytes "$SUPERVISOR_LOG_MAX" \
    --keep-bytes "$SUPERVISOR_LOG_KEEP" || true
}

while true; do
  append_supervisor_log "$(date -u +%FT%TZ) supervisor pid=$$ launching watcher in read-only namespace"

  bash /tmp/afs_watcher/run_ro.sh 2>&1 | {
    "$PYTHON_BIN" "$LOG_SINK" \
      --path "$STATE/watcher.stdout.log" \
      --max-bytes "$WATCHER_STDOUT_MAX" \
      --keep-bytes "$WATCHER_STDOUT_KEEP"
    bounded_sink_rc=$?
    # A diagnostic logging failure must not SIGPIPE/terminate the watcher.
    # Keep consuming stdout, then return the original sink failure after the
    # watcher exits so the supervisor can record it.
    if [[ "$bounded_sink_rc" -ne 0 ]]; then
      cat >/dev/null
    fi
    exit "$bounded_sink_rc"
  }
  pipe_rc=("${PIPESTATUS[@]}")
  rc="${pipe_rc[0]}"
  sink_rc="${pipe_rc[1]}"

  if [[ "$sink_rc" -ne 0 ]]; then
    append_supervisor_log "$(date -u +%FT%TZ) supervisor: bounded stdout sink exited rc=$sink_rc"
  fi

  now_epoch="$(date -u +%s)"
  cutoff_epoch=$((now_epoch - STORM_WINDOW_SECONDS))
  recent_restarts=()
  for ts in "${restart_times[@]}"; do
    if (( ts >= cutoff_epoch )); then
      recent_restarts+=("$ts")
    fi
  done
  recent_restarts+=("$now_epoch")
  restart_times=("${recent_restarts[@]}")

  if (( ${#restart_times[@]} >= STORM_BURST )); then
    storm_msg="$(date -u +%FT%TZ) supervisor: restart storm detected (${#restart_times[@]} watcher exits within ${STORM_WINDOW_SECONDS}s) — stopping automatic restarts with rc=${STORM_EXIT_CODE}"
    append_supervisor_log "$storm_msg"
    printf '%s\n' "$storm_msg" >&2
    exit "$STORM_EXIT_CODE"
  fi

  append_supervisor_log "$(date -u +%FT%TZ) supervisor: watcher exited rc=$rc — restarting in 60s"
  sleep 60
done
