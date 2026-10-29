#!/bin/bash
# Fix A/B harness. Extracts the v11 lib and stubs host commands. No VPS.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
BOOK="$ROOT/futures_release_recut_and_os_maintenance_RUNBOOK_DRAFT.v11.md"
BASE=$(mktemp -d)
LIB="$BASE/lib.sh"
BIN="$BASE/bin"
CALLLOG="$BASE/calls.txt"
NRFILE="$BASE/nr"
mkdir -p "$BIN"
: > "$CALLLOG"
echo 0 > "$NRFILE"

awk '
$0=="```bash" { inb=1; buf=""; next }
inb && $0=="```" {
  if (index(buf, "/root/afs-maint-lib.sh") > 0) { printf "%s", buf; exit }
  inb=0; next
}
inb { buf = buf $0 "\n" }
' "$BOOK" > "$LIB"
bash -n "$LIB" || { echo "FAIL: extracted lib syntax"; exit 1; }

cat > "$BIN/sleep" << 'EOF'
#!/bin/bash
exit 0
EOF
cat > "$BIN/stat" << 'EOF'
#!/bin/bash
echo 1700000000
EOF
cat > "$BIN/curl" << 'EOF'
#!/bin/bash
exit 0
EOF
cat > "$BIN/sha256sum" << 'EOF'
#!/bin/bash
for a in "$@"; do echo "abc  $a"; done
EOF
cat > "$BIN/journalctl" << 'EOF'
#!/bin/bash
case "${JMODE:-clean}" in
  clean) printf '%s\n' '-- No entries --'; exit 0 ;;
  err) printf '%s\n' 'Oct 29 21:10:01 h python[1]: Traceback boom'; exit 0 ;;
  fail) exit 1 ;;
esac
exit 0
EOF
cat > "$BIN/readlink" << 'EOF'
#!/bin/bash
if [ "$1" = "-f" ]; then echo "$OLD"; exit 0; fi
echo "$NEW"
EOF
cat > "$BIN/ln" << 'EOF'
#!/bin/bash
echo "LN $*" >> "$CALLLOG"
exit 0
EOF
cat > "$BIN/mv" << 'EOF'
#!/bin/bash
echo "MV $*" >> "$CALLLOG"
exit 0
EOF
cat > "$BIN/systemctl" << 'EOF'
#!/bin/bash
echo "SC $*" >> "$CALLLOG"
if [ "$1" = is-active ]; then echo active; exit 0; fi
if [ "$1" = restart ]; then echo "RESTART" >> "$CALLLOG"; exit 0; fi
case "$*" in
  *NRestarts*)
    if [ "${MODE:-stable}" = flap ]; then
      n=$(cat "$NRFILE")
      n=$((n+1))
      echo "$n" > "$NRFILE"
      echo "$n"
    else
      echo 0
    fi
    ;;
  *MainPID*) echo 4242 ;;
  *) ;;
esac
exit 0
EOF
chmod 755 "$BIN/sleep" "$BIN/stat" "$BIN/curl" "$BIN/sha256sum" "$BIN/journalctl" "$BIN/readlink" "$BIN/ln" "$BIN/mv" "$BIN/systemctl"

export PATH="$BIN:$PATH"
export CALLLOG NRFILE
export JMODE=clean
export MODE=stable

# shellcheck disable=SC1090
. "$LIB"
ORIG_SWITCH_BACK=$(declare -f _switch_back)
W_START=200001010000
LATE_ABORT=299901010000
CUT_STOP=299901010000
SWITCH_STOP=299901010000
RB_STOP=299901010000

fail_n=0
assert_out(){
  local name=$1 needle=$2 text=$3
  if printf '%s' "$text" | grep -q "$needle"; then
    echo "PASS $name"
  else
    echo "FAIL $name missing [$needle]"
    printf '%s\n' "$text" | head -n 40 >&2
    fail_n=1
  fi
}
assert_absent(){
  local name=$1 needle=$2 text=$3
  if printf '%s' "$text" | grep -q "$needle"; then
    echo "FAIL $name unexpectedly saw [$needle]"
    fail_n=1
  else
    echo "PASS $name"
  fi
}

