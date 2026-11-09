#!/bin/bash
# Remove grok-ops privilege. The append-only audit log is kept.
# Root ignores AFS_OPS_*. A non-root run is the test prefix only.
set -euo pipefail

if [[ "$(id -u)" -eq 0 ]]; then
  unset AFS_OPS_ROOT AFS_OPS_PYTHON AFS_PYTHON AFS_OPS_FLAT_FILE
  PREFIX=""
else
  PREFIX="${AFS_OPS_ROOT:-}"
  if [[ -z "$PREFIX" ]]; then
    echo "uninstall_grok_ops.sh must run as root" >&2
    exit 1
  fi
fi

rm -f \
  "$PREFIX/etc/sudoers.d/grok-ops" \
  "$PREFIX/usr/local/sbin/afs-grok-ops" \
  "$PREFIX/usr/local/sbin/afs-ops-authorize" \
  "$PREFIX/usr/local/sbin/afs-ops-record-refusal" \
  "$PREFIX/usr/local/sbin/afs-ops-preflight" \
  "$PREFIX/usr/local/sbin/afs-ops-backup" \
  "$PREFIX/usr/local/sbin/afs-ops-baseline-reset-dry-run" \
  "$PREFIX/usr/local/sbin/afs-ops-baseline-reset" \
  "$PREFIX/usr/local/sbin/afs-ops-promote" \
  "$PREFIX/usr/local/sbin/afs-ops-verify" \
  "$PREFIX/usr/local/sbin/afs-ops-rollback" \
  "$PREFIX/usr/local/sbin/afs-ops-status" \
  "$PREFIX/var/lib/afs-ops/.ssh/authorized_keys"
rm -rf "$PREFIX/usr/local/lib/afs-ops"

if [[ -z "$PREFIX" ]] && id grok-ops >/dev/null 2>&1; then
  passwd -l grok-ops >/dev/null || true
  chsh -s /usr/sbin/nologin grok-ops || true
fi

echo "grok-ops privilege removed; audit log kept"
