#!/usr/bin/env bash
# Operator entry for the futures baseline-reset procedure.
# The Python module owns every guard. This wrapper does not source .env,
# does not print secret values, and does not restart units.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1

exec "${AFS_PYTHON:-python3}" -B -m ops.baseline_reset "$@"
