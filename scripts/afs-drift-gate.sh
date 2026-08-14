#!/usr/bin/env bash
# scripts/afs-drift-gate.sh
#
# Read-only drift gate: compares the watched source paths at a git ref (main)
# against the copy actually running on the box, and alerts on anything that is
# merged-but-unshipped (MISSING), edited on the box (DIFFER/EXTRA), or shipped
# from a stale release.
#
# This gate answers one question only: "is the box running what main says it
# should be running?" It never deploys, never restarts, and never writes to the
# box outside its own seed file. Promotion is scripts/atomic_release.sh.
#
# Host identity stays outside the repository:
#   AFS_BOX=root@example scripts/afs-drift-gate.sh
#   AFS_BOX=root@example scripts/afs-drift-gate.sh --seed
#
# Environment:
#   AFS_BOX          (required) ssh target for the box
#   AFS_BOX_ROOT     deployed tree to hash        (default /root/autonomous-futures-system)
#   AFS_SHARED_DIR   where the seed baseline lives (default /root/afs-shared)
#   AFS_DRIFT_REF    ref treated as truth          (default origin/main)
#   AFS_DRIFT_PATHS  space-separated watch roots   (default "alert_ranker ops/project_check")
#   DISCORD_ROUTE_DEPLOYMENT / DISCORD_WEBHOOK_URL  alert sink; unset -> print only
#
# Seeding: --seed records the CURRENT drift set as accepted, so those exact
# items stop alerting. Every recorded DIFFER pins both hashes, so a later edit
# on either side surfaces again rather than hiding under the old acceptance.
# Seed only after reviewing why the box is allowed to differ -- seeding a box
# that is simply behind main permanently hides the fact that it is behind.
#
# Exit codes: 0 = no unexpected drift (or seeded), 1 = unexpected drift, 64 = usage.
set -euo pipefail

SEED=0
QUIET=0
for arg in "$@"; do
  case "$arg" in
    --seed) SEED=1 ;;
    --quiet) QUIET=1 ;;
    -h|--help)
      echo "usage: $0 [--seed] [--quiet]" >&2
      exit 64
      ;;
    *)
      echo "usage: $0 [--seed] [--quiet]" >&2
      exit 64
      ;;
  esac
done

BOX="${AFS_BOX:?set AFS_BOX (for example root@host)}"
BOX_ROOT="${AFS_BOX_ROOT:-/root/autonomous-futures-system}"
SHARED="${AFS_SHARED_DIR:-/root/afs-shared}"
REF="${AFS_DRIFT_REF:-origin/main}"
PATHS="${AFS_DRIFT_PATHS:-alert_ranker ops/project_check}"
SEED_FILE="$SHARED/drift-seed.txt"

remote() {
  ssh -o BatchMode=yes -o ConnectTimeout=20 "$BOX" "$@"
}
REMOTE_EXEC="${REMOTE_EXEC:-remote}"

# "<path> <md5>" for every tracked .py under the watch roots at $ref.
drift_main_hashes() {
  local ref="$1" paths="$2" f h
  while IFS= read -r f; do
    [[ "$f" == *.py ]] || continue
    h="$(git show "$ref:$f" | md5sum | cut -d' ' -f1)"
    printf '%s %s\n' "$f" "$h"
  done < <(git ls-tree -r --name-only "$ref" -- $paths)
}

# "<path> <md5>" for every .py under the watch roots on the box. A watch root
# that is absent on the box yields nothing and surfaces as MISSING below --
# that is the merged-but-unshipped case, not an error, so find/md5sum failures
# must not abort the run under `set -e`/pipefail.
drift_box_hashes() {
  local root="$1" paths="$2"
  "$REMOTE_EXEC" "cd '$root' && { find $paths -type f -name '*.py' -not -path '*/__pycache__/*' -print0 2>/dev/null | xargs -0 -r md5sum 2>/dev/null; true; }" \
    | awk 'NF >= 2 { h = $1; $1 = ""; sub(/^ +/, ""); print $0 " " h }'
}

# Emits one line per drift item:
#   DIFFER <path> <main_md5> <box_md5>   content differs
#   MISSING <path>                       in main, not on the box
#   EXTRA <path>                         on the box, not in main
#
# Output is whole-line LC_ALL=C sorted because the seed subtraction is a `comm`
# set difference, which silently misbehaves on any other ordering. Sorting for
# human display (by path, so a file's status changes are easy to scan) happens
# at the point of display instead.
drift_compare() {
  local main_list="$1" box_list="$2"
  awk '
    NR == FNR { main[$1] = $2; next }
    {
      box[$1] = $2
      if (!($1 in main)) print "EXTRA " $1
      else if (main[$1] != $2) print "DIFFER " $1 " " main[$1] " " $2
    }
    END { for (p in main) if (!(p in box)) print "MISSING " p }
  ' "$main_list" "$box_list" | LC_ALL=C sort
}

