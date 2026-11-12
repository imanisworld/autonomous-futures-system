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
SAMPLEFILE="$BASE/samples"
MODEFILE="$BASE/imode"
mkdir -p "$BIN"
: > "$CALLLOG"
echo 0 > "$NRFILE"
echo 0 > "$SAMPLEFILE"
echo ok > "$MODEFILE"

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
echo "CURL $*" >> "$CALLLOG"
exit "${CURL_RC:-0}"
EOF
cat > "$BIN/sha256sum" << 'EOF'
#!/bin/bash
for a in "$@"; do echo "abc  $a"; done
EOF
cat > "$BIN/journalctl" << 'EOF'
#!/bin/bash
echo "JC $*" >> "$CALLLOG"
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
echo "${CWD_LINK:-$NEW}"
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
if [ "$1" = is-active ]; then
  s=$(cat "$SAMPLEFILE")
  s=$((s+1))
  echo "$s" > "$SAMPLEFILE"
  echo active
  exit 0
fi
if [ "$1" = restart ]; then echo "RESTART" >> "$CALLLOG"; exit 0; fi
case "$*" in
  *Environment*)
    printf '%s\n' "${SYSTEMD_ENV:-}"
    ;;
  *NRestarts*)
    if [ "${MODE:-stable}" = flap ]; then
      n=$(cat "$NRFILE")
      n=$((n+1))
      echo "$n" > "$NRFILE"
      echo "$n"
    elif [ "${MODE:-stable}" = third ]; then
      s=$(cat "$SAMPLEFILE")
      if [ "$s" -ge 3 ]; then echo 1; else echo 0; fi
    else
      echo 0
    fi
    ;;
  *MainPID*)
    if [ "${MODE:-stable}" = nopid ] && ! grep -q '^RESTART$' "$CALLLOG"; then
      echo 0
    else
      echo 4242
    fi
    ;;
  *) ;;
esac
exit 0
EOF
chmod 755 "$BIN/sleep" "$BIN/stat" "$BIN/curl" "$BIN/sha256sum" "$BIN/journalctl" "$BIN/readlink" "$BIN/ln" "$BIN/mv" "$BIN/systemctl"

export PATH="$BIN:$PATH"
export CALLLOG NRFILE SAMPLEFILE MODEFILE
export JMODE=clean
export MODE=stable
export CURL_RC=0
export SYSTEMD_ENV=

