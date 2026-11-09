#!/bin/bash
# Static self-test for the grok-ops lane. Prints the passing count when every guard holds.
# The interpreter is /usr/bin/python3. AFS_OPS_PYTHON and cwd are not consulted.
set -euo pipefail

die() {
  echo "$1" >&2
  exit "${2:-1}"
}

SCRIPT=${BASH_SOURCE[0]}
[[ "$SCRIPT" == /* ]] || die "refusing a path that depends on cwd" 2
REPO=$(dirname "$(dirname "$(dirname "$SCRIPT")")")
[[ "$SCRIPT" == "$REPO/scripts/ops/grok_ops_self_test.sh" ]] || die "unexpected script location" 2
if [[ -L "$SCRIPT" || -L "$REPO" ]]; then
  die "refusing symlink" 2
fi
unset AFS_OPS_PYTHON AFS_PYTHON AFS_OPS_ROOT PYTHONPATH
exec env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$REPO" \
  /usr/bin/python3 -B -m ops.grok_ops.self_test
