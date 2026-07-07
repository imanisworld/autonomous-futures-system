#!/usr/bin/env bash
# Versioned immutable release builder/promoter for AFS.
#
# Host identity stays outside the repository:
#   AFS_BOX=root@example AFS_SHARED_DIR=/root/afs-shared \
#     scripts/atomic_release.sh build origin/main
#   scripts/atomic_release.sh verify <sha>
#   scripts/atomic_release.sh promote <sha>
#   scripts/atomic_release.sh rollback
#
# Each action acquires a box-side deploy mutex ($AFS_SHARED_DIR/deploy.lock,
# see scripts/deploy_lock.sh) before touching the box and releases it on exit
# (normal, error, or interrupt). If another deploy holds the lock, the action
# refuses and prints that lock's metadata (ref/user/host/time/pid). Pass
# --force-lock as the trailing arg to break a stale/abandoned lock.
set -euo pipefail

ACTION="${1:-}"
REF="${2:-origin/main}"
FORCE_LOCK="${3:-}"
BOX="${AFS_BOX:?set AFS_BOX (for example root@host)}"
RELEASES="${AFS_RELEASES_DIR:-/root/afs-releases}"
SHARED="${AFS_SHARED_DIR:-/root/afs-shared}"
CURRENT="${AFS_CURRENT_LINK:-/root/autonomous-futures-system}"
SERVICE="${AFS_SERVICE:-futures-bot}"
ROOT="$(git rev-parse --show-toplevel)"
LOCK_DIR="$SHARED/deploy.lock"

# rollback takes no ref — let a bare --force-lock land in $2 for that action.
if [[ "$ACTION" == "rollback" && "$REF" == "--force-lock" ]]; then
  FORCE_LOCK="--force-lock"
fi

source "$ROOT/scripts/deploy_lock.sh"

remote() {
  ssh -o BatchMode=yes -o ConnectTimeout=20 "$BOX" "$@"
}
REMOTE_EXEC=remote

build_release() {
  deploy_lock_acquire "$LOCK_DIR" "build $REF" "$0" "$FORCE_LOCK" || exit 1
  trap "deploy_lock_release '$LOCK_DIR' '$DEPLOY_LOCK_OWNER'" EXIT

  git fetch -q origin
  local sha short work archive manifest
  sha="$(git rev-parse "$REF")"
  short="${sha:0:12}"
  work="$(mktemp -d "/tmp/afs-release-${short}.XXXX")"
  archive="/tmp/afs-release-${short}.tgz"
  manifest="$work/release_manifest.json"
  trap "git worktree remove -f '$work' >/dev/null 2>&1 || true; rm -f '$archive'; deploy_lock_release '$LOCK_DIR' '$DEPLOY_LOCK_OWNER'" EXIT

  git worktree add --detach "$work" "$sha" >/dev/null
  (
    cd "$work"
    RELEASE_BRANCH=main python3 -m ops.release_manifest \
      --repo-root . --output release_manifest.json
    python3 -m ops.release_integrity --repo-root . --manifest release_manifest.json
    tar czf "$archive" --exclude=.git .
  )

  remote "mkdir -p '$RELEASES' '$SHARED/logs' '$SHARED/data' '$SHARED/backups' '$SHARED/candidate-logs'"
  scp -q "$archive" "$BOX:/tmp/afs-release-${short}.tgz"
  remote "
    set -e
    test ! -e '$RELEASES/$sha' || { echo 'release already exists: $sha'; exit 2; }
    mkdir '$RELEASES/$sha'
    tar xzf '/tmp/afs-release-${short}.tgz' -C '$RELEASES/$sha'
    python3 -m venv '$RELEASES/$sha/.venv'
    '$RELEASES/$sha/.venv/bin/pip' install -q --requirement '$RELEASES/$sha/requirements.txt'
    '$RELEASES/$sha/.venv/bin/pip' freeze > '$SHARED/release-${sha}-dependencies.txt'
    PYTHONPATH='$RELEASES/$sha' '$RELEASES/$sha/.venv/bin/python' \
      -m ops.release_integrity --repo-root '$RELEASES/$sha'
    chmod -R a-w '$RELEASES/$sha'
    rm -f '/tmp/afs-release-${short}.tgz'
  "
  echo "$sha"
}

verify_release() {
  deploy_lock_acquire "$LOCK_DIR" "verify $REF" "$0" "$FORCE_LOCK" || exit 1
  trap "deploy_lock_release '$LOCK_DIR' '$DEPLOY_LOCK_OWNER'" EXIT

  local sha="$REF" short="${REF:0:12}" unit="afs-candidate-${REF:0:12}" fingerprint
  fingerprint="$(remote "'$RELEASES/$sha/.venv/bin/python' -c \"import json;print(json.load(open('$RELEASES/$sha/release_manifest.json'))['fingerprint_sha256'])\"")"
  remote "
    set -e
    test -d '$RELEASES/$sha'
    test -f '$SHARED/.env'
    systemctl stop '$unit' 2>/dev/null || true
    systemd-run --unit='$unit' \
      --property=WorkingDirectory='$RELEASES/$sha' \
      --property=EnvironmentFile='$SHARED/.env' \
      --setenv=PYTHONDONTWRITEBYTECODE=1 \
      --setenv=PYTHONPATH='$RELEASES/$sha' \
      --setenv=PORT=8010 \
      --setenv=LOG_DIR='$SHARED/candidate-logs/$sha' \
      --setenv=SCHEDULE_MODE=always_on_shadow \
      --setenv=EXPECTED_PROOF_SCHEDULE_MODE=always_on_shadow \
      --setenv=HTF_DIRECTION_MODE=off \
      --setenv=EXPECTED_PROOF_HTF_DIRECTION_MODE=off \
      --setenv=EXIT_MODE=static \
      --setenv=EXPECTED_PROOF_EXIT_MODE=static \
      --setenv=RELEASE_INTEGRITY_ENFORCED=true \
      --setenv=EXPECTED_RELEASE_FINGERPRINT='$fingerprint' \
      '$RELEASES/$sha/.venv/bin/python' -m uvicorn webhook.app:app \
        --host 127.0.0.1 --port 8010
    for i in 1 2 3 4 5 6 7 8 9 10; do
      sleep 2
      curl -fsS http://127.0.0.1:8010/health >/dev/null && break
    done
    curl -fsS http://127.0.0.1:8010/health
    curl -fsS http://127.0.0.1:8010/status/tradovate-reliability >/dev/null
    PYTHONPATH='$RELEASES/$sha' '$RELEASES/$sha/.venv/bin/python' \
      -m ops.release_integrity --repo-root '$RELEASES/$sha'
  "
  echo "candidate $short verified on 127.0.0.1:8010 in always_on_shadow"
}

