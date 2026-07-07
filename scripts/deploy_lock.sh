#!/usr/bin/env bash
# Generic deploy mutex so two concurrent deploy runs (two agent sessions, or a
# human + an agent) cannot stage/promote/restart the box at the same time.
#
# Host-agnostic: callers set REMOTE_EXEC to the name of a function that runs a
# single shell-command string in whatever "remote" means for that caller (ssh
# to the box in production, a local shell in tests). This file carries no
# hostname/IP so it is safe to keep in the public repo.
#
# Usage (from a caller script):
#   REMOTE_EXEC=remote                 # e.g. remote() { ssh "$BOX" "$1"; }
#   source scripts/deploy_lock.sh
#   deploy_lock_acquire "$SHARED/deploy.lock" "$REF" "$0" "$FORCE_LOCK" || exit 1
#   trap "deploy_lock_release '$SHARED/deploy.lock'" EXIT
#   ... critical section (stage/verify/promote/restart) ...

deploy_lock_acquire() {
  local lock_dir="$1" ref="$2" script_name="$3" force_lock="${4:-}"
  local meta
  meta="$(printf 'ref=%s\nuser=%s\nhost=%s\nstart_utc=%s\nscript=%s\npid=%s\n' \
    "$ref" "$(whoami)" "$(hostname)" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$script_name" "$$")"

  if "$REMOTE_EXEC" "mkdir '$lock_dir'" >/dev/null 2>&1; then
    printf '%s' "$meta" | "$REMOTE_EXEC" "cat > '$lock_dir/meta.txt'"
    echo "  deploy lock acquired ($lock_dir)"
    return 0
  fi

  local existing
  existing="$("$REMOTE_EXEC" "cat '$lock_dir/meta.txt' 2>/dev/null" 2>/dev/null || true)"
  [[ -z "$existing" ]] && existing="(lock directory present, no metadata readable)"

  if [[ "$force_lock" == "--force-lock" ]]; then
    echo "  ⚠ breaking existing deploy lock (--force-lock):"
    echo "$existing" | sed 's/^/    /'
    "$REMOTE_EXEC" "rm -rf '$lock_dir'"
    if ! "$REMOTE_EXEC" "mkdir '$lock_dir'" >/dev/null 2>&1; then
      echo "✗ failed to acquire deploy lock even after --force-lock"
      return 1
    fi
    printf '%s' "$meta" | "$REMOTE_EXEC" "cat > '$lock_dir/meta.txt'"
    echo "  deploy lock acquired after forced break"
    return 0
  fi

  echo "✗ another deploy holds the lock — refusing to proceed:"
  echo "$existing" | sed 's/^/    /'
  echo "  If you are certain that deploy is stale/abandoned, re-run with --force-lock."
  return 1
}

deploy_lock_release() {
  local lock_dir="$1"
  "$REMOTE_EXEC" "rm -rf '$lock_dir'" >/dev/null 2>&1 || true
}
