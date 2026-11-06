#!/bin/bash
# Static self-test for the grok-ops lane. Prints 34/34 when every guard holds.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"
export PYTHONPATH="${REPO}${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
exec "${AFS_OPS_PYTHON:-python3}" -B -m ops.grok_ops.self_test