prep_env(){
  WORK="$BASE/work-$1"
  rm -rf "$WORK"
  mkdir -p "$WORK/ev" "$WORK/shared" "$WORK/new/context" "$WORK/old"
  echo paper > "$WORK/new/context/wide_stop_ledger_paper.py"
  echo exec > "$WORK/new/context/wide_stop_execution.py"
  echo '{}' > "$WORK/old/release_manifest.json"
  EV="$WORK/ev"
  SHARED="$WORK/shared"
  NEW="$WORK/new"
  OLD="$WORK/old"
  LINK="$WORK/link"
  /bin/ln -sfn "$OLD" "$LINK"
  SWITCH_MARKER="$WORK/marker"
  touch "$SWITCH_MARKER"
  STATE="$WORK/state.env"
  STAMP=testharness
  LOCK_OWNER=maint-recut-testharness
  CUT_OK=1
  ROLLBACK="$WORK/rb"
  rm -rf "$ROLLBACK"
  mkdir -p "$ROLLBACK"
  echo '{}' > "$ROLLBACK/release_manifest.json"
  chmod -R a-w "$ROLLBACK"
  printf '%s\n' "STAMP=$STAMP" "EV=$EV" "NEW=$NEW" "OLD=$OLD" "ROLLBACK=$ROLLBACK" "LOCK_OWNER=$LOCK_OWNER" "CUT_OK=1" > "$STATE"
  chmod 600 "$STATE"
  : > "$EV/pins_pre.txt"
  sha256sum "$NEW/context/wide_stop_ledger_paper.py" "$NEW/context/wide_stop_execution.py" | awk '{print $1}' > "$EV/cap_files_sha_pre.txt"
  sha256sum /etc/systemd/system/futures-bot.service.d/*.conf > "$EV/dropins_sha_pre.txt"
  : > "$CALLLOG"
  echo 0 > "$NRFILE"
  export EV SHARED NEW OLD LINK SWITCH_MARKER STATE STAMP LOCK_OWNER CUT_OK ROLLBACK
  export JMODE MODE
}

integrity_ok(){ echo INTEGRITY_OK; return 0; }
flat_check(){ return 0; }
flat_retry(){ return 0; }
pins_snapshot(){ :; }

# --- rollback_ok ---
prep_env rb
out=$(rollback_ok "$ROLLBACK" 2>&1); rc=$?
assert_out "rollback_ok clean" "ROLLBACK_TARGET_OK" "$out"
[ "$rc" -eq 0 ] || { echo "FAIL rollback_ok clean rc=$rc"; fail_n=1; }

prep_env pyc
chmod -R u+w "$ROLLBACK"
mkdir -p "$ROLLBACK/x/__pycache__"
echo pyc > "$ROLLBACK/x/__pycache__/a.pyc"
chmod -R a-w "$ROLLBACK"
out=$(rollback_ok "$ROLLBACK" 2>&1); rc=$?
assert_out "rollback_ok pycache" "has pycache" "$out"
[ "$rc" -ne 0 ] || { echo "FAIL rollback_ok pycache rc=0"; fail_n=1; }

prep_env rw
chmod -R u+w "$ROLLBACK"
echo z > "$ROLLBACK/file"
chmod -R a-w "$ROLLBACK"
chmod u+w "$ROLLBACK/file"
out=$(rollback_ok "$ROLLBACK" 2>&1); rc=$?
assert_out "rollback_ok writable" "not read-only" "$out"
[ "$rc" -ne 0 ] || { echo "FAIL rollback_ok writable rc=0"; fail_n=1; }

prep_env noman
chmod -R u+w "$ROLLBACK"
rm -f "$ROLLBACK/release_manifest.json"
out=$(rollback_ok "$ROLLBACK" 2>&1); rc=$?
assert_out "rollback_ok manifest" "manifest missing" "$out"
[ "$rc" -ne 0 ] || { echo "FAIL rollback_ok manifest rc=0"; fail_n=1; }

integrity_ok(){ printf '%s\n' 'release integrity: FAIL — 1 files checked' '  ✗ stray'; return 1; }
prep_env badint
out=$(rollback_ok "$ROLLBACK" 2>&1); rc=$?
assert_out "rollback_ok integrity" "fails integrity" "$out"
[ "$rc" -ne 0 ] || { echo "FAIL rollback_ok integrity rc=0"; fail_n=1; }
integrity_ok(){ echo INTEGRITY_OK; return 0; }

# --- _postgate journal cases; wrapper is what _switch does on failure ---
_switch_back(){ echo SWITCH_BACK >> "$CALLLOG"; }
run_postgate(){ _postgate || { echo "POSTGATE FAILED -> automatic R1 rollback under lock"; _switch_back; return 1; }; }

prep_env a
JMODE=clean
out=$(run_postgate 2>&1); rc=$?
assert_out "postgate clean JOURNAL_CLEAN" "JOURNAL_CLEAN" "$out"
assert_out "postgate clean POSTGATE_PASS" "POSTGATE_PASS" "$out"
[ "$rc" -eq 0 ] || { echo "FAIL postgate clean rc=$rc"; fail_n=1; }
assert_absent "postgate clean no switch_back" "SWITCH_BACK" "$(cat "$CALLLOG")"

prep_env b
JMODE=err
out=$(run_postgate 2>&1); rc=$?
assert_out "postgate err line" "journal errors:" "$out"
assert_absent "postgate err no POSTGATE_PASS" "POSTGATE_PASS" "$out"
assert_out "postgate err switch_back" "SWITCH_BACK" "$(cat "$CALLLOG")"
[ "$rc" -ne 0 ] || { echo "FAIL postgate err rc=0"; fail_n=1; }

prep_env c
JMODE=fail
out=$(run_postgate 2>&1); rc=$?
assert_out "postgate journalctl rc" "journalctl rc=1" "$out"
assert_absent "postgate journalctl rc no POSTGATE_PASS" "POSTGATE_PASS" "$out"
assert_out "postgate journalctl rc switch_back" "SWITCH_BACK" "$(cat "$CALLLOG")"
[ "$rc" -ne 0 ] || { echo "FAIL postgate journalctl rc rc=0"; fail_n=1; }

# --- _switch refuses before lock or restart when rollback_ok fails ---
rollback_ok(){ return 1; }
prep_env nosw
JMODE=clean
out=$(_switch 2>&1); rc=$?
assert_out "switch no rollback" "no verified rollback target" "$out"
assert_absent "switch no restart" "RESTART" "$(cat "$CALLLOG")"
assert_absent "switch no ln" "LN " "$(cat "$CALLLOG")"
if [ -e "$SHARED/deploy.lock" ]; then echo "FAIL switch took the lock"; fail_n=1; else echo "PASS switch lock not taken"; fi
[ "$rc" -ne 0 ] || { echo "FAIL switch no-rollback rc=0"; fail_n=1; }
rollback_ok(){ local d=$1
  [ -d "$d" ] && [ -s "$d/release_manifest.json" ] || { fail "rollback target missing/manifest missing: $d"; return 1; }
  [ -z "$(find "$d" -mindepth 1 \( -name .venv -prune \) -o \( -name __pycache__ -o -name '*.pyc' \) -print)" ] || { fail "rollback target has pycache: $d"; return 1; }
  [ -z "$(find "$d" -mindepth 1 \( -name .venv -prune \) -o -perm -u=w -print -quit)" ] || { fail "rollback target not read-only: $d"; return 1; }
  integrity_ok "$d" "$EV/integrity_rollback_$(basename "$d").txt" || { fail "rollback target fails integrity: $d"; return 1; }
  echo ROLLBACK_TARGET_OK; }

# --- _switch_back flaps when NRestarts increments ---
eval "$ORIG_SWITCH_BACK"
prep_env flap
MODE=flap
JMODE=clean
out=$(_switch_back 2>&1); rc=$?
assert_out "switch_back flap" "pid/NRestarts moved" "$out"
assert_absent "switch_back flap no verified" "ROLLBACK_VERIFIED" "$out"
[ "$rc" -ne 0 ] || { echo "FAIL switch_back flap rc=0"; fail_n=1; }

# --- full _switch happy path ---
prep_env happy
MODE=stable
JMODE=clean
# real _switch_back must not run; count calls
_switch_back(){ echo SWITCH_BACK >> "$CALLLOG"; echo "HOLD unexpected"; return 1; }
out=$(_switch 2>&1); rc=$?
assert_out "switch happy SWITCH_DONE" "SWITCH_DONE" "$out"
assert_out "switch happy POSTGATE_PASS" "POSTGATE_PASS" "$out"
assert_out "switch happy lock taken" "LOCK_TAKEN" "$out"
assert_out "switch happy lock released" "LOCK_RELEASED" "$out"
assert_absent "switch happy no switch_back" "SWITCH_BACK" "$(cat "$CALLLOG")"
[ "$rc" -eq 0 ] || { echo "FAIL switch happy rc=$rc"; printf '%s\n' "$out" | head -n 50 >&2; fail_n=1; }
if [ -d "$SHARED/deploy.lock" ]; then echo "FAIL switch happy lock still held"; fail_n=1; else echo "PASS switch happy lock released on disk"; fi

chmod -R u+w "$BASE" 2>/dev/null || true
rm -rf "$BASE"
exit $fail_n
