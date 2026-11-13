#!/usr/bin/env bash
# Credential-free Cloud Agent bootstrap for the paper-first test suite.
# Installs CPython 3.13 (same major.minor as .github/workflows/ci.yml) and
# the packages in requirements-dev.txt. Public sources only. No services,
# schedulers, listeners, or broker contact.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

# Force the public index. Ignore any ambient private index configuration.
export PIP_INDEX_URL="https://pypi.org/simple"
export PIP_DISABLE_PIP_VERSION_CHECK=1
unset PIP_EXTRA_INDEX_URL || true

export PATH="${HOME}/.local/bin:${PATH}"

# uv older than 0.13.4 lacks the GHSA-hq85-5cpj-mr62 fix. A uv already on
# PATH below that floor is replaced with the pin, not reused.
UV_PIN="0.13.6"
UV_MIN="0.13.4"

uv_meets_min() {
  local have
  command -v uv >/dev/null 2>&1 || return 1
  have="$(uv --version | awk '{print $2}')"
  [[ "$(printf '%s\n%s\n' "${UV_MIN}" "${have}" | sort -V | head -n1)" == "${UV_MIN}" ]]
}

if ! uv_meets_min; then
  python3 -m pip install --user --upgrade "uv==${UV_PIN}"
  hash -r
  if ! uv_meets_min; then
    echo "uv on PATH is still older than ${UV_MIN}: $(command -v uv || echo none)" >&2
    exit 1
  fi
fi

uv python install 3.13

venv_ok=0
if [[ -x .venv/bin/python ]]; then
  if .venv/bin/python -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) else 1)'; then
    venv_ok=1
  fi
fi
if [[ "${venv_ok}" -ne 1 ]]; then
  uv venv .venv --python 3.13 --seed --clear
fi

.venv/bin/python -m pip install -r requirements-dev.txt
