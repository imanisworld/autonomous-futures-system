#!/usr/bin/env bash
# Versioned immutable release builder/promoter for AFS.
#
# Host identity stays outside the repository:
#   AFS_BOX=root@example AFS_SHARED_DIR=/root/afs-shared \
#     scripts/atomic_release.sh build <sha>
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
REF="${2:-}"
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

_require_exact_sha() {
  local value="${1:-}"
  if [[ ! "$value" =~ ^[0-9a-f]{40}$ ]]; then
    echo "release action requires an exact 40-character lowercase commit SHA" >&2
    return 1
  fi
}

build_release() {
  deploy_lock_acquire "$LOCK_DIR" "build $REF" "$0" "$FORCE_LOCK" || exit 1
  trap "deploy_lock_release '$LOCK_DIR' '$DEPLOY_LOCK_OWNER'" EXIT

  git fetch -q origin
  local sha short work archive manifest
  sha="$(git rev-parse "$REF^{commit}")"
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

  local sha="$REF" short="${REF:0:12}" unit="afs-candidate-${REF:0:12}" fingerprint port candidate_overrides posture_label
  if [[ "${AFS_VERIFY_POSTURE:-shadow_baseline}" == "preserve_current" ]]; then
    candidate_overrides=""
    posture_label="current strategy/exit pins with paper-isolated broker"
  else
    candidate_overrides="--setenv=SCHEDULE_MODE=always_on_shadow --setenv=EXPECTED_PROOF_SCHEDULE_MODE=always_on_shadow --setenv=HTF_DIRECTION_MODE=off --setenv=EXPECTED_PROOF_HTF_DIRECTION_MODE=off --setenv=EXIT_MODE=static --setenv=EXPECTED_PROOF_EXIT_MODE=static"
    posture_label="always_on_shadow/static with paper-isolated broker"
  fi
  fingerprint="$(remote "'$RELEASES/$sha/.venv/bin/python' -c \"import json;print(json.load(open('$RELEASES/$sha/release_manifest.json'))['fingerprint_sha256'])\"")"
  port="$(remote "python3 -c 'import socket; s=socket.socket(); s.bind((\"127.0.0.1\", 0)); print(s.getsockname()[1]); s.close()'")"
  remote "
    set -e
    test -d '$RELEASES/$sha'
    test -f '$SHARED/.env'
    candidate_env='$SHARED/candidate-env-$sha'
    cleanup_candidate() {
      systemctl stop '$unit' >/dev/null 2>&1 || true
      rm -f \"\$candidate_env\"
    }
    trap cleanup_candidate EXIT
    grep -Ev '^(EXPECTED_RELEASE_FINGERPRINT|BROKER)=' '$SHARED/.env' > \"\$candidate_env\"
    printf 'EXPECTED_RELEASE_FINGERPRINT=%s\nBROKER=paper\n' '$fingerprint' >> \"\$candidate_env\"
    systemctl stop '$unit' 2>/dev/null || true
    systemd-run --unit='$unit' \
      --property=WorkingDirectory='$RELEASES/$sha' \
      --property=EnvironmentFile=\"\$candidate_env\" \
      --setenv=PYTHONDONTWRITEBYTECODE=1 \
      --setenv=PYTHONPATH='$RELEASES/$sha' \
      --setenv=PORT='$port' \
      --setenv=LOG_DIR='$SHARED/candidate-logs/$sha' \
      $candidate_overrides \
      --setenv=RELEASE_INTEGRITY_ENFORCED=true \
      '$RELEASES/$sha/.venv/bin/python' -m uvicorn webhook.app:app \
        --host 127.0.0.1 --port '$port'
    for i in 1 2 3 4 5 6 7 8 9 10; do
      sleep 2
      curl -fsS http://127.0.0.1:'$port'/health >/dev/null && break
    done
    curl -fsS http://127.0.0.1:'$port'/health
    curl -fsS http://127.0.0.1:'$port'/status/tradovate-reliability >/dev/null
    PYTHONPATH='$RELEASES/$sha' '$RELEASES/$sha/.venv/bin/python' \
      -m ops.release_integrity --repo-root '$RELEASES/$sha'
  "
  echo "candidate $short verified on 127.0.0.1:$port with $posture_label"
}

