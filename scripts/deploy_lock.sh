#!/usr/bin/env bash
# Generic deploy mutex so two concurrent deploy runs (two agent sessions, or a
# human + an agent) cannot stage/promote/restart the box at the same time.
#
# Host-agnostic: callers set REMOTE_EXEC to the name of a function that runs a
# single shell-command string in whatever "remote" means for that caller (ssh
# to the box in production, a local shell in tests). This file carries no
# hostname/IP so it is safe to keep in the public repo.
#
# Ownership-aware: acquire mints a fresh owner token per holder and sets it in
# the caller's shell as DEPLOY_LOCK_OWNER (a genuine global — deploy_lock_acquire
# does not `local` it). Release only removes the lock if the remote meta.txt
# still names that exact owner, so a holder whose lock was --force-lock-broken
# by someone else can never delete the new holder's lock out from under them.
#
# Usage (from a caller script):
#   REMOTE_EXEC=remote                 # e.g. remote() { ssh "$BOX" "$1"; }
#   source scripts/deploy_lock.sh
#   deploy_lock_acquire "$SHARED/deploy.lock" "$REF" "$0" "$FORCE_LOCK" || exit 1
#   trap "deploy_lock_release '$SHARED/deploy.lock' '$DEPLOY_LOCK_OWNER'" EXIT
#   ... critical section (stage/verify/promote/restart) ...

_DEPLOY_LOCK_COUNTER=0

_deploy_lock_new_owner() {
  _DEPLOY_LOCK_COUNTER=$((_DEPLOY_LOCK_COUNTER + 1))
  echo "$$-$(date +%s 2>/dev/null || echo 0)-$RANDOM-${_DEPLOY_LOCK_COUNTER}"
}

_deploy_lock_write_meta() {
  local lock_dir="$1" ref="$2" script_name="$3" owner="$4"
  local meta
  meta="$(printf 'ref=%s\nuser=%s\nhost=%s\nstart_utc=%s\nscript=%s\npid=%s\nowner=%s\n' \
    "$ref" "$(whoami)" "$(hostname)" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$script_name" "$$" "$owner")"
  printf '%s\n' "$meta" | "$REMOTE_EXEC" "cat > '$lock_dir/meta.txt'"
}

deploy_lock_acquire() {
  local lock_dir="$1" ref="$2" script_name="$3" force_lock="${4:-}"

  if "$REMOTE_EXEC" "mkdir '$lock_dir'" >/dev/null 2>&1; then
    DEPLOY_LOCK_OWNER="$(_deploy_lock_new_owner)"
    _deploy_lock_write_meta "$lock_dir" "$ref" "$script_name" "$DEPLOY_LOCK_OWNER"
    echo "  deploy lock acquired ($lock_dir, owner=$DEPLOY_LOCK_OWNER)"
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
    DEPLOY_LOCK_OWNER="$(_deploy_lock_new_owner)"
    _deploy_lock_write_meta "$lock_dir" "$ref" "$script_name" "$DEPLOY_LOCK_OWNER"
    echo "  deploy lock acquired after forced break (owner=$DEPLOY_LOCK_OWNER)"
    return 0
  fi

  echo "✗ another deploy holds the lock — refusing to proceed:"
  echo "$existing" | sed 's/^/    /'
  echo "  If you are certain that deploy is stale/abandoned, re-run with --force-lock."
  return 1
}

# Removes the lock ONLY if it still names owner_token as its owner. A stale
# holder (one whose lock was force-broken by someone else in the meantime)
# calling this is a safe no-op — it never touches the new holder's lock.
deploy_lock_release() {
  local lock_dir="$1" owner_token="${2:-}"
  local current_owner
  current_owner="$("$REMOTE_EXEC" "grep '^owner=' '$lock_dir/meta.txt' 2>/dev/null" 2>/dev/null | cut -d= -f2- || true)"
  if [[ -z "$owner_token" || -z "$current_owner" || "$current_owner" != "$owner_token" ]]; then
    return 0
  fi
  "$REMOTE_EXEC" "rm -rf '$lock_dir'" >/dev/null 2>&1 || true
}