# shellcheck disable=SC1090
. "$LIB"
ORIG_SWITCH_BACK=$(declare -f _switch_back)
ORIG_ROLLBACK_OK=$(declare -f rollback_ok)
touch(){ echo "TOUCH $*" >> "$CALLLOG"; command touch "$@"; }
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
  chmod -R u+w "$WORK" 2>/dev/null || true
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
  command touch "$SWITCH_MARKER"
  STATE="$WORK/state.env"
  STAMP=testharness
  LOCK_OWNER=maint-recut-testharness
  CUT_OK=1
  ROLLBACK="$WORK/rb"
  make_clean_tree "$ROLLBACK"
  write_pins "$EV/integrity_pins.txt"
  printf '%s\n' "STAMP=$STAMP" "EV=$EV" "NEW=$NEW" "OLD=$OLD" "ROLLBACK=$ROLLBACK" "LOCK_OWNER=$LOCK_OWNER" "CUT_OK=1" "INTEGRITY_PINS=$EV/integrity_pins.txt" > "$STATE"
  chmod 600 "$STATE"
  : > "$EV/pins_pre.txt"
  sha256sum "$NEW/context/wide_stop_ledger_paper.py" "$NEW/context/wide_stop_execution.py" | awk '{print $1}' > "$EV/cap_files_sha_pre.txt"
  sha256sum /etc/systemd/system/futures-bot.service.d/*.conf > "$EV/dropins_sha_pre.txt"
  : > "$CALLLOG"
  echo 0 > "$NRFILE"
  echo 0 > "$SAMPLEFILE"
  echo ok > "$MODEFILE"
  MODE=stable
  CURL_RC=0
  SYSTEMD_ENV=
  JMODE=clean
  CWD_LINK="$NEW"
  export EV SHARED NEW OLD LINK SWITCH_MARKER STATE STAMP LOCK_OWNER CUT_OK ROLLBACK INTEGRITY_PINS
  export JMODE MODE CURL_RC SYSTEMD_ENV CWD_LINK
}

make_clean_tree(){
  local d=$1
  rm -rf "$d"
  mkdir -p "$d/.venv/bin"
  echo '{}' > "$d/release_manifest.json"
  cat > "$d/.venv/bin/python" << EOF
#!/bin/bash
mode=\$(cat "$MODEFILE" 2>/dev/null || echo ok)
if [ "\$mode" = fail ]; then
  printf '%s\n' 'release integrity: FAIL — 1 files checked' '  ✗ stray'
  exit 1
fi
printf '%s\n' 'release integrity: OK — 3 files checked, release test'
exit 0
EOF
  chmod 755 "$d/.venv/bin/python"
  chmod -R a-w "$d"
}

write_pins(){
  cat > "$1" << 'EOF'
EXPECTED_LIVE_COMMIT=94a71bdbb975b53f21a81b7084482ea3c5270ceb
EXPECTED_RELEASE_FINGERPRINT=abc
EXPECTED_RISK_RULES_SHA256=def
RELEASE_INTEGRITY_ENFORCED=true
EOF
  INTEGRITY_PINS=$1
  export INTEGRITY_PINS
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

echo fail > "$MODEFILE"
prep_env badint
echo fail > "$MODEFILE"
out=$(rollback_ok "$ROLLBACK" 2>&1); rc=$?
assert_out "rollback_ok integrity" "fails integrity" "$out"
[ "$rc" -ne 0 ] || { echo "FAIL rollback_ok integrity rc=0"; fail_n=1; }
echo ok > "$MODEFILE"

prep_env nopins
MODE=nopid
out=$(rollback_ok "$ROLLBACK" 2>&1); rc=$?
assert_out "rollback_ok no pid with saved pins" "ROLLBACK_TARGET_OK" "$out"
assert_absent "rollback_ok no pid does not demand MainPID" "no MainPID" "$out"
[ "$rc" -eq 0 ] || { echo "FAIL rollback_ok no pid rc=$rc"; fail_n=1; }

prep_env sysd
rm -f "$EV/integrity_pins.txt"
unset INTEGRITY_PINS
MODE=nopid
SYSTEMD_ENV='EXPECTED_LIVE_COMMIT=94a71bdbb975b53f21a81b7084482ea3c5270ceb EXPECTED_RELEASE_FINGERPRINT=abc EXPECTED_RISK_RULES_SHA256=def RELEASE_INTEGRITY_ENFORCED=true'
export SYSTEMD_ENV
out=$(rollback_ok "$ROLLBACK" 2>&1); rc=$?
assert_out "rollback_ok no pid systemd pins" "ROLLBACK_TARGET_OK" "$out"
[ "$rc" -eq 0 ] || { echo "FAIL rollback_ok systemd rc=$rc"; printf '%s\n' "$out" | head -n 20 >&2; fail_n=1; }

prep_env dotenv
rm -f "$EV/integrity_pins.txt"
unset INTEGRITY_PINS
SYSTEMD_ENV=
export SYSTEMD_ENV
MODE=nopid
cat > "$SHARED/.env" << 'EOF'
EXPECTED_LIVE_COMMIT=94a71bdbb975b53f21a81b7084482ea3c5270ceb
EXPECTED_RELEASE_FINGERPRINT=abc
EXPECTED_RISK_RULES_SHA256=def
RELEASE_INTEGRITY_ENFORCED=true
EOF
out=$(rollback_ok "$ROLLBACK" 2>&1); rc=$?
assert_absent "rollback_ok ignores dotenv" "ROLLBACK_TARGET_OK" "$out"
[ "$rc" -ne 0 ] || { echo "FAIL rollback_ok dotenv rc=0"; fail_n=1; }

# --- _postgate journal cases; wrapper is what _switch does on failure ---
_switch_back(){ echo SWITCH_BACK >> "$CALLLOG"; }
run_postgate(){ _postgate || { echo "POSTGATE FAILED -> automatic R1 rollback under lock"; _switch_back; return 1; }; }

prep_env a
JMODE=clean
out=$(run_postgate 2>&1); rc=$?
assert_out "postgate clean JOURNAL_CLEAN" "JOURNAL_CLEAN" "$out"
assert_out "postgate clean POSTGATE_PASS" "POSTGATE_PASS" "$out"
assert_out "postgate journalctl -q" "JC -q " "$(cat "$CALLLOG")"
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
eval "$ORIG_ROLLBACK_OK"

# --- _switch_back flaps when NRestarts increments ---
eval "$ORIG_SWITCH_BACK"
prep_env flap
MODE=flap
JMODE=clean
out=$(_switch_back 2>&1); rc=$?
assert_out "switch_back flap" "pid/NRestarts moved" "$out"
assert_absent "switch_back flap no verified" "ROLLBACK_VERIFIED" "$out"
[ "$rc" -ne 0 ] || { echo "FAIL switch_back flap rc=0"; fail_n=1; }

prep_env port
MODE=stable
CURL_RC=1
out=$(_switch_back 2>&1); rc=$?
assert_out "switch_back port 8000" "port 8000" "$out"
assert_absent "switch_back port no verified" "ROLLBACK_VERIFIED" "$out"
[ "$rc" -ne 0 ] || { echo "FAIL switch_back port rc=0"; fail_n=1; }

prep_env third
MODE=third
out=$(_switch_back 2>&1); rc=$?
assert_out "switch_back third sample" "pid/NRestarts moved" "$out"
assert_absent "switch_back third no verified" "ROLLBACK_VERIFIED" "$out"
[ "$rc" -ne 0 ] || { echo "FAIL switch_back third rc=0"; fail_n=1; }

prep_env gate
chmod -R u+w "$ROLLBACK"
rm -f "$ROLLBACK/release_manifest.json"
out=$(_switch_back 2>&1); rc=$?
assert_out "switch_back recheck" "not restarting" "$out"
assert_absent "switch_back recheck no restart" "RESTART" "$(cat "$CALLLOG")"
[ "$rc" -ne 0 ] || { echo "FAIL switch_back recheck rc=0"; fail_n=1; }

prep_env sb0
MODE=nopid
CWD_LINK="$ROLLBACK"
out=$(_switch_back 2>&1); rc=$?
assert_out "switch_back no pid" "ROLLBACK_VERIFIED" "$out"
assert_out "switch_back no pid restarted" "RESTART" "$(cat "$CALLLOG")"
assert_absent "switch_back no pid no MainPID demand" "no MainPID" "$out"
[ "$rc" -eq 0 ] || { echo "FAIL switch_back no pid rc=$rc"; printf '%s\n' "$out" | head -n 30 >&2; fail_n=1; }

prep_env owner
RELEASES="$WORK/releases"
NEW="$RELEASES/${SHORT}-recut-$STAMP"
make_clean_tree "$NEW"
OLD="$NEW"
printf '%s\n' "STAMP=$STAMP" "EV=$EV" "NEW=$NEW" "OLD=$OLD" "ROLLBACK=$ROLLBACK" "LOCK_OWNER=$LOCK_OWNER" "CUT_OK=1" "INTEGRITY_PINS=$INTEGRITY_PINS" "LINK=$LINK" "SWITCH_MARKER=$SWITCH_MARKER" > "$STATE"
mkdir -p "$SHARED/deploy.lock"
printf 'owner=%s1\npid=1\nstarted_utc=t\nsha=x\n' "$LOCK_OWNER" > "$SHARED/deploy.lock/meta.txt"
out=$(step_rollback 2>&1); rc=$?
assert_absent "lock owner substring does not match" "RESTART" "$(cat "$CALLLOG")"
assert_absent "lock owner test reached the lock" "state incomplete" "$out"
[ "$rc" -ne 0 ] || { echo "FAIL lock owner substring rc=0"; printf '%s\n' "$out" | head -n 20 >&2; fail_n=1; }

prep_env rs
MODE=nopid
RELEASES="$WORK/releases"
NEW="$RELEASES/${SHORT}-recut-$STAMP"
CWD_LINK="$NEW"
make_clean_tree "$NEW"
printf '%s\n' "STAMP=$STAMP" "EV=$EV" "NEW=$NEW" "OLD=$OLD" "ROLLBACK=$ROLLBACK" "LOCK_OWNER=$LOCK_OWNER" "CUT_OK=1" "INTEGRITY_PINS=$INTEGRITY_PINS" "LINK=$LINK" "SWITCH_MARKER=$SWITCH_MARKER" > "$STATE"
mkdir -p "$SHARED/deploy.lock"
printf 'owner=%s\npid=1\nstarted_utc=t\nsha=x\n' "$LOCK_OWNER" > "$SHARED/deploy.lock/meta.txt"
export RESWITCH_FALSE_POSITIVE=1
out=$(step_reswitch_new 2>&1); rc=$?
assert_out "reswitch no pid" "RESWITCH_DONE" "$out"
assert_out "reswitch no pid restarted" "RESTART" "$(cat "$CALLLOG")"
assert_absent "reswitch no pid no MainPID demand" "no MainPID" "$out"
[ "$rc" -eq 0 ] || { echo "FAIL reswitch no pid rc=$rc"; printf '%s\n' "$out" | head -n 40 >&2; fail_n=1; }
unset RESWITCH_FALSE_POSITIVE

python3 - "$LIB" << 'PY'
import re, sys
text = open(sys.argv[1]).read()
def fn(name):
    out = []
    on = False
    for line in text.splitlines(True):
        if line.startswith(name + "()"):
            on = True
            out.append(line)
            continue
        if on and re.match(r"^[A-Za-z_][A-Za-z0-9_]*\(\)", line):
            break
        if on:
            out.append(line)
    return "".join(out)
err = []
post = fn("_postgate")
if "journalctl -q -u futures-bot" not in post:
    err.append("post-gate journalctl -q")
sb = fn("_switch_back")
if sb.find("rollback_ok") < 0 or sb.find("systemctl restart") < 0 or sb.find("rollback_ok") > sb.find("systemctl restart"):
    err.append("rollback_ok before switch-back restart")
sw = fn("_switch")
touch_at = sw.find('touch "$SWITCH_MARKER"')
restart_at = sw.find("systemctl restart futures-bot")
if touch_at < 0 or restart_at < 0 or touch_at > restart_at:
    err.append("marker before restart")
step = fn("step_rollback")
if 'grep -qx "owner=$LOCK_OWNER"' not in step or "grep -c" in step:
    err.append("exact lock owner")
ver = fn("_stable_verify")
if "127.0.0.1:8000/status/broker-account" not in ver:
    err.append("port 8000")
if 'while [ "$i" -le 3 ]' not in ver:
    err.append("3 verify samples")
ro = fn("rollback_ok")
if "integrity_ok_from_pins" not in ro or "fb_pid" in ro:
    err.append("runbook rollback_ok")
if "afs-shared/.env" in text:
    err.append("lib mentions shared env file")
if err:
    sys.stderr.write("FAIL source guards: " + ", ".join(err) + "\n")
    sys.exit(1)
print("PASS source guards")
PY
[ $? -eq 0 ] || fail_n=1

lib_n=$(python3 -c '
import re, sys
text=open(sys.argv[1]).read()
name="rollback_ok"
out=[]; on=False; depth=0
for line in text.splitlines(True):
    if not on:
        if line.startswith(name+"()"):
            on=True
        else:
            continue
    out.append(line)
    depth += line.count("{")-line.count("}")
    if on and depth==0:
        break
sys.stdout.write(re.sub(r"[\s;]+","", "".join(out)))
' "$LIB")
dec_n=$(declare -f rollback_ok | tr -d '[:space:];')
if [ "$lib_n" = "$dec_n" ]; then
  echo "PASS shell rollback_ok is the runbook function"
else
  echo "FAIL shell rollback_ok is not the runbook function"
  echo "lib_len=${#lib_n} dec_len=${#dec_n}" >&2
  fail_n=1
fi

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
if ! python3 -c 'import pathlib,sys; t=pathlib.Path(sys.argv[1]).read_text(); i=t.find("TOUCH "); j=t.find("\nRESTART\n"); sys.exit(0 if 0<=i<j else 1)' "$CALLLOG"; then echo "FAIL marker not touched before restart"; fail_n=1; else echo "PASS marker before restart"; fi
[ "$rc" -eq 0 ] || { echo "FAIL switch happy rc=$rc"; printf '%s\n' "$out" | head -n 50 >&2; fail_n=1; }
if [ -d "$SHARED/deploy.lock" ]; then echo "FAIL switch happy lock still held"; fail_n=1; else echo "PASS switch happy lock released on disk"; fi

chmod -R u+w "$BASE" 2>/dev/null || true
rm -rf "$BASE"
exit $fail_n
