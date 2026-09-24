#!/usr/bin/env bash
# Idempotent repository bootstrap for the Cloud Agent environment.
#
# Runs after the repository is checked out. Creates a Python 3.13 virtualenv at
# .venv and installs the dev dependency set (requirements-dev.txt = runtime
# requirements + pytest + pytest-cov), matching how CI installs and runs tests.
#
# Safe to run repeatedly: `python -m venv` is a no-op when the venv already
# exists, and pip installs are idempotent.
set -euo pipefail

# Resolve the repository root (parent of this script's .cursor directory).
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3.13}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    PYTHON_BIN="python3"
fi

echo "Using interpreter: $("$PYTHON_BIN" --version)"

if [ ! -x ".venv/bin/python" ]; then
    "$PYTHON_BIN" -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements-dev.txt

# Provide a local .env for tooling that expects one. Unit tests disable dotenv
# loading via tests/conftest.py, so this never leaks deployment values.
if [ ! -f .env ]; then
    cp .env.example .env
fi

echo "Environment ready. Run tests with: .venv/bin/python -m pytest -q"
