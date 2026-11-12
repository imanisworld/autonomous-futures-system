#!/bin/bash
# Fix A unit table. Reads journal_err_clean from the v11 runbook. No VPS.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
BOOK="$ROOT/futures_release_recut_and_os_maintenance_RUNBOOK_DRAFT.v11.md"
line=$(grep -n '^journal_err_clean()' "$BOOK" | head -n 1 | cut -d: -f1)
[ -n "$line" ] || { echo "FAIL: helper missing"; exit 1; }
eval "$(sed -n "${line}p" "$BOOK")"
fail=0
TMP=$(mktemp -d)
check(){
  local name=$1 expect=$2 file=$3 rc
  if [ "$file" = MISSING ]; then
    journal_err_clean "$TMP/missing"
  else
    journal_err_clean "$file"
  fi
  rc=$?
  if [ "$rc" = "$expect" ]; then
    echo "PASS $name rc=$rc"
  else
    echo "FAIL $name rc=$rc expect=$expect"
    fail=1
  fi
}
: > "$TMP/empty"
printf '%s\n' '-- No entries --' > "$TMP/nl"
printf '%s' '-- No entries --' > "$TMP/nonewline"
printf '\n\t\n' > "$TMP/ws"
printf '%s\n' 'Oct 29 21:10:01 h python[1]: Traceback boom' > "$TMP/real"
printf '%s\n' '-- No entries --' 'Oct 29 x: ERROR y' > "$TMP/mixed"
printf '%s\n' 'Oct 29 x: ERROR -- No entries -- parse' > "$TMP/substr"
check empty 0 "$TMP/empty"
check no-entries 0 "$TMP/nl"
check no-entries-no-newline 0 "$TMP/nonewline"
check whitespace 0 "$TMP/ws"
check real-error 1 "$TMP/real"
check mixed 1 "$TMP/mixed"
check missing 2 MISSING
check substring-trap 1 "$TMP/substr"
rm -rf "$TMP"
exit $fail
