#!/bin/bash
# Run the watcher inside a PRIVATE mount namespace where every production tree
# is READ-ONLY at the kernel level. Only /tmp (watcher state) stays writable.
# Nothing here changes the host's own mount table (propagation=private).
exec unshare -m --propagation private bash -c '
  set -e
  for d in /root /etc /opt /usr/local; do
    [ -d "$d" ] || continue
    mount --bind "$d" "$d" && mount -o remount,bind,ro "$d"
  done
  # sanity: refuse to start unless production is really read-only in here
  if [ -w /root/afs-shared/logs ] || [ -w /root/afs-shared/.env ] || [ -w /root/autonomous-futures-system/risk_rules.yaml ]; then
    echo "RO GUARD FAILED — production writable inside namespace; not starting" >&2; exit 97
  fi
  exec /usr/bin/python3 /tmp/afs_watcher/watcher.py "$@"' _ "$@"