drift_report() {
  local work main_list box_list all_list unexpected
  work="$(mktemp -d "${TMPDIR:-/tmp}/afs-drift.XXXXXX")"
  trap "rm -rf '$work'" RETURN
  main_list="$work/main" box_list="$work/box" all_list="$work/all"

  # An unreachable box (ssh down, wrong AFS_BOX_ROOT) would otherwise hash to
  # an empty set and read as "every watched file is MISSING" -- a false deploy
  # alarm. Refuse instead, so the gate is never silently blind.
  if ! "$REMOTE_EXEC" "test -d '$BOX_ROOT'" >/dev/null 2>&1; then
    echo "✗ box tree not readable ($BOX:$BOX_ROOT) -- refusing to report drift against an unreachable box" >&2
    return 2
  fi

  drift_main_hashes "$REF" "$PATHS" | sort > "$main_list"
  drift_box_hashes "$BOX_ROOT" "$PATHS" | sort > "$box_list"

  if [[ ! -s "$main_list" ]]; then
    echo "✗ no tracked .py files under AFS_DRIFT_PATHS ($PATHS) at $REF -- refusing to report a vacuously clean box" >&2
    return 2
  fi

  drift_compare "$main_list" "$box_list" > "$all_list"

  if [[ "$SEED" -eq 1 ]]; then
    "$REMOTE_EXEC" "mkdir -p '$SHARED'"
    "$REMOTE_EXEC" "cat > '$SEED_FILE'" < "$all_list"
    echo "seeded $(wc -l < "$all_list" | tr -d ' ') accepted drift item(s) to $BOX:$SEED_FILE"
    return 0
  fi

  # Accepted drift is subtracted by exact line, so a re-edit of an already
  # accepted file changes its hash pair and alerts again.
  unexpected="$work/unexpected"
  "$REMOTE_EXEC" "cat '$SEED_FILE' 2>/dev/null || true" 2>/dev/null \
    | LC_ALL=C sort > "$work/seed" || true
  LC_ALL=C comm -23 "$all_list" "$work/seed" | sort -k2,2 > "$unexpected"

  local count
  count="$(wc -l < "$unexpected" | tr -d ' ')"
  if [[ "$count" -eq 0 ]]; then
    [[ "$QUIET" -eq 1 ]] || echo "✓ AFS drift gate: box matches $REF across $PATHS"
    return 0
  fi

  local header
  header="🚨 **AFS drift gate: $count UNEXPECTED drift item(s)** (main vs box — merged-but-unshipped or box edits). Deploy, or re-seed after review: \`afs-drift-gate.sh --seed\`"
  printf '%s\n' "$header"
  cat "$unexpected"
  drift_post_discord "$header" "$unexpected"
  return 1
}

# Discord caps a message at 2000 characters, so a wide drift (the exact case
# this gate exists to catch) would 400 and silently send nothing. Truncate the
# body and say how many items were withheld -- the full list is always on stdout.
drift_post_discord() {
  local header="$1" body_file="$2" url
  url="${DISCORD_ROUTE_DEPLOYMENT:-${DISCORD_WEBHOOK_URL:-}}"
  if [[ -z "$url" ]]; then
    return 0
  fi
  python3 - "$header" "$body_file" "$url" <<'PY'
import json, sys, urllib.request

header, body_file, url = sys.argv[1], sys.argv[2], sys.argv[3]
lines = open(body_file).read().splitlines()

LIMIT = 1900
content = header
shown = 0
for line in lines:
    if len(content) + len(line) + 1 > LIMIT:
        break
    content += "\n" + line
    shown += 1
if shown < len(lines):
    content += f"\n… {len(lines) - shown} more (see gate output)"

request = urllib.request.Request(
    url,
    data=json.dumps({"content": content}).encode(),
    headers={"Content-Type": "application/json"},
)
try:
    urllib.request.urlopen(request, timeout=20)
except Exception as exc:  # never let a webhook failure mask the drift verdict
    print(f"WARN drift alert not delivered: {exc}", file=sys.stderr)
PY
}

# Guarded so tests can `source` this file to exercise drift_compare and the
# hashing helpers against a fake box without opening an ssh connection.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  git fetch -q origin || true
  drift_report
fi
