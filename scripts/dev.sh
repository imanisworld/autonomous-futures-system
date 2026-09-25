#!/usr/bin/env bash
# Local development helper for the Autonomous Futures Paper System.
#
# Loads .env.local (if present) BEFORE running, so local-only overrides
# such as LOG_DIR and PUBLIC_DEMO_MODE take effect without editing the
# box-oriented .env or passing inline env vars on every command.
#
# The app itself loads .env via load_dotenv(override=False), so any var
# exported here (from .env.local) wins over the value in .env.
#
# Usage:
#   scripts/dev.sh engine [--dry-run] [main.py args...]
#   scripts/dev.sh web                 # start the webhook + dashboard
#   scripts/dev.sh test [pytest args]  # run the tests/ suite
#   scripts/dev.sh shell -- <cmd...>   # run any command with .env.local loaded
#
# Copy .env.local.example to .env.local first:
#   cp .env.local.example .env.local
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Load local overrides (simple KEY=value lines) into the environment.
if [ -f .env.local ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env.local
  set +a
fi

# Prefer the project virtualenv if it exists.
if [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
else
  PY=python3
fi

cmd="${1:-help}"
shift || true

case "$cmd" in
  engine)
    exec "$PY" main.py "$@"
    ;;
  web)
    exec "$PY" -m webhook "$@"
    ;;
  test)
    exec "$PY" -m pytest tests/ "$@"
    ;;
  shell)
    # Drop a leading "--" separator if present, then exec the rest.
    if [ "${1:-}" = "--" ]; then shift; fi
    if [ "$#" -eq 0 ]; then
      echo "usage: scripts/dev.sh shell -- <command...>" >&2
      exit 2
    fi
    exec "$@"
    ;;
  help | -h | --help)
    # Print the leading comment block (skip the shebang, stop at the first
    # non-comment line) with the leading "# " markers removed.
    awk 'NR==1 && /^#!/ {next} /^#/ {sub(/^# ?/, ""); print; next} {exit}' \
      "${BASH_SOURCE[0]}"
    ;;
  *)
    echo "unknown subcommand: $cmd" >&2
    echo "run 'scripts/dev.sh help' for usage" >&2
    exit 2
    ;;
esac
