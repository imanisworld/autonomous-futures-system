#!/usr/bin/env bash
# Pinned, immutable install of the options coverage collector on the box.
#
#   AFS_BOX=root@host deploy/coverage/install_coverage_release.sh build   <sha>   # stage <sha> as an immutable coverage release
#   AFS_BOX=root@host deploy/coverage/install_coverage_release.sh activate <sha>  # point coverage/current at it, (re)install timer
#   AFS_BOX=root@host deploy/coverage/install_coverage_release.sh status
#
# What this touches on the box — and ONLY this:
#   $AFS_SHARED_DIR/coverage/releases/<sha>/     immutable tree + its own .venv + release_manifest.json (chmod a-w)
#   $AFS_SHARED_DIR/coverage/current             symlink → releases/<sha>
#   /etc/systemd/system/afs-coverage-collector.{service,timer}   copied from the activated release
# It never reads or writes /root/autonomous-futures-system, never touches
# /root/afs-releases, and never restarts futures-bot, the options scanner or
# the watcher.  The collector therefore runs whatever commit is pinned here,
# not whatever production release happens to be current.
set -euo pipefail

ACTION="${1:-}"
REF="${2:-}"
BOX="${AFS_BOX:?set AFS_BOX (for example root@host)}"
SHARED="${AFS_SHARED_DIR:-/root/afs-shared}"
COVERAGE="$SHARED/coverage"
RELEASES="$COVERAGE/releases"
CURRENT="$COVERAGE/current"
TIMER="afs-coverage-collector.timer"
SERVICE="afs-coverage-collector.service"
ROOT="$(git rev-parse --show-toplevel)"

remote() {
  ssh -o BatchMode=yes -o ConnectTimeout=20 "$BOX" "$@"
}

_require_exact_sha() {
  if [[ ! "${1:-}" =~ ^[0-9a-f]{40}$ ]]; then
    echo "coverage release action requires an exact 40-character lowercase commit SHA" >&2
    return 1
  fi
}

build() {
  _require_exact_sha "$REF"
  git fetch -q origin
  local sha short work archive
  sha="$(git rev-parse "$REF^{commit}")"
  short="${sha:0:12}"
  work="$(mktemp -d "/tmp/afs-coverage-${short}.XXXX")"
  archive="/tmp/afs-coverage-${short}.tgz"
  trap "git worktree remove -f '$work' >/dev/null 2>&1 || true; rm -f '$archive'" EXIT

  git worktree add --detach "$work" "$sha" >/dev/null
  (
    cd "$work"
    # The manifest's repo.commit is the collector's provenance pin (--require-pinned).
    RELEASE_BRANCH=main python3 -m ops.release_manifest --repo-root . --output release_manifest.json
    python3 -c "import json,sys; m=json.load(open('release_manifest.json')); sys.exit(0 if m['repo']['commit']=='$sha' and not m['repo']['dirty'] else 1)"
    tar czf "$archive" --exclude=.git .
  )

  remote "mkdir -p '$RELEASES' '$COVERAGE/daily' '$COVERAGE/aggregate' '$COVERAGE/runs'"
  scp -q "$archive" "$BOX:$archive"
  remote "
    set -e
    test ! -e '$RELEASES/$sha' || { echo 'coverage release already exists: $sha'; exit 2; }
    mkdir '$RELEASES/$sha'
    tar xzf '$archive' -C '$RELEASES/$sha'
    python3 -m venv '$RELEASES/$sha/.venv'
    '$RELEASES/$sha/.venv/bin/pip' install -q --requirement '$RELEASES/$sha/requirements.txt'
    '$RELEASES/$sha/.venv/bin/pip' freeze > '$COVERAGE/release-${sha}-dependencies.txt'
    cd '$RELEASES/$sha' && '$RELEASES/$sha/.venv/bin/python' scripts/options_coverage_collect.py --plan --require-pinned \
      --sqlite '$COVERAGE/options_coverage_observer.sqlite' --data-dir '$COVERAGE' --no-calendar-check >/dev/null
    chmod -R a-w '$RELEASES/$sha'
    rm -f '$archive'
  "
  echo "$sha"
}

activate() {
  _require_exact_sha "$REF"
  remote "
    set -e
    test -d '$RELEASES/$REF' || { echo 'no such coverage release: $REF'; exit 2; }
    ln -sfn '$RELEASES/$REF' '$CURRENT'
    install -m 0644 '$RELEASES/$REF/deploy/systemd/$SERVICE' '/etc/systemd/system/$SERVICE'
    install -m 0644 '$RELEASES/$REF/deploy/systemd/$TIMER' '/etc/systemd/system/$TIMER'
    systemctl daemon-reload
    systemctl enable '$TIMER' >/dev/null
    systemctl start '$TIMER'
    printf '%s activate %s\n' \"\$(date -u +%Y-%m-%dT%H:%M:%SZ)\" '$REF' >> '$COVERAGE/release_history.txt'
    systemctl list-timers '$TIMER' --no-pager
  "
}

status() {
  remote "
    readlink -f '$CURRENT' 2>/dev/null || echo 'coverage/current: not activated'
    ls -1 '$RELEASES' 2>/dev/null || true
    systemctl list-timers '$TIMER' --no-pager 2>/dev/null || true
    tail -n 3 '$COVERAGE/ledger.jsonl' 2>/dev/null || echo 'ledger: empty'
  "
}

case "$ACTION" in
  build) build ;;
  activate) activate ;;
  status) status ;;
  *) echo "usage: $0 {build|activate} <sha> | status" >&2; exit 2 ;;
esac