# Decides whether $1 (a release sha) may be promoted, given the box's
# current posture and, if on the operational posture, a behavior-neutral
# diff against the live commit. Returns 0 to proceed, 1 to refuse (printing
# why on stderr). Deliberately isolated from the symlink-swap/restart
# mechanics below (no `exit`, only `return`, and it never touches $SHARED/
# .env or $CURRENT) so it can be exercised directly in tests against a fake
# box/git repo without a real systemd service.
_promote_gate_check() {
  local sha="$1"

  # Path 1: box is on the fully-reset baseline posture. Any release may
  # promote here -- this is the route for actual strategy/risk/exit changes,
  # always starting from a known-safe, no-live-orders state.
  local on_baseline=0
  remote "
    grep -qx 'SCHEDULE_MODE=always_on_shadow' '$SHARED/.env' &&
    grep -qx 'EXPECTED_PROOF_SCHEDULE_MODE=always_on_shadow' '$SHARED/.env' &&
    grep -qx 'HTF_DIRECTION_MODE=off' '$SHARED/.env' &&
    grep -qx 'EXPECTED_PROOF_HTF_DIRECTION_MODE=off' '$SHARED/.env' &&
    grep -qx 'EXIT_MODE=static' '$SHARED/.env' &&
    grep -qx 'EXPECTED_PROOF_EXIT_MODE=static' '$SHARED/.env'
  " >/dev/null 2>&1 || on_baseline=1

  if [[ "$on_baseline" -eq 0 ]]; then
    return 0
  fi

  # Path 2: box is on the approved operational posture (demo execution +
  # runner-shadow exits, mid a strategy-observation window). Promotion is
  # allowed WITHOUT resetting those pins only if (a) the posture is exactly
  # the one currently approved, and (b) the candidate's diff against the
  # live commit is behavior-neutral (an explicit reviewed file allowlist,
  # never whole scripts/ or ops/ -- see ops/behavior_neutral_gate.py). This
  # lets operational/observability fixes ship without interrupting a live
  # strategy-observation sample; it never bypasses the check silently.
  local on_current=0
  remote "
    grep -qx 'SCHEDULE_MODE=current' '$SHARED/.env' &&
    grep -qx 'EXPECTED_PROOF_SCHEDULE_MODE=current' '$SHARED/.env' &&
    grep -qx 'HTF_DIRECTION_MODE=off' '$SHARED/.env' &&
    grep -qx 'EXPECTED_PROOF_HTF_DIRECTION_MODE=off' '$SHARED/.env' &&
    grep -qx 'EXIT_MODE=runner_shadow' '$SHARED/.env' &&
    grep -qx 'EXPECTED_PROOF_EXIT_MODE=runner_shadow' '$SHARED/.env'
  " >/dev/null 2>&1 || on_current=1

  if [[ "$on_current" -ne 0 ]]; then
    echo "promotion refused: box posture matches neither the reset baseline (always_on_shadow/static) nor the approved operational posture (current/runner_shadow) -- resolve drift before promoting" >&2
    return 1
  fi

  local live_sha
  live_sha="$(remote "grep '^EXPECTED_LIVE_COMMIT=' '$SHARED/.env' | tail -1 | cut -d= -f2" || true)"
  if [[ -z "$live_sha" ]]; then
    echo "promotion refused: cannot determine the currently-live commit (EXPECTED_LIVE_COMMIT unset in $SHARED/.env) to run the behavior-neutral check" >&2
    return 1
  fi

  git -C "$ROOT" fetch -q origin
  if ! python3 -m ops.behavior_neutral_gate --repo-root "$ROOT" --baseline-sha "$live_sha" --candidate-sha "$sha"; then
    echo "promotion refused: candidate is not behavior-neutral relative to the live commit ($live_sha) -- box stays on the approved operational posture (not the reset baseline), so only operational-only changes may promote without a full strategy-observation reset" >&2
    return 1
  fi
  echo "behavior-neutral check passed against live commit $live_sha -- promoting under the current operational posture, no pin reset"
  return 0
}

