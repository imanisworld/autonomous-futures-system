#!/usr/bin/env python3
"""scanner-suppression-detail -- READ-ONLY breakdown of today's suppression reasons.

Forced-command name: scanner-suppression-detail   (no arguments; argv ignored)

PURPOSE
  For today (America/New_York calendar day): every alert_suppression_reason
  bucket with count, first/last time ET and per-ticker counts; the individual
  values behind variable-suffix buckets (planned-risk amounts, no_liquid_contract
  details, remaining_rr values); contract detail for planned_risk_outside_v1_cap
  and no_liquid_contract rows; and per-tick integrity of the scheduled scan.

READ-ONLY GUARANTEES
  Same as the other scanner_* audit scripts: sqlite3 'file:<db>?mode=ro' URI +
  PRAGMA query_only=ON, no writes, no files/temp files created, no env reads,
  whitelisted + truncated + secret-masked output only. WAL DB without sidecars
  => ERROR, exit 3, RESULT_INCOMPLETE=true (ALLOW_IMMUTABLE_FALLBACK is False).
  Missing or unreadable DB / missing `scans` => ERROR line and non-zero exit.

FIELDS PRINTED
  REASON        bucket count sent first_et last_et distinct_tickers tickers
  REASON_VALUE  bucket value count tickers        (variable-suffix buckets)
  RISKCAP       id time_et ticker dir amount implied_ask_from_amount contract
                ask bid entry_premium delta strike expiry dte dte_src src
                sel_status          (raw_json first, else options_selector_evidence
                                     via raw_json.selector_evidence_id)
  NOLIQ         id time_et ticker dir detail chosen_expiration dte sel_status
                sel_reason
  SOURCE        scan source histogram
  TICKS / WATCHLIST_DERIVED / SYMBOL_TICKS / SYMBOL_TICKS_SUMMARY /
  TICK_INCOMPLETE / TICK_GAP   per-tick integrity of source='scheduled*' rows
                (a tick = one distinct scans.timestamp; scan_watchlist stamps
                every ticker of a cycle with the same `now`)

EXIT CODES 0 ok | 2 DB missing | 3 DB unreadable/unopenable | 4 table missing |
           5 DB changed during an immutable=1 read (unreachable while
           ALLOW_IMMUTABLE_FALLBACK is False) | 6 rowid/timestamp order is
           not monotonic or not provable (exact two-line ERROR, no counts) |
           1 unexpected error
  Every non-zero exit except 6 also prints ERROR and RESULT_INCOMPLETE=true.

ROWID / TIMESTAMP ORDER
  The day-window read binary-searches scans.id (and the same pattern on the
  other tables) so it does not full-scan a multi-GB table. That is safe only
  if no row inside the window has a smaller id. guard_id_floor proves it:
  boundary rows just below the floor must be strictly older than the window
  start; when the id span below the floor is small, every prefix timestamp is
  read and must be older and non-decreasing (a complete MAX proof); when the
  span is large, a prefix scan is refused because a rollback-journal SHARED
  lock would block scanner commits. The large-table proof instead range-scans
  the lexical window through a NOT NULL index prefix plus the timestamp
  (scans: idx_scans_alert_key). If that proof is unavailable, the script
  fails closed rather than scanning. Timestamps in the scanned id range must
  also be non-decreasing. On failure the process prints exactly
      ERROR: ROWID_TIMESTAMP_ORDER_NOT_MONOTONIC
      RESULT_INCOMPLETE=true
  and exits 6. No counts are emitted. See guard_id_floor in the common block.

IMMUTABLE FALLBACK
  ALLOW_IMMUTABLE_FALLBACK is False. A WAL file with no -wal/-shm sidecars is
  not opened. Exit 3, an ERROR line, and RESULT_INCOMPLETE=true. The
  immutable=1 branch remains in open_db_readonly and does not run.
"""
# ---- path constants (the ONLY configuration; tests patch the ROOT line) ----
ROOT = "/root"
DB_PATH = ROOT + "/afs-shared/logs/options_scanner.sqlite"
ALLOW_IMMUTABLE_FALLBACK = False
SCRIPT = "scanner-suppression-detail"
VERSION = "2"

# ===== BEGIN COMMON BLOCK v1 (byte-identical in all four scanner_* scripts) =====
# Stdlib only. Nothing below opens a file for writing, creates temp files,
# reads environment variables, or looks at sys.argv.
import collections
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
MAX_ROWS = 200            # per-section row cap; a truncated=N line is printed
MAX_TOTAL_LINES = 4000    # hard cap on total output lines
_SECRETISH = re.compile(
    r"(?i)(https?://|discord(app)?\.com/api|webhook|bearer\s|token|api[_-]?key|"
    r"secret|passw|authorization|cookie|account_?id)"
)
# Exact values that trip _SECRETISH but are known non-secret labels
# (the Signa error code 'missing_api_key'; scans.source values 'webhook', 'webhook:daily').
_SAFE_LABELS = frozenset(["missing_api_key", "webhook", "webhook:daily"])
_STATE = {"printed": 0, "suppressed": 0, "hold": [], "buffering": True}
# Exit 6. Exact two lines, no counts, no end_of_report. See guard_id_floor.
EXIT_ROWID_ORDER = 6
# Prefix reads at or under this id span are short enough to finish without
# holding a rollback-journal SHARED lock across a multi-GB scans table.
_PROOF_ID_SPAN_MAX = 8192
_PROOF_GROUP_MAX = 8192
_PROOF_WINDOW_ROW_MAX = 100000
_BOUNDARY_ROWS = 8
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _emit_line(line):
    if _STATE["printed"] >= MAX_TOTAL_LINES:
        _STATE["suppressed"] += 1
        return
    _STATE["printed"] += 1
    sys.stdout.write(line + "\n")


