#!/usr/bin/env bash
# Server-side AFS drift gate for curated immutable releases.
#
# RED/exit 1 means the live tree no longer matches its own release manifest or
# durable release pins. A newer repository main is NOT runtime drift; it is
# reported separately as informational release lag.
#
# Read-only: never deploys, restarts, promotes, seeds, or edits a release.
set -euo pipefail

QUIET=0
for arg in "$@"; do
  case "$arg" in
    --quiet) QUIET=1 ;;
    --seed)
      echo "REFUSED: --seed is unsupported; fix/redeploy the curated release instead." >&2
      exit 64
      ;;
    -h|--help)
      echo "usage: $0 [--quiet]" >&2
      exit 0
      ;;
    *)
      echo "usage: $0 [--quiet]" >&2
      exit 64
      ;;
  esac
done

LIVE="${AFS_LIVE_ROOT:-/root/autonomous-futures-system}"
SHARED="${AFS_SHARED_DIR:-/root/afs-shared}"
ENV_FILE="${AFS_ENV_FILE:-$SHARED/.env}"
LOG="${AFS_DRIFT_LOG:-/root/afs-drift-gate.log}"
REPO_URL="${AFS_REPO_URL:-https://github.com/imanisworld/autonomous-futures-system.git}"
PYTHON="${AFS_PYTHON:-$LIVE/.venv/bin/python}"
INFO_MAIN="${AFS_DRIFT_MAIN_INFO:-1}"

log_line() {
  local message="$1"
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$message" | tee -a "$LOG"
}

env_value() {
  local key="$1"
  [[ -f "$ENV_FILE" ]] || return 1
  python3 - "$ENV_FILE" "$key" <<'PY'
from pathlib import Path
import sys
path, key = Path(sys.argv[1]), sys.argv[2]
for raw in path.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    name, value = line.split("=", 1)
    if name.strip() == key:
        print(value.strip().strip('"').strip("'"))
        raise SystemExit(0)
raise SystemExit(1)
PY
}

manifest_value() {
  local key="$1"
  python3 - "$LIVE/release_manifest.json" "$key" <<'PY'
import json, sys
path, key = sys.argv[1], sys.argv[2]
try:
    data = json.load(open(path, encoding="utf-8"))
except Exception:
    raise SystemExit(2)
if key == "commit":
    print((data.get("repo") or {}).get("commit") or "")
elif key == "fingerprint":
    print(data.get("fingerprint_sha256") or "")
else:
    raise SystemExit(2)
PY
}

release_integrity_check() {
  PYTHONPATH="$LIVE" "$PYTHON" -m ops.release_integrity --repo-root "$LIVE"
}

discord_hook() {
  env_value DISCORD_ROUTE_DEPLOYMENT 2>/dev/null \
    || env_value DISCORD_ROUTE_ERROR 2>/dev/null \
    || env_value DISCORD_WEBHOOK_URL 2>/dev/null \
    || true
}

post_red_alert() {
  local title="$1" body="$2" hook payload
  hook="$(discord_hook)"
  [[ -n "$hook" ]] || return 0
  payload="$(printf '%s\n%s' "$title" "$body" \
    | python3 -c 'import json,sys; print(json.dumps({"content": sys.stdin.read()[:1900]}))')"
  curl -sS -f -X POST -H 'Content-Type: application/json' -d "$payload" "$hook" >/dev/null \
    || log_line "WARN drift alert delivery failed"
}

file_filter() {
  grep -E '\.(py|ya?ml|json|html|j2)$|(^|/)requirements[^/]*\.txt$' \
    | grep -Ev '^(tests|scripts|docs|\.github|interactive-course|data)/' \
    | grep -Ev 'fixture' | LC_ALL=C sort
}

tree_hashes() {
  local root="$1" list="$2" out="$3" f
  : > "$out"
  while IFS= read -r f; do
    [[ -f "$root/$f" ]] || continue
    printf '%s %s\n' "$f" "$(md5sum "$root/$f" | awk '{print $1}')" >> "$out"
  done < "$list"
}