promote_release() {
  deploy_lock_acquire "$LOCK_DIR" "promote $REF" "$0" "$FORCE_LOCK" || exit 1
  trap "deploy_lock_release '$LOCK_DIR' '$DEPLOY_LOCK_OWNER'" EXIT

  local sha="$REF"
  remote "
    set -e
    test -d '$RELEASES/$sha'
    test -f '$SHARED/.env'
    grep -qx 'SCHEDULE_MODE=always_on_shadow' '$SHARED/.env'
    grep -qx 'EXPECTED_PROOF_SCHEDULE_MODE=always_on_shadow' '$SHARED/.env'
    grep -qx 'HTF_DIRECTION_MODE=off' '$SHARED/.env'
    grep -qx 'EXPECTED_PROOF_HTF_DIRECTION_MODE=off' '$SHARED/.env'
    grep -qx 'EXIT_MODE=static' '$SHARED/.env'
    grep -qx 'EXPECTED_PROOF_EXIT_MODE=static' '$SHARED/.env'
    fp=\$('$RELEASES/$sha/.venv/bin/python' -c \"import json;print(json.load(open('$RELEASES/$sha/release_manifest.json'))['fingerprint_sha256'])\")
    risk_sha=\$('$RELEASES/$sha/.venv/bin/python' -c \"import json;print(json.load(open('$RELEASES/$sha/release_manifest.json'))['risk_rules_sha256'])\")
    sed -i '/^EXPECTED_RELEASE_FINGERPRINT=/d;/^EXPECTED_LIVE_BRANCH=/d;/^EXPECTED_LIVE_COMMIT=/d;/^EXPECTED_RISK_RULES_SHA256=/d' '$SHARED/.env'
    printf 'EXPECTED_RELEASE_FINGERPRINT=%s\nEXPECTED_LIVE_BRANCH=main\nEXPECTED_LIVE_COMMIT=%s\nEXPECTED_RISK_RULES_SHA256=%s\n' \
      \"\$fp\" '$sha' \"\$risk_sha\" >> '$SHARED/.env'
    old=\$(readlink '$CURRENT' 2>/dev/null || true)
    [ -z \"\$old\" ] || printf '%s\n' \"\$old\" > '$SHARED/current.previous'
    ln -s '$RELEASES/$sha' '$CURRENT.next'
    mv -Tf '$CURRENT.next' '$CURRENT'
    mkdir -p /etc/systemd/system/'$SERVICE'.service.d
    printf '%s\n' \
      '[Service]' \
      'WorkingDirectory=$CURRENT' \
      'EnvironmentFile=' \
      'EnvironmentFile=$SHARED/.env' \
      'Environment=PYTHONDONTWRITEBYTECODE=1' \
      'Environment=PYTHONPATH=$CURRENT' \
      'Environment=LOG_DIR=$SHARED/logs' \
      'ExecStart=' \
      'ExecStart=$CURRENT/.venv/bin/python -m uvicorn webhook.app:app --host 127.0.0.1 --port 8000' \
      > /etc/systemd/system/'$SERVICE'.service.d/atomic-release.conf
    systemctl daemon-reload
    systemctl restart '$SERVICE'
    sleep 6
    systemctl is-active '$SERVICE'
    curl -fsS http://127.0.0.1:8000/health
    PYTHONPATH='$CURRENT' '$CURRENT/.venv/bin/python' \
      -m ops.release_integrity --repo-root '$CURRENT'
  "
}

rollback_release() {
  deploy_lock_acquire "$LOCK_DIR" "rollback" "$0" "$FORCE_LOCK" || exit 1
  trap "deploy_lock_release '$LOCK_DIR' '$DEPLOY_LOCK_OWNER'" EXIT

  remote "
    set -e
    previous=\$(cat '$SHARED/current.previous')
    test -d \"\$previous\"
    current=\$(readlink '$CURRENT')
    ln -s \"\$previous\" '$CURRENT.next'
    mv -Tf '$CURRENT.next' '$CURRENT'
    printf '%s\n' \"\$current\" > '$SHARED/current.previous'
    systemctl restart '$SERVICE'
    sleep 6
    systemctl is-active '$SERVICE'
    curl -fsS http://127.0.0.1:8000/health
  "
}

case "$ACTION" in
  build) build_release ;;
  verify) verify_release ;;
  promote) promote_release ;;
  rollback) rollback_release ;;
  *)
    echo "usage: $0 {build [ref]|verify <sha>|promote <sha>|rollback} [--force-lock]" >&2
    exit 64
    ;;
esac
