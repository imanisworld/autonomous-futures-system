#!/usr/bin/env python3
"""scanner-alerts-today -- READ-ONLY audit of today's options-scanner alerts.

Forced-command name: scanner-alerts-today   (no arguments; sys.argv is ignored)

PURPOSE
  Show every `scans` row for today (America/New_York calendar day) that was
  alert-relevant -- alert_sent=1, or suppression reason ''/NULL, or reason
  'duplicate_30m', or reason LIKE 'discord%' -- then totals, per-setup re-alert
  timing, and the options_shadow_journal rows that are currently OPEN.

READ-ONLY GUARANTEES
  * Opens SQLite only via sqlite3.connect('file:<db>?mode=ro', uri=True) plus
    PRAGMA query_only=ON. Never writes, never creates files or temp files.
  * WAL DB with no -wal/-shm sidecars: fail closed (exit 3, ERROR,
    RESULT_INCOMPLETE=true). ALLOW_IMMUTABLE_FALLBACK is False. Never a
    best-effort immutable=1 read.
  * Reads no environment variables, no .env, no config; prints no webhook URLs,
    tokens or account ids. Only the whitelisted fields below are printed, each
    value truncated and masked if it looks secret-bearing.
  * Missing/unreadable DB or missing `scans` table => 'ERROR:' line, exit != 0.

FIELDS PRINTED
  ALERT      id time_et utc ticker dir pattern score source sent reason lane
             setup_status status policy_status contract setup_type setup_tf
             (lane = raw_json.paper_evidence_lane, else raw_json.lane)
  TOTAL      alerts_sent_today duplicate_30m_today discord_errors_today (+split)
             empty/null-reason counts, alerts_sent_since_<SINCE_ET_DATE>
  SENT_BY_DAY et_date sent             (every ET day since SINCE_ET_DATE)
  GROUP      ticker dir pattern sends dup30 discord first/last send, gaps (min)
  OPEN       options_shadow_journal status='OPEN': id opened_et ticker dir
             pattern lane contract planned_risk entry_premium dte expiry
             setup_type setup_tf scan_id
  JOURNAL_TODAY status histogram of journal rows opened today

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
SINCE_ET_DATE = "2026-09-08"
ALLOW_IMMUTABLE_FALLBACK = False
SCRIPT = "scanner-alerts-today"
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


def _interesting(row):
    reason = row["alert_suppression_reason"]
    if int(row["alert_sent"] or 0) == 1:
        return True
    if reason is None or str(reason).strip() == "":
        return True
    reason = str(reason)
    return reason == "duplicate_30m" or reason.startswith("discord")


def _lane_of(selected, setup_inputs):
    lane = selected.get("paper_evidence_lane") or setup_inputs.get("paper_evidence_lane")
    if lane == "COUNTERFACTUAL" or selected.get("risk_budget_consumed") is False:
        return "COUNTERFACTUAL"
    return str(lane or "ACTIVE")


def _alerts_section(conn, start, end):
    jf = [
        ("lane", "raw_json", "$.paper_evidence_lane", "value"),
        ("lane_alt", "raw_json", "$.lane", "value"),
        ("setup_status", "raw_json", "$.setup_status", "value"),
        ("status", "raw_json", "$.status", "value"),
        ("policy_status", "raw_json", "$.paper_policy_status", "value"),
        ("contract", "raw_json", "$.contract", "value"),
        ("setup_type", "raw_json", "$.setup_type", "value"),
        ("setup_tf", "raw_json", "$.setup_timeframe", "value"),
    ]
    base = ["id", "source", "ticker", "direction", "score", "pattern", "alert_sent",
            "alert_suppression_reason"]
    rows, stats = select_window(conn, "scans", "timestamp", base, jf, start, end)
    stats_line("SCANS_TODAY", stats)
    picked = [r for r in rows if _interesting(r)]
    out("## section=alert_rows (alert_sent=1 OR reason ''/NULL OR duplicate_30m OR discord%%) rows=%d" % len(picked))
    for r in picked[:MAX_ROWS]:
        emit("ALERT", ("id", r["id"]), ("time_et", fmt_et(r["_ts"])), ("utc", fmt_hms_utc(r["_ts"])),
             ("ticker", r["ticker"]), ("dir", r["direction"]), ("pattern", r["pattern"]),
             ("score", r["score"]), ("source", r["source"]), ("sent", int(r["alert_sent"] or 0)),
             ("reason", rdisp(r["alert_suppression_reason"]), 120),
             ("lane", r["lane"] or r["lane_alt"]), ("setup_status", r["setup_status"]),
             ("status", r["status"]), ("policy_status", r["policy_status"]),
             ("contract", r["contract"]), ("setup_type", r["setup_type"]), ("setup_tf", r["setup_tf"]))
    truncated_note("alert_rows", len(picked), min(len(picked), MAX_ROWS))
    return rows, picked


def _totals(conn, rows, et_date):
    reasons = [r["alert_suppression_reason"] for r in rows]
    sent = sum(1 for r in rows if int(r["alert_sent"] or 0) == 1)
    dup = sum(1 for x in reasons if x == "duplicate_30m")
    d_err = sum(1 for x in reasons if x == "discord_error")
    d_nc = sum(1 for x in reasons if x == "discord_not_configured")
    d_all = sum(1 for x in reasons if x is not None and str(x).startswith("discord"))
    null_r = sum(1 for x in reasons if x is None)
    empty_r = sum(1 for x in reasons if x is not None and str(x).strip() == "")
    empty_unsent = sum(1 for r in rows if int(r["alert_sent"] or 0) == 0
                       and (r["alert_suppression_reason"] is None
                            or str(r["alert_suppression_reason"]).strip() == ""))
    sent_with_reason = sum(1 for r in rows if int(r["alert_sent"] or 0) == 1
                           and str(r["alert_suppression_reason"] or "").strip() != "")
    out("## section=totals")
    out("TOTAL rows_today=%d" % len(rows))
    out("TOTAL alerts_sent_today=%d" % sent)
    out("TOTAL duplicate_30m_today=%d" % dup)
    out("TOTAL discord_errors_today=%d discord_error=%d discord_not_configured=%d discord_other=%d" % (
        d_all, d_err, d_nc, d_all - d_err - d_nc))
    out("TOTAL reason_null_today=%d reason_empty_today=%d" % (null_r, empty_r))
    out("TOTAL anomaly_empty_or_null_reason_but_not_sent=%d" % empty_unsent)
    out("TOTAL anomaly_sent_with_nonempty_reason=%d" % sent_with_reason)

    since = date.fromisoformat(SINCE_ET_DATE)
    since_utc = et_midnight_utc(since)
    lo = (since_utc - timedelta(days=1)).strftime("%Y-%m-%d")
    per_day = collections.Counter()
    unparseable = 0
    floor, _probes, predecessor = guard_id_floor(
        conn, "scans", "timestamp",
        (since_utc - timedelta(days=2)).strftime("%Y-%m-%d"),
        since_utc, lo, None)
    sent_pairs = []
    for row in conn.execute(
            "SELECT id, timestamp FROM scans WHERE id >= ? AND alert_sent = 1 "
            "AND timestamp >= ? ORDER BY id",
            (floor or 0, lo)).fetchall():
        stamp = parse_ts(row[1])
        sent_pairs.append((int(row[0]), stamp))
        if stamp is None:
            unparseable += 1
            continue
        if stamp >= since_utc:
            per_day[stamp.astimezone(ET).date()] += 1
    assert_timestamps_nondecreasing(sent_pairs, predecessor)
    out("TOTAL alerts_sent_since_%s=%d (ET days %s..%s inclusive; unparseable_ts=%d)" % (
        SINCE_ET_DATE, sum(v for k, v in per_day.items() if k <= et_date), SINCE_ET_DATE,
        et_date.isoformat(), unparseable))
    day = since
    while day <= et_date:
        out("SENT_BY_DAY et_date=%s sent=%d" % (day.isoformat(), per_day.get(day, 0)))
        day += timedelta(days=1)
    later = sum(v for k, v in per_day.items() if k > et_date)
    if later:
        out("WARNING: alerts_sent_with_future_et_date=%d (not counted above)" % later)


def _groups(picked):
    groups = collections.OrderedDict()
    for r in picked:
        key = (r["ticker"], r["direction"], r["pattern"])
        g = groups.setdefault(key, {"sends": [], "dup": 0, "discord": 0, "other": 0})
        reason = r["alert_suppression_reason"]
        if int(r["alert_sent"] or 0) == 1:
            g["sends"].append(r["_ts"])
        elif reason == "duplicate_30m":
            g["dup"] += 1
        elif reason is not None and str(reason).startswith("discord"):
            g["discord"] += 1
        else:
            g["other"] += 1
    out("## section=realert_groups (distinct ticker,dir,pattern among alert rows) groups=%d" % len(groups))
    ordered = sorted(groups.items(), key=lambda kv: (
        kv[1]["sends"][0] if kv[1]["sends"] else datetime.max.replace(tzinfo=UTC), str(kv[0])))
    max_sends = 0
    for (ticker, direction, pattern), g in ordered[:MAX_ROWS]:
        sends = sorted(g["sends"])
        max_sends = max(max_sends, len(sends))
        gaps = [round((b - a).total_seconds() / 60.0, 1) for a, b in zip(sends, sends[1:])]
        emit("GROUP", ("ticker", ticker), ("dir", direction), ("pattern", pattern),
             ("sends", len(sends)), ("dup30", g["dup"]), ("discord", g["discord"]),
             ("other_unsent", g["other"]),
             ("first_send_et", fmt_et(sends[0]) if sends else None),
             ("last_send_et", fmt_et(sends[-1]) if sends else None),
             ("min_gap_min", min(gaps) if gaps else None),
             ("max_gap_min", max(gaps) if gaps else None),
             ("gaps_min", ",".join(repr(x) for x in gaps[:30]) + (",..." if len(gaps) > 30 else "")
              if gaps else None, 400))
    truncated_note("realert_groups", len(ordered), min(len(ordered), MAX_ROWS))
    out("TOTAL max_sends_single_setup_today=%d" % max_sends)


def _open_journal(conn, tables, start, end):
    out("## section=open_shadow_journal (options_shadow_journal.status='OPEN')")
    if "options_shadow_journal" not in tables:
        out("NOTE: table options_shadow_journal not present")
        return
    rows = conn.execute(
        "SELECT id, timestamp, scan_id, ticker, direction, pattern, status, "
        "selected_contract_json, setup_inputs_json FROM options_shadow_journal "
        "WHERE status = 'OPEN' ORDER BY id").fetchall()
    active = cf = missing_risk = 0
    active_risk = 0.0
    for idx, r in enumerate(rows):
        try:
            selected = json.loads(r["selected_contract_json"] or "{}")
        except ValueError:
            selected = {}
        try:
            inputs = json.loads(r["setup_inputs_json"] or "{}")
        except ValueError:
            inputs = {}
        if not isinstance(selected, dict):
            selected = {}
        if not isinstance(inputs, dict):
            inputs = {}
        lane = _lane_of(selected, inputs)
        risk = num(selected.get("planned_risk_dollars"))
        if lane == "COUNTERFACTUAL":
            cf += 1
        else:
            active += 1
            if selected.get("paper_policy_id") == "OPTIONS_PAPER_V1":
                if risk is None:
                    missing_risk += 1
                else:
                    active_risk += risk
        if idx < MAX_ROWS:
            emit("OPEN", ("id", r["id"]), ("opened_et", fmt_et(parse_ts(r["timestamp"]))),
                 ("ticker", r["ticker"]), ("dir", r["direction"]), ("pattern", r["pattern"]),
                 ("lane", lane), ("contract", selected.get("contract")),
                 ("planned_risk", risk), ("entry_premium", num(selected.get("option_mark"))),
                 ("dte", selected.get("dte")), ("expiry", selected.get("expiry")),
                 ("setup_type", selected.get("setup_type") or inputs.get("setup_type")),
                 ("setup_tf", selected.get("setup_timeframe") or inputs.get("setup_timeframe")),
                 ("scan_id", r["scan_id"]))
    truncated_note("open_shadow_journal", len(rows), min(len(rows), MAX_ROWS))
    out("TOTAL open_total=%d open_active=%d open_counterfactual=%d "
        "open_active_v1_planned_risk_sum=%s open_active_v1_missing_risk=%d (cap 1000.0)" % (
            len(rows), active, cf, repr(round(active_risk, 2)), missing_risk))
    today, stats = select_window(conn, "options_shadow_journal", "timestamp", ["id", "status"], [], start, end)
    hist = collections.Counter(str(r["status"]) for r in today)
    for status, count in sorted(hist.items()):
        out("JOURNAL_TODAY status=%s rows=%d" % (safe(status), count))
    out("JOURNAL_TODAY total=%d unparseable_ts=%d" % (len(today), stats["unparseable_ts"]))


def main(now=None):
    now = now or datetime.now(UTC)
    et_date, start, end = et_day_bounds(now)
    print_header(SCRIPT, VERSION, now, et_date, start, end)
    conn, ctx = open_db_readonly(DB_PATH, ALLOW_IMMUTABLE_FALLBACK)
    tables = require_tables(conn, ["scans"])
    rows, picked = _alerts_section(conn, start, end)
    _totals(conn, rows, et_date)
    _groups(picked)
    _open_journal(conn, tables, start, end)
    close_db_readonly(conn, ctx)
    return finish(0)


if __name__ == "__main__":
    run(main)