main_ahead_report() {
  local tmp main_files main_hash live_hash drift count
  tmp="$(mktemp -d /tmp/afs_main_ahead.XXXXXX)"

  if ! git clone -q --depth 1 "$REPO_URL" "$tmp/main" 2>>"$LOG"; then
    log_line "WARN main-ahead comparison unavailable: clone failed"
    rm -rf "$tmp"
    return 0
  fi
  main_files="$tmp/main_files"
  git -C "$tmp/main" ls-tree -r --name-only HEAD | file_filter > "$main_files"
  main_hash="$tmp/main_hash"
  live_hash="$tmp/live_hash"
  drift="$tmp/drift"

  tree_hashes "$tmp/main" "$main_files" "$main_hash"
  tree_hashes "$LIVE" "$main_files" "$live_hash"

  awk '
    NR == FNR { main[$1] = $2; next }
    { live[$1] = $2 }
    END {
      for (p in main) {
        if (!(p in live)) print "MISSING " p
        else if (main[p] != live[p]) print "DIFFER " p " " main[p] " " live[p]
      }
    }
  ' "$main_hash" "$live_hash" | LC_ALL=C sort -k2,2 > "$drift"

  count="$(wc -l < "$drift" | tr -d ' ')"
  if [[ "$count" -eq 0 ]]; then
    [[ "$QUIET" -eq 1 ]] || log_line "INFO main-ahead: live release matches current main for compared runtime files"
    rm -rf "$tmp"
    return 0
  fi

  log_line "INFO main-ahead: $count merged-but-unshipped runtime item(s); informational only, release integrity is authoritative"
  if [[ "$QUIET" -ne 1 ]]; then
    sed -n '1,20p' "$drift"
    if [[ "$count" -gt 20 ]]; then
      echo "... $((count - 20)) more"
    fi
  fi
  rm -rf "$tmp"
}

run_gate() {
  local integrity expected_commit manifest_commit expected_fp manifest_fp body

  if [[ ! -d "$LIVE" || ! -f "$LIVE/release_manifest.json" ]]; then
    body="live release tree/manifest unavailable: $LIVE"
    log_line "ALARM release-drift: $body"
    post_red_alert "🚨 **AFS release drift: integrity proof unavailable**" "$body"
    return 1
  fi

  if ! integrity="$(release_integrity_check 2>&1)"; then
    body="$integrity"
    log_line "ALARM release-drift: release integrity FAILED"
    printf '%s\n' "$body"
    post_red_alert "🚨 **AFS release drift: manifest integrity FAILED**" "$body"
    return 1
  fi

  expected_commit="$(env_value EXPECTED_LIVE_COMMIT 2>/dev/null || true)"
  manifest_commit="$(manifest_value commit 2>/dev/null || true)"
  if [[ -z "$expected_commit" || -z "$manifest_commit" || "$expected_commit" != "$manifest_commit" ]]; then
    body="EXPECTED_LIVE_COMMIT=${expected_commit:-<missing>} manifest_commit=${manifest_commit:-<missing>}"
    log_line "ALARM release-drift: pinned commit mismatch"
    post_red_alert "🚨 **AFS release drift: pinned commit mismatch**" "$body"
    return 1
  fi
  expected_fp="$(env_value EXPECTED_RELEASE_FINGERPRINT 2>/dev/null || true)"
  manifest_fp="$(manifest_value fingerprint 2>/dev/null || true)"
  if [[ -n "$expected_fp" && "$expected_fp" != "$manifest_fp" ]]; then
    body="EXPECTED_RELEASE_FINGERPRINT=$expected_fp manifest_fingerprint=${manifest_fp:-<missing>}"
    log_line "ALARM release-drift: pinned fingerprint mismatch"
    post_red_alert "🚨 **AFS release drift: release fingerprint mismatch**" "$body"
    return 1
  fi

  [[ "$QUIET" -eq 1 ]] || log_line "OK release-integrity: ${manifest_commit:0:12} matches manifest and durable pins"

  if [[ "$INFO_MAIN" == "1" ]]; then
    main_ahead_report
  fi
  return 0
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  run_gate
fi