promote_release() {
  deploy_lock_acquire "$LOCK_DIR" "promote $REF" "$0" "$FORCE_LOCK" || exit 1
  trap "deploy_lock_release '$LOCK_DIR' '$DEPLOY_LOCK_OWNER'" EXIT

  local sha="$REF"
  _promote_gate_check "$sha" || exit 1

  remote "
    set -e
    test -d '$RELEASES/$sha'
    test -f '$SHARED/.env'
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
      'Environment=RELEASE_INTEGRITY_ENFORCED=true' \
      'ExecStart=' \
      'ExecStart=$CURRENT/.venv/bin/python -m uvicorn webhook.app:app --host 127.0.0.1 --port 8000' \
      > /etc/systemd/system/'$SERVICE'.service.d/atomic-release.conf
    systemctl daemon-reload
    systemctl restart '$SERVICE'
    sleep 6
    systemctl is-active '$SERVICE'
    pid=\$(systemctl show '$SERVICE' -p MainPID --value)
    expected_cwd=\$(readlink -f '$CURRENT')
    actual_cwd=\$(readlink -f "/proc/\$pid/cwd")
    test "\$actual_cwd" = "\$expected_cwd" || {
      echo "release activation mismatch: service cwd=\$actual_cwd expected=\$expected_cwd" >&2
      exit 1
    }
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
    manifest=\"\$previous/release_manifest.json\"
    test -f \"\$manifest\"
    prev_fp=\$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[\"fingerprint_sha256\"])' \"\$manifest\")
    prev_commit=\$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[\"repo\"][\"commit\"])' \"\$manifest\")
    prev_risk=\$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[\"risk_rules_sha256\"])' \"\$manifest\")
    sed -i '/^EXPECTED_RELEASE_FINGERPRINT=/d;/^EXPECTED_LIVE_BRANCH=/d;/^EXPECTED_LIVE_COMMIT=/d;/^EXPECTED_RISK_RULES_SHA256=/d' '$SHARED/.env'
    printf 'EXPECTED_RELEASE_FINGERPRINT=%s\nEXPECTED_LIVE_BRANCH=main\nEXPECTED_LIVE_COMMIT=%s\nEXPECTED_RISK_RULES_SHA256=%s\n' \
      \"\$prev_fp\" \"\$prev_commit\" \"\$prev_risk\" >> '$SHARED/.env'
    current=\$(readlink '$CURRENT')
    ln -s \"\$previous\" '$CURRENT.next'
    mv -Tf '$CURRENT.next' '$CURRENT'
    printf '%s\n' \"\$current\" > '$SHARED/current.previous'
    systemctl restart '$SERVICE'
    sleep 6
    systemctl is-active '$SERVICE'
    pid=\$(systemctl show '$SERVICE' -p MainPID --value)
    expected_cwd=\$(readlink -f '$CURRENT')
    actual_cwd=\$(readlink -f "/proc/\$pid/cwd")
    test "\$actual_cwd" = "\$expected_cwd" || {
      echo "rollback activation mismatch: service cwd=\$actual_cwd expected=\$expected_cwd" >&2
      exit 1
    }
    curl -fsS http://127.0.0.1:8000/health
    PYTHONPATH='$CURRENT' '$CURRENT/.venv/bin/python' \
      -m ops.release_integrity --repo-root '$CURRENT'
  "
}

# Guarded so tests can `source` this file (e.g. to call _promote_gate_check
# directly against a fake box/git repo) without triggering a real action.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  case "$ACTION" in
    build)
      _require_exact_sha "$REF" || exit 64
      build_release ;;
    verify)
      _require_exact_sha "$REF" || exit 64
      verify_release ;;
    promote)
      _require_exact_sha "$REF" || exit 64
      promote_release ;;
    rollback)
      if [[ -n "$REF" && "$REF" != "--force-lock" ]]; then
        echo "rollback takes no release ref" >&2
        exit 64
      fi
      rollback_release ;;
    *)
      echo "usage: $0 {build <sha>|verify <sha>|promote <sha>|rollback} [--force-lock]" >&2
      exit 64
      ;;
  esac
fi