def release_output():
    """Flush the buffered report. Idempotent."""
    if not _STATE["buffering"]:
        return
    _STATE["buffering"] = False
    pending = _STATE["hold"]
    _STATE["hold"] = []
    for line in pending:
        _emit_line(line)


def out(line=""):
    # Buffer until the rowid proof has passed (or a non-rowid failure).
    # A rowid failure discards the buffer so partial counts cannot leak.
    if _STATE["buffering"]:
        _STATE["hold"].append(line)
        return
    _emit_line(line)


def safe(value, limit=80):
    """Render one whitelisted value as a single grep-friendly token."""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        text = repr(round(value, 4))
    else:
        text = str(value)
    text = re.sub(r"\s+", "_", text.strip())
    if text == "":
        return "<empty>"
    if _SECRETISH.search(text) and text not in _SAFE_LABELS:
        return "<masked>"
    if len(text) > limit:
        text = "%s...(+%d)" % (text[:limit], len(text) - limit)
    return text


def emit(tag, *pairs):
    """One record line: TAG key=value key=value ... (values via safe())."""
    parts = [tag]
    for item in pairs:
        limit = item[2] if len(item) > 2 else 80
        parts.append("%s=%s" % (item[0], safe(item[1], limit)))
    out(" ".join(parts))


def rdisp(reason):
    """Suppression reason for display: NULL and '' are distinct and visible."""
    if reason is None:
        return "<null>"
    if str(reason).strip() == "":
        return "<empty>"
    return str(reason)


def fmt_counts(counter, limit=40):
    items = sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))
    text = ",".join("%s:%d" % (safe(k, 40), v) for k, v in items[:limit])
    if len(items) > limit:
        text += ",+%d_more" % (len(items) - limit)
    return text or "-"


def truncated_note(section, total, shown):
    if total > shown:
        out("truncated=%d section=%s shown=%d total=%d" % (total - shown, section, shown, total))


