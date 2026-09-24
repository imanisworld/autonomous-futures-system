#!/usr/bin/env bash
# Idempotent repository bootstrap for the Cloud Agent environment.
#
# Runs after the repository is checked out. Creates a Python 3.13 virtualenv at
# .venv and installs the dev dependency set (requirements-dev.txt = runtime
# requirements + pytest + pytest-cov), matching how CI installs and runs tests.
#
# Works both on the committed .cursor/Dockerfile base (python:3.13-bookworm,
# where Python 3.13 is already present) and on a plain default image (where it
# provisions Python 3.13 from the deadsnakes PPA on first run).
#
# Safe to run repeatedly: interpreter provisioning and venv creation are
# skipped when already satisfied, and pip installs are idempotent.
set -euo pipefail

# Resolve the repository root (parent of this script's .cursor directory).
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Provision a Python 3.13 interpreter when the base image does not ship one.
# On the committed Dockerfile base python3.13 already exists, so this is a
# no-op there. On a Debian/Ubuntu default image it installs from deadsnakes.
ensure_python313() {
    if command -v python3.13 >/dev/null 2>&1; then
        return 0
    fi
    if ! command -v sudo >/dev/null 2>&1 || ! command -v apt-get >/dev/null 2>&1; then
        return 0
    fi
    echo "python3.13 not found; provisioning via deadsnakes PPA"
    export DEBIAN_FRONTEND=noninteractive
    sudo apt-get update
    sudo apt-get install -y --no-install-recommends software-properties-common
    sudo add-apt-repository -y ppa:deadsnakes/ppa
    sudo apt-get update
    sudo apt-get install -y --no-install-recommends python3.13 python3.13-venv python3.13-dev
}

ensure_python313

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