# ---- time ---------------------------------------------------------------
def parse_ts(value):
    """Parse an ISO-8601 string into an aware UTC datetime (naive => UTC)."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text[-1] in "Zz":
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC)


def et_midnight_utc(et_date):
    return datetime.combine(et_date, dtime(0), tzinfo=ET).astimezone(UTC)


def et_day_bounds(now=None):
    """'Today' = current America/New_York date; bounds [00:00 ET, next 00:00 ET) in UTC.

    zoneinfo resolves the offset of each midnight separately, so DST days are
    23h (March) / 25h (November) long, as they should be.
    """
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    et_date = now.astimezone(ET).date()
    return et_date, et_midnight_utc(et_date), et_midnight_utc(et_date + timedelta(days=1))


def fmt_et(stamp):
    return "-" if stamp is None else stamp.astimezone(ET).isoformat(timespec="seconds")


def fmt_utc(stamp):
    return "-" if stamp is None else stamp.astimezone(UTC).isoformat(timespec="seconds")


def fmt_hms_utc(stamp):
    return "-" if stamp is None else stamp.astimezone(UTC).strftime("%H:%M:%SZ")


def fmt_mtime(path_stat):
    return fmt_et(datetime.fromtimestamp(path_stat.st_mtime, UTC))


def print_header(script, version, now, et_date, start, end):
    out("# %s  (READ-ONLY audit; takes no arguments; argv is ignored)" % script)
    out("script=%s version=%s python=%s sqlite_lib=%s" % (
        script, version, sys.version.split()[0], sqlite3.sqlite_version))
    out("generated_at_et=%s generated_at_utc=%s" % (fmt_et(now), fmt_utc(now)))
    out("et_date=%s day_start_utc=%s day_end_utc=%s day_hours=%s" % (
        et_date.isoformat(), fmt_utc(start), fmt_utc(end),
        repr((end - start).total_seconds() / 3600.0)))


def finish(code):
    release_output()
    if _STATE["suppressed"]:
        sys.stdout.write("output_truncated_lines=%d (MAX_TOTAL_LINES=%d)\n" % (
            _STATE["suppressed"], MAX_TOTAL_LINES))
    sys.stdout.write("end_of_report exit=%d\n" % code)
    sys.stdout.flush()
    return code


def fail(code, message):
    """Loud failure: an 'ERROR:' line, RESULT_INCOMPLETE, non-zero exit.

    Never a best-effort result. Releases any buffered header first.
    """
    release_output()
    sys.stdout.write("ERROR: %s\n" % message)
    sys.stdout.write("RESULT_INCOMPLETE=true\n")
    finish(code)
    sys.exit(code)


def fail_rowid_order():
    """Rowid/timestamp order is inverted or not provable inside the cost budget.

    Prints exactly two lines and nothing else (the buffered report is dropped).
    """
    _STATE["hold"] = []
    _STATE["buffering"] = False
    sys.stdout.write("ERROR: ROWID_TIMESTAMP_ORDER_NOT_MONOTONIC\n")
    sys.stdout.write("RESULT_INCOMPLETE=true\n")
    sys.stdout.flush()
    sys.exit(EXIT_ROWID_ORDER)


def run(main_fn):
    _STATE["printed"] = 0
    _STATE["suppressed"] = 0
    _STATE["hold"] = []
    _STATE["buffering"] = True
    try:
        code = main_fn()
    except SystemExit:
        raise
    except Exception as exc:  # report, never a traceback that could echo data
        release_output()
        sys.stdout.write("ERROR: unexpected %s: %s\n" % (type(exc).__name__, safe(str(exc), 200)))
        sys.stdout.write("RESULT_INCOMPLETE=true\n")
        code = finish(1)
    sys.exit(code)


# ---- read-only sqlite -----------------------------------------------------
_SIDECARS = ("-wal", "-shm", "-journal")


def _stat_or_none(path):
    try:
        return os.stat(path)
    except FileNotFoundError:
        return None


def _db_state(path):
    return dict((suffix, _stat_or_none(path + suffix)) for suffix in ("",) + _SIDECARS)


def _state_text(st):
    return "absent" if st is None else "present(size=%d)" % st.st_size


def db_header_mode(path):
    """Journal mode from the 100-byte header (bytes 18/19 == 2 means WAL). Pure read."""
    with open(path, "rb") as handle:
        header = handle.read(100)
    if len(header) < 100 or not header.startswith(b"SQLite format 3\x00"):
        return "not_sqlite"
    return "wal" if 2 in (header[18], header[19]) else "rollback"



def _quote_path(path):
    """Percent-encode a path for a SQLite file: URI. Slashes stay literal.

    UTF-8 bytes above ASCII are encoded byte by byte. No network library.
    """
    encoded = []
    for byte in path.encode("utf-8"):
        ch = chr(byte)
        if ch.isalnum() or ch in "/-._~":
            encoded.append(ch)
        else:
            encoded.append("%%%02X" % byte)
    return "".join(encoded)


def open_db_readonly(path, allow_immutable_fallback):
    """Open strictly read-only: sqlite3.connect('file:<path>?mode=ro', uri=True).

    WAL databases: a mode=ro reader needs the -wal and -shm sidecars to exist
    (SQLite >= 3.22 can then read even if they are not writable). If they are
    absent, mode=ro would either CREATE them (directory writable) or fail with
    'attempt to write a readonly database' (directory not writable). Shipped
    scripts pass allow_immutable_fallback=False and then fail closed (exit 3,
    an ERROR line, and RESULT_INCOMPLETE=true). The immutable=1 branch is
    kept only so that path stays visibly disabled; it does not run unless a
    caller passes True.
    """
    out("db_path=%s" % path)
    if not os.path.exists(path):
        fail(2, "database file not found: %s (refusing to report zeros)" % path)
    if not os.path.isfile(path):
        fail(3, "database path is not a regular file: %s" % path)
    if not os.access(path, os.R_OK):
        fail(3, "database not readable by uid=%d: %s" % (os.getuid(), path))
    try:
        mode = db_header_mode(path)
    except OSError as exc:
        fail(3, "cannot read database header (%s): %s" % (type(exc).__name__, path))
    if mode == "not_sqlite":
        fail(3, "file is not an SQLite 3 database: %s" % path)
    pre = _db_state(path)
    out("db_size=%d db_mtime_et=%s db_header_journal_mode=%s" % (
        pre[""].st_size, fmt_mtime(pre[""]), mode))
    out("db_sidecars_before %s" % " ".join(
        "%s=%s" % (s, _state_text(pre[s])) for s in _SIDECARS))
    use_immutable = False
    if mode == "wal" and (pre["-wal"] is None or pre["-shm"] is None):
        if not allow_immutable_fallback:
            fail(3, "WAL database without -wal/-shm sidecars: a mode=ro open would have to "
                    "create them (or fail); ALLOW_IMMUTABLE_FALLBACK is False. Retry while the "
                    "scanner holds the DB open, or see INSTALL.md")
        use_immutable = True
    uri = "file:%s?mode=ro%s" % (_quote_path(path), "&immutable=1" if use_immutable else "")
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.Error as exc:
        fail(3, "cannot open database read-only (%s): %s" % (type(exc).__name__, safe(str(exc), 160)))
    if use_immutable:
        out("WARNING: immutable_fallback_used=1 reason=wal_db_without_sidecars "
            "(no un-checkpointed WAL data exists, so this read is exact unless a writer "
            "checkpoints during the read; re-checked below)")
    out("open_mode=%s uri_flags=mode=ro%s query_only=on" % (
        "ro+immutable" if use_immutable else "ro", "&immutable=1" if use_immutable else ""))
    return conn, {"path": path, "pre": pre, "immutable": use_immutable}


def close_db_readonly(conn, ctx):
    conn.close()
    path, pre = ctx["path"], ctx["pre"]
    post = _db_state(path)
    out("db_sidecars_after %s" % " ".join(
        "%s=%s" % (s, _state_text(post[s])) for s in _SIDECARS))
    for suffix in _SIDECARS:
        if pre[suffix] is None and post[suffix] is not None:
            out("WARNING: sidecar_appeared_during_run=%s (a concurrent writer, or this "
                "reader if the directory is writable -- see INSTALL.md)" % suffix)
    before, after = pre[""], post[""]
    changed = after is None or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
    out("db_file_changed_during_run=%s" % ("yes" if changed else "no"))
    if ctx["immutable"] and changed:
        fail(5, "database file changed during an immutable=1 read; results may be "
                "inconsistent -- rerun")


def require_tables(conn, names):
    have = set(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"))
    missing = [n for n in names if n not in have]
    if missing:
        fail(4, "required table(s) missing: %s" % ",".join(missing))
    return have


def json_funcs_available(conn):
    try:
        conn.execute("SELECT json_extract('{\"a\":1}','$.a'), json_type('{\"a\":1}','$.a'), "
                     "json_valid('{}')").fetchone()
        return True
    except sqlite3.Error:
        return False


def _py_path(doc, path):
    cur = doc
    for part in path[2:].split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False, None
    return True, cur


def _py_type(present, value):
    if not present:
        return None
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "real"
    if isinstance(value, str):
        return "text"
    return "array" if isinstance(value, list) else "object"


def _py_value(present, value):
    if not present or value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return value


def _ident(name):
    if not isinstance(name, str) or _IDENT.fullmatch(name) is None:
        return None
    return name


def _min_id(conn, table):
    row = conn.execute("SELECT id FROM %s ORDER BY id LIMIT 1" % table).fetchone()
    return None if row is None else int(row[0])


def _notnull_columns(conn, table):
    cols = set()
    for row in conn.execute("PRAGMA table_info(%s)" % table):
        if row[3]:
            cols.add(row[1])
    return cols


def _ts_index(conn, table, ts_col):
    """Shortest non-partial index: NOT NULL equality prefix + timestamp.

    Trailing columns may only be `id` (still covering for this probe).
    Returns (index_name, prefix_cols) or None.
    """
    required = _notnull_columns(conn, table)
    best = None
    try:
        indexes = list(conn.execute("PRAGMA index_list(%s)" % table))
    except sqlite3.Error:
        return None
    for info in indexes:
        if len(info) > 4 and info[4]:
            continue
        iname = info[1]
        if _ident(iname) is None:
            continue
        cols = [r[2] for r in conn.execute("PRAGMA index_info(%s)" % iname)]
        if ts_col not in cols:
            continue
        pos = cols.index(ts_col)
        prefix = cols[:pos]
        tail = cols[pos + 1:]
        if any(_ident(c) is None for c in prefix):
            continue
        if any(c not in required for c in prefix):
            continue
        if any(c != "id" for c in tail):
            continue
        if best is None or len(prefix) < len(best[1]):
            best = (iname, tuple(prefix))
    return best


def _iter_prefixes(conn, table, index, prefix_cols):
    """Distinct prefix tuples via index seeks. Caps the group count."""
    colsql = ", ".join(prefix_cols)
    order = colsql
    seen = 0
    current = None
    while True:
        if current is None:
            sql = "SELECT %s FROM %s INDEXED BY %s ORDER BY %s LIMIT 1" % (
                colsql, table, index, order)
            row = conn.execute(sql).fetchone()
        else:
            marks = ", ".join("?" for _ in prefix_cols)
            sql = ("SELECT %s FROM %s INDEXED BY %s WHERE (%s) > (%s) "
                   "ORDER BY %s LIMIT 1" % (colsql, table, index, colsql, marks, order))
            row = conn.execute(sql, tuple(current)).fetchone()
        if row is None:
            return
        seen += 1
        if seen > _PROOF_GROUP_MAX:
            fail_rowid_order()
        current = tuple(row[i] for i in range(len(prefix_cols)))
        yield current


def _boundary_ok(conn, table, ts_col, floor, not_before):
    """PK-seek the rows just below the floor.

    Each must parse, be strictly older than not_before, and be non-decreasing
    in id. Returns the immediate predecessor's parsed time, or None.
    """
    rows = conn.execute(
        "SELECT id, %s AS ts FROM %s WHERE id < ? ORDER BY id DESC LIMIT %d" % (
            ts_col, table, _BOUNDARY_ROWS),
        (int(floor),)).fetchall()
    prev = None
    predecessor = None
    for row in reversed(rows):
        stamp = parse_ts(row[1])
        if stamp is None or stamp >= not_before:
            fail_rowid_order()
        if prev is not None and stamp < prev:
            fail_rowid_order()
        prev = stamp
        predecessor = stamp
    return predecessor


def _prove_prefix_by_span(conn, table, ts_col, floor, not_before):
    """Complete proof when the id span below floor is small.

    Reads every timestamp with id < floor. Returns False WITHOUT reading when
    the span exceeds the budget (caller must use an index proof or fail).
    """
    min_id = _min_id(conn, table)
    if min_id is None:
        fail_rowid_order()
    span = int(floor) - int(min_id)
    if span < 0:
        fail_rowid_order()
    if span > _PROOF_ID_SPAN_MAX:
        return False
    last = None
    seen = 0
    cur = conn.execute(
        "SELECT id, %s FROM %s WHERE id < ? ORDER BY id" % (ts_col, table),
        (int(floor),))
    try:
        for row in cur:
            seen += 1
            if seen > _PROOF_ID_SPAN_MAX:
                fail_rowid_order()
            stamp = parse_ts(row[1])
            if stamp is None or stamp >= not_before:
                fail_rowid_order()
            if last is not None and stamp < last:
                fail_rowid_order()
            last = stamp
    finally:
        cur.close()
    return True


def _prove_no_skip_by_index(conn, table, ts_col, floor, not_before, lex_lo, lex_hi):
    """Bounded proof that no lexical-window row with id < floor is in-window.

    Uses a covering index seek per distinct prefix, then a timestamp range
    limited to the same lexical window the report query uses. This walks the
    window, not table history. Returns False when no safe index exists.
    """
    found = _ts_index(conn, table, ts_col)
    if found is None:
        return False
    iname, prefix = found
    seen = 0
    if prefix:
        groups = _iter_prefixes(conn, table, iname, prefix)
    else:
        groups = [()]
    for group in groups:
        where = []
        params = []
        for col, val in zip(prefix, group):
            where.append("%s = ?" % col)
            params.append(val)
        where.append("%s >= ?" % ts_col)
        params.append(lex_lo)
        if lex_hi is not None:
            where.append("%s < ?" % ts_col)
            params.append(lex_hi)
        sql = "SELECT id, %s FROM %s INDEXED BY %s WHERE %s" % (
            ts_col, table, iname, " AND ".join(where))
        cur = conn.execute(sql, params)
        try:
            for row in cur:
                seen += 1
                if seen > _PROOF_WINDOW_ROW_MAX:
                    fail_rowid_order()
                if int(row[0]) >= int(floor):
                    continue
                stamp = parse_ts(row[1])
                if stamp is None or stamp >= not_before:
                    fail_rowid_order()
        finally:
            cur.close()
    return True


def guard_id_floor(conn, table, ts_col, lo_text, not_before, lex_lo, lex_hi):
    """Return (floor, probes, predecessor_ts) after proving the id filter.

    ROWID / TIMESTAMP ORDER
    -----------------------
    id_floor() binary-searches the INTEGER PRIMARY KEY so a day-window read
    does not scan a multi-GB scans table. A rollback-journal reader holds a
    SHARED lock for the whole statement, and that lock blocks the scanner's
    commit (its busy timeout is 5s). The search is valid only if no row whose
    parsed timestamp is >= not_before has an id below the floor. A backfill
    (today's timestamp, low id) would otherwise be skipped and the counts
    would look complete.

    Bounded proof, in order:
      1. Boundary. The rows immediately below the floor (a few PK seeks) must
         all parse, be strictly older than not_before, and be non-decreasing
         in id. Necessary, not sufficient: a low-id backfill is not next to
         the floor.
      2. If (floor - min id) <= _PROOF_ID_SPAN_MAX, read every timestamp with
         id < floor and require each one parses, is < not_before, and is
         non-decreasing. That is MAX(parsed timestamp) of the prefix, and it
         is a complete proof. The read is capped so it cannot be the long lock.
      3. Otherwise do not read the prefix. A full prefix scan is exactly the
         writer-blocking scan this floor exists to avoid, so it is not a
         fallback. Instead use a non-partial index whose leading columns are
         a NOT NULL equality prefix plus the timestamp (scans:
         idx_scans_alert_key on ticker, direction, pattern, timestamp).
         Distinct prefixes are enumerated with index seeks (capped). For each
         prefix, range-scan only the lexical window the report already uses.
         Any row in that window with id < floor and a parsed time >=
         not_before, or an unparseable timestamp, means a skipped or
         unprovable row. The walk is proportional to the window plus the
         number of distinct keys, not to history. On scans the index covers
         (id, timestamp), so the fat raw_json payload is not read.
      4. If the span is over budget and no such index can finish inside the
         group/window caps, the no-skip property is unprovable. Fail closed.
         Do not fall back to a full table scan.

    Separately, assert_timestamps_nondecreasing() requires parsed timestamps
    in the rows actually returned (the scanned id range) to be non-decreasing,
    and not earlier than the predecessor just below the floor. An out-of-order
    pair inside the window fails even when no row was skipped. Equal
    timestamps are ordered (one scan cycle stamps many tickers with one now).
    Comparison is on parsed absolute times. Unparseable values in the scanned
    range are skipped here (the caller counts them); an unparseable value in
    the prefix or in the lexical window below the floor fails closed, because
    it cannot be shown to be outside the day.

    Failure prints exactly two lines and exits 6. The report buffer is
    discarded, so partial counts are never shown as complete.
    """
    table_name = _ident(table)
    ts_name = _ident(ts_col)
    if table_name is None or ts_name is None:
        fail_rowid_order()
    floor, probes = id_floor(conn, table_name, ts_name, lo_text)
    if floor is None:
        any_row = conn.execute("SELECT id FROM %s LIMIT 1" % table_name).fetchone()
        if any_row is not None:
            fail_rowid_order()
        return None, probes, None
    predecessor = _boundary_ok(conn, table_name, ts_name, floor, not_before)
    proved = _prove_prefix_by_span(conn, table_name, ts_name, floor, not_before)
    if not proved:
        proved = _prove_no_skip_by_index(
            conn, table_name, ts_name, floor, not_before, lex_lo, lex_hi)
    if not proved:
        fail_rowid_order()
    return floor, probes, predecessor


def assert_timestamps_nondecreasing(pairs, predecessor=None):
    """Parsed timestamps must be non-decreasing in id. None stamps are skipped."""
    last = predecessor
    for _row_id, stamp in sorted(pairs, key=lambda item: item[0]):
        if stamp is None:
            continue
        if last is not None and stamp < last:
            fail_rowid_order()
        last = stamp


def id_floor(conn, table, ts_col, lo_text):
    """Candidate first id whose timestamp text is >= lo_text (PK binary search).

    O(log n) rowid probes, not a full-table scan. The candidate is safe to
    filter on only after guard_id_floor proves no in-window row sits below it.
    Returns (floor, probes); floor is None when the table is empty or a probe
    fails (the caller then fails closed if the table is not empty).
    """
    try:
        # two separate probes: "SELECT min(id), max(id)" in ONE statement defeats
        # SQLite's min/max optimisation and scans the whole table.
        first = conn.execute("SELECT id FROM %s ORDER BY id LIMIT 1" % table).fetchone()
        last = conn.execute("SELECT id FROM %s ORDER BY id DESC LIMIT 1" % table).fetchone()
    except sqlite3.Error:
        return None, 0
    if first is None or last is None:
        return None, 0
    lo_id, hi_id = first[0], last[0]
    probes = 0
    lo, hi = int(lo_id), int(hi_id) + 1
    while lo < hi:
        mid = (lo + hi) // 2
        probes += 1
        row = conn.execute("SELECT id, %s FROM %s WHERE id >= ? ORDER BY id LIMIT 1" % (ts_col, table),
                           (mid,)).fetchone()
        if row is None:
            hi = mid
        elif str(row[1]) >= lo_text:
            hi = mid
        else:
            lo = int(row[0]) + 1
    return lo, probes


def select_window(conn, table, ts_col, base_cols, jfields, start, end, where_extra=""):
    """Rows of `table` whose `ts_col` parses into [start, end), as dicts.

    jfields: (alias, json_column, '$.dotted.path', 'value'|'type'). Uses SQLite
    JSON1 when available (json_valid-guarded), else parses in Python.
    The SQL prefilter is a rowid floor (see guard_id_floor) plus a lexical date
    window widened by a day on each side; the exact comparison is done on parsed
    datetimes, so '+00:00', 'Z' and other offsets are all handled correctly.
    guard_id_floor runs before any row is returned. If the id/timestamp order
    is inverted or unprovable, it exits 6 and the caller prints no counts.
    """
    lo = (start - timedelta(days=1)).strftime("%Y-%m-%d")
    hi = (end + timedelta(days=1)).strftime("%Y-%m-%d")
    use_json = json_funcs_available(conn)
    jcols = []
    for _alias, jcol, _path, _kind in jfields:
        if jcol not in jcols:
            jcols.append(jcol)
    select = [ts_col + " AS _ts_raw"]
    if "id" not in base_cols:
        select.append("id AS _row_id")
    select.extend(base_cols)
    if use_json:
        for alias, jcol, path, kind in jfields:
            func = "json_extract" if kind == "value" else "json_type"
            select.append("CASE WHEN json_valid(%s) THEN %s(%s, '%s') END AS %s" % (
                jcol, func, jcol, path, alias))
        select += ["json_valid(%s) AS _valid_%s" % (c, c) for c in jcols]
    else:
        select += ["%s AS _doc_%s" % (c, c) for c in jcols]
    floor, probes, predecessor = guard_id_floor(
        conn, table, ts_col, (start - timedelta(days=2)).strftime("%Y-%m-%d"),
        start, lo, hi)
    sql = "SELECT %s FROM %s WHERE %s >= ? AND %s < ?%s%s" % (
        ", ".join(select), table, ts_col, ts_col, " AND id >= ?" if floor is not None else "",
        (" AND (%s)" % where_extra) if where_extra else "")
    stats = {"prefiltered": 0, "kept": 0, "unparseable_ts": 0, "id_floor": floor, "probes": probes,
             "invalid_json": collections.Counter(), "json_mode": "sqlite_json1" if use_json else "python"}
    rows = []
    scanned_pairs = []
    params = (lo, hi, floor) if floor is not None else (lo, hi)
    for raw in conn.execute(sql, params).fetchall():
        stats["prefiltered"] += 1
        row_id = raw["id"] if "id" in raw.keys() else raw["_row_id"]
        stamp = parse_ts(raw["_ts_raw"])
        if row_id is None:
            fail_rowid_order()
        scanned_pairs.append((int(row_id), stamp))
        if stamp is None:
            stats["unparseable_ts"] += 1
            continue
        if not (start <= stamp < end):
            continue
        row = dict((c, raw[c]) for c in base_cols)
        row["_ts"] = stamp
        row["_ts_raw"] = raw["_ts_raw"]
        if use_json:
            for alias, _jcol, _path, _kind in jfields:
                row[alias] = raw[alias]
            for c in jcols:
                if not raw["_valid_%s" % c]:
                    stats["invalid_json"][c] += 1
        else:
            docs = {}
            for c in jcols:
                try:
                    docs[c] = json.loads(raw["_doc_%s" % c] or "")
                except (TypeError, ValueError):
                    docs[c] = None
                    stats["invalid_json"][c] += 1
            for alias, jcol, path, kind in jfields:
                present, value = _py_path(docs[jcol], path) if docs[jcol] is not None else (False, None)
                row[alias] = _py_type(present, value) if kind == "type" else _py_value(present, value)
        rows.append(row)
    assert_timestamps_nondecreasing(scanned_pairs, predecessor)
    stats["kept"] = len(rows)
    rows.sort(key=lambda r: (r["_ts"], r.get("id") or 0))
    return rows, stats


def stats_line(label, stats):
    out("%s rows_in_window=%d prefiltered=%d unparseable_ts=%d invalid_json=%s json_mode=%s "
        "id_floor=%s (proved: no in-window row has a smaller id)" % (
            label, stats["kept"], stats["prefiltered"], stats["unparseable_ts"],
            fmt_counts(stats["invalid_json"]) if stats["invalid_json"] else "0", stats["json_mode"],
            "-" if stats["id_floor"] is None else stats["id_floor"]))


def lookup_json_by_id(conn, table, ids, jfields):
    """{id: {alias: value}} for the given ids (chunked IN queries)."""
    use_json = json_funcs_available(conn)
    result = {}
    ids = sorted(set(int(i) for i in ids if i is not None))
    for offset in range(0, len(ids), 400):
        chunk = ids[offset:offset + 400]
        marks = ",".join("?" for _ in chunk)
        if use_json:
            select = ["id"] + ["CASE WHEN json_valid(%s) THEN %s(%s, '%s') END AS %s" % (
                jcol, "json_extract" if kind == "value" else "json_type", jcol, path, alias)
                for alias, jcol, path, kind in jfields]
            sql = "SELECT %s FROM %s WHERE id IN (%s)" % (", ".join(select), table, marks)
            for raw in conn.execute(sql, chunk).fetchall():
                result[int(raw["id"])] = dict((a, raw[a]) for a, _c, _p, _k in jfields)
        else:
            jcols = sorted(set(j[1] for j in jfields))
            sql = "SELECT id, %s FROM %s WHERE id IN (%s)" % (", ".join(jcols), table, marks)
            for raw in conn.execute(sql, chunk).fetchall():
                entry = {}
                for alias, jcol, path, kind in jfields:
                    try:
                        doc = json.loads(raw[jcol] or "")
                        present, value = _py_path(doc, path)
                    except (TypeError, ValueError):
                        present, value = False, None
                    entry[alias] = _py_type(present, value) if kind == "type" else _py_value(present, value)
                result[int(raw["id"])] = entry
    return result


def num(value):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None
# ===== END COMMON BLOCK v1 =====

_BUCKET_RULES = (
    (re.compile(r"^(.*planned_risk_outside_v1_cap):(.+)$"), "%s:*"),
    (re.compile(r"^(.*aggregate_risk_cap_exceeded):(.+)$"), "%s:*"),
    (re.compile(r"^(.*no_liquid_contract)\[(.*)\]$"), "%s[*]"),
    (re.compile(r"^(.*remaining_rr)_(-?[0-9.]+_below_[0-9.]+)$"), "%s_*"),
)


def bucket_of(reason):
    """(bucket, individual_value_or_None) for one suppression reason."""
    text = rdisp(reason)
    for pattern, fmt in _BUCKET_RULES:
        match = pattern.match(text)
        if match:
            return fmt % match.group(1), match.group(2)
    return text, None


def _reasons(rows):
    buckets = collections.OrderedDict()
    values = collections.OrderedDict()
    for r in rows:
        bucket, value = bucket_of(r["alert_suppression_reason"])
        b = buckets.setdefault(bucket, {"count": 0, "sent": 0, "first": r["_ts"], "last": r["_ts"],
                                        "tickers": collections.Counter()})
        b["count"] += 1
        b["sent"] += int(r["alert_sent"] or 0)
        b["first"] = min(b["first"], r["_ts"])
        b["last"] = max(b["last"], r["_ts"])
        b["tickers"][r["ticker"]] += 1
        if value is not None:
            v = values.setdefault((bucket, value), {"count": 0, "tickers": collections.Counter()})
            v["count"] += 1
            v["tickers"][r["ticker"]] += 1
    out("## section=reasons buckets=%d rows=%d" % (len(buckets), len(rows)))
    ordered = sorted(buckets.items(), key=lambda kv: (-kv[1]["count"], kv[0]))
    for bucket, b in ordered[:MAX_ROWS]:
        emit("REASON", ("bucket", bucket, 160), ("count", b["count"]), ("sent", b["sent"]),
             ("first_et", fmt_et(b["first"])), ("last_et", fmt_et(b["last"])),
             ("distinct_tickers", len(b["tickers"])), ("tickers", fmt_counts(b["tickers"]), 600))
    truncated_note("reasons", len(ordered), min(len(ordered), MAX_ROWS))
    out("## section=reason_values (individual values inside variable-suffix buckets) values=%d" % len(values))
    vordered = sorted(values.items(), key=lambda kv: (kv[0][0], -kv[1]["count"], kv[0][1]))
    for (bucket, value), v in vordered[:MAX_ROWS]:
        emit("REASON_VALUE", ("bucket", bucket, 160), ("value", value, 200), ("count", v["count"]),
             ("tickers", fmt_counts(v["tickers"]), 400))
    truncated_note("reason_values", len(vordered), min(len(vordered), MAX_ROWS))


def _dte(expiry, scan_ts):
    try:
        return (date.fromisoformat(str(expiry)[:10]) - scan_ts.astimezone(ET).date()).days
    except (TypeError, ValueError):
        return None


def _contract_detail(conn, tables, start, end):
    jf = [(k, "raw_json", "$." + k, "value") for k in (
        "contract", "option_ask", "option_bid", "option_mark", "delta", "dte", "expiry", "strike",
        "selector_evidence_id", "paper_policy_reason")]
    where = ("alert_suppression_reason LIKE '%planned_risk_outside_v1_cap%' "
             "OR alert_suppression_reason LIKE '%no_liquid_contract%'")
    rows, stats = select_window(conn, "scans", "timestamp",
                                ["id", "ticker", "direction", "alert_suppression_reason"],
                                jf, start, end, where_extra=where)
    evidence = {}
    if "options_selector_evidence" in tables:
        sjf = [("sel_status", "evidence_json", "$.status", "value"),
               ("sel_reason_code", "evidence_json", "$.reason_code", "value"),
               ("chosen_expiration", "evidence_json", "$.chosen_expiration", "value"),
               ("prod_status", "evidence_json", "$.production_selection.status", "value"),
               ("prod_reason", "evidence_json", "$.production_selection.reason", "value")]
        sjf += [("c_" + k, "evidence_json", "$.production_selection.contract." + k, "value")
                for k in ("symbol", "ask", "bid", "mid", "delta", "strike")]
        wanted = [num(r["selector_evidence_id"]) for r in rows[:MAX_ROWS]]
        evidence = lookup_json_by_id(conn, "options_selector_evidence",
                                     [int(w) for w in wanted if w is not None], sjf)
    else:
        out("NOTE: table options_selector_evidence not present; raw_json only")
    risk = [r for r in rows if "planned_risk_outside_v1_cap" in str(r["alert_suppression_reason"])]
    noliq = [r for r in rows if "no_liquid_contract" in str(r["alert_suppression_reason"])]
    out("## section=riskcap_rows (DATA_INVALID:planned_risk_outside_v1_cap:*) rows=%d" % len(risk))
    for r in risk[:MAX_ROWS]:
        amount = num(str(r["alert_suppression_reason"]).rsplit(":", 1)[-1])
        sid = num(r["selector_evidence_id"])
        ev = evidence.get(int(sid)) if sid is not None else None
        src = "raw" if r["contract"] else ("selector_evidence" if ev and ev.get("c_symbol") else "none")
        expiry = r["expiry"] or (ev or {}).get("chosen_expiration")
        dte = r["dte"] if r["dte"] is not None else _dte(expiry, r["_ts"])
        emit("RISKCAP", ("id", r["id"]), ("time_et", fmt_et(r["_ts"])), ("ticker", r["ticker"]),
             ("dir", r["direction"]), ("amount", amount),
             ("implied_ask_from_amount", round(amount / 25.0, 4) if amount is not None else None),
             ("contract", r["contract"] or (ev or {}).get("c_symbol")),
             ("ask", num(r["option_ask"]) if r["option_ask"] is not None else num((ev or {}).get("c_ask"))),
             ("bid", num(r["option_bid"]) if r["option_bid"] is not None else num((ev or {}).get("c_bid"))),
             ("entry_premium", num(r["option_mark"])),
             ("delta", num(r["delta"]) if r["delta"] is not None else num((ev or {}).get("c_delta"))),
             ("strike", num(r["strike"]) if r["strike"] is not None else num((ev or {}).get("c_strike"))),
             ("expiry", expiry), ("dte", dte),
             ("dte_src", "raw" if r["dte"] is not None else ("computed" if dte is not None else None)),
             ("src", src), ("sel_status", (ev or {}).get("sel_status")))
    truncated_note("riskcap_rows", len(risk), min(len(risk), MAX_ROWS))
    out("## section=noliq_rows (DATA_INVALID:no_liquid_contract[...]) rows=%d" % len(noliq))
    for r in noliq[:MAX_ROWS]:
        sid = num(r["selector_evidence_id"])
        ev = evidence.get(int(sid)) if sid is not None else {}
        ev = ev or {}
        detail = bucket_of(r["alert_suppression_reason"])[1]
        expiry = r["expiry"] or ev.get("chosen_expiration")
        emit("NOLIQ", ("id", r["id"]), ("time_et", fmt_et(r["_ts"])), ("ticker", r["ticker"]),
             ("dir", r["direction"]), ("detail", detail, 200), ("contract", r["contract"]),
             ("ask", num(r["option_ask"])), ("delta", num(r["delta"])),
             ("chosen_expiration", expiry), ("dte", r["dte"] if r["dte"] is not None else _dte(expiry, r["_ts"])),
             ("sel_status", ev.get("sel_status")), ("sel_reason", ev.get("prod_reason") or ev.get("sel_reason_code"), 160))
    truncated_note("noliq_rows", len(noliq), min(len(noliq), MAX_ROWS))


def _ticks(rows):
    out("## section=tick_integrity")
    sources = collections.Counter(str(r["source"]) for r in rows)
    for source, count in sorted(sources.items(), key=lambda kv: (-kv[1], kv[0])):
        out("SOURCE source=%s rows=%d" % (safe(source), count))
    all_ts = set(r["_ts_raw"] for r in rows)
    tick_rows = [r for r in rows if str(r["source"] or "").startswith("scheduled")]
    ticks = collections.OrderedDict()
    for r in tick_rows:
        t = ticks.setdefault(r["_ts_raw"], {"ts": r["_ts"], "tickers": collections.Counter()})
        t["tickers"][r["ticker"]] += 1
    ordered = sorted(ticks.values(), key=lambda t: t["ts"])
    symbols = sorted(set(r["ticker"] for r in tick_rows))
    spacing = sorted((b["ts"] - a["ts"]).total_seconds() for a, b in zip(ordered, ordered[1:]))
    median = spacing[len(spacing) // 2] if spacing else None
    out("TICKS distinct_scan_timestamps_all_sources=%d scheduled_ticks=%d first_tick_et=%s "
        "last_tick_et=%s median_spacing_s=%s symbols=%d" % (
            len(all_ts), len(ordered), fmt_et(ordered[0]["ts"]) if ordered else "-",
            fmt_et(ordered[-1]["ts"]) if ordered else "-",
            repr(median) if median is not None else "-", len(symbols)))
    out("WATCHLIST_DERIVED source=scheduled* symbols=%s" % safe(",".join(symbols), 2000))
    per_symbol = collections.Counter()
    rows_per_symbol = collections.Counter()
    for t in ordered:
        for ticker, n in t["tickers"].items():
            per_symbol[ticker] += 1
            rows_per_symbol[ticker] += n
    for ticker in symbols[:MAX_ROWS]:
        emit("SYMBOL_TICKS", ("ticker", ticker), ("ticks", per_symbol[ticker]),
             ("rows", rows_per_symbol[ticker]), ("missing_ticks", len(ordered) - per_symbol[ticker]))
    truncated_note("symbol_ticks", len(symbols), min(len(symbols), MAX_ROWS))
    if symbols:
        lo, hi = min(per_symbol.values()), max(per_symbol.values())
        out("SYMBOL_TICKS_SUMMARY min=%d min_symbols=%s max=%d max_symbols=%s" % (
            lo, safe(",".join(s for s in symbols if per_symbol[s] == lo), 300),
            hi, safe(",".join(s for s in symbols if per_symbol[s] == hi), 300)))
    incomplete = [t for t in ordered if len(t["tickers"]) < len(symbols)]
    out("TOTAL ticks_incomplete=%d of %d" % (len(incomplete), len(ordered)))
    for t in incomplete[:MAX_ROWS]:
        missing = [s for s in symbols if s not in t["tickers"]]
        shown = ",".join(missing[:25]) + (",+%d_more" % (len(missing) - 25) if len(missing) > 25 else "")
        emit("TICK_INCOMPLETE", ("time_et", fmt_et(t["ts"])), ("present", "%d/%d" % (len(t["tickers"]), len(symbols))),
             ("missing", shown, 400))
    truncated_note("tick_incomplete", len(incomplete), min(len(incomplete), MAX_ROWS))
    gaps = []
    if median:
        for a, b in zip(ordered, ordered[1:]):
            gap = (b["ts"] - a["ts"]).total_seconds()
            if gap > 1.5 * median:
                gaps.append((a["ts"], b["ts"], gap))
    out("TOTAL tick_gaps_over_1.5x_median=%d" % len(gaps))
    for a, b, gap in gaps[:50]:
        emit("TICK_GAP", ("after_et", fmt_et(a)), ("before_et", fmt_et(b)), ("gap_s", gap))
    truncated_note("tick_gaps", len(gaps), min(len(gaps), 50))


def main(now=None):
    now = now or datetime.now(UTC)
    et_date, start, end = et_day_bounds(now)
    print_header(SCRIPT, VERSION, now, et_date, start, end)
    conn, ctx = open_db_readonly(DB_PATH, ALLOW_IMMUTABLE_FALLBACK)
    tables = require_tables(conn, ["scans"])
    rows, stats = select_window(conn, "scans", "timestamp",
                                ["id", "source", "ticker", "alert_sent", "alert_suppression_reason"],
                                [], start, end)
    stats_line("SCANS_TODAY", stats)
    _reasons(rows)
    _contract_detail(conn, tables, start, end)
    _ticks(rows)
    close_db_readonly(conn, ctx)
    return finish(0)


if __name__ == "__main__":
    run(main)
