#!/usr/bin/env python3
"""scanner-script-drift -- READ-ONLY drift check of the out-of-release ops scripts.

Forced-command name: scanner-script-drift   (no arguments; argv ignored)

PURPOSE
  For the scripts that run OUTSIDE the deployed release (/root/afs-shared/*.py),
  their repo-worktree copies and the deployed-release copies: exists, symlink
  target, size, mtime ET, sha256 and git blob sha1 (sha1(b"blob <len>\\0"+data),
  directly comparable with GitHub blob SHAs / `git hash-object`), plus which
  known GitHub blob it matches (embedded KNOWN_BLOBS; see REFERENCE.txt).
  Then: feed_gap_alarm.py watched-symbol lines, the last 40 lines of
  options_daily_pnl.log, and scalar top-level values of the daily P&L JSONs.

READ-ONLY GUARANTEES
  Only os.stat/os.lstat/os.readlink/os.listdir and open(..., "rb"). No writes,
  no child processes, no git invocation (HEAD is read from .git/HEAD + refs +
  packed-refs; .git/config is never read), no env reads, no network. Target
  files are hashed and read as bytes only. They are never imported, executed,
  or spawned.
  Secret hygiene: feed_gap_alarm.py lines containing KEY/SECRET/TOKEN/WEBHOOK/
  PASSWORD (any case) or URLs are skipped (counted, not shown); log lines
  containing a URL, webhook, or token (or other secret-looking text) are
  replaced by '<masked line>'; JSON strings are printed only for whitelisted
  keys.

FIELDS PRINTED
  PATHS        constants and realpath of RELEASE_DIR
  REPO_HEAD    worktree HEAD ref/commit (from .git files)
  FILE         group name path exists type realpath size mtime_et sha256
               git_blob_sha1 known_as
  DRIFT        per script: blob of each copy + verdict
  FEEDGAP|     <copy> <lineno>: <line, max 200 chars>   (max 20 per distinct copy)
  LOG|         last 40 lines of options_daily_pnl.log (max 300 chars each)
  PNL_JSON     file exists size mtime_et ; JSON_KV key=value (max 40 scalars)

EXIT CODES 0 ok | 2 base directory missing | 3 permission/read error | 1 unexpected
  This script does not open the scanner DB. The shared block still
  contains guard_id_floor (exit 6) for the three DB scripts: a
  rowid/timestamp inversion or an unprovable floor fails closed
  with exactly ERROR: ROWID_TIMESTAMP_ORDER_NOT_MONOTONIC and
  RESULT_INCOMPLETE=true, and never emits partial counts. A long
  prefix scan is refused because rollback-journal readers block
  writer commits. ALLOW_IMMUTABLE_FALLBACK is False.
"""
# ---- path constants (the ONLY configuration; tests patch the ROOT line) ----
ROOT = "/root"
# Drift does not open SQLite. The constant is still fixed False so every
# audit script hard-disables the immutable fallback.
ALLOW_IMMUTABLE_FALLBACK = False
SHARED_DIR = ROOT + "/afs-shared"
LOG_DIR = SHARED_DIR + "/logs"
REPO_DIR = ROOT + "/autonomous-futures-system"
DEPLOYED_COMMIT = "47ae01acdbedd544d4a72abbf63dc933d506d2c4"
RELEASE_DIR = ROOT + "/afs-releases/" + DEPLOYED_COMMIT
PNL_LOG = LOG_DIR + "/options_daily_pnl.log"
PNL_JSON_LATEST = LOG_DIR + "/options_daily_pnl_latest.json"
PNL_JSON_DAY_FMT = LOG_DIR + "/options_daily_pnl_%s.json"   # %s = today's ET date
SCRIPT = "scanner-script-drift"
VERSION = "2"

# Scripts that run from /root/afs-shared and their path inside the repo.
DRIFT_SCRIPTS = (
    ("options_daily_pnl_report.py", "ops/options_daily_pnl_report.py"),
    ("shadow_daily_pnl_report.py", "ops/shadow_daily_pnl_report.py"),
    ("feed_gap_alarm.py", "ops/feed_gap_alarm.py"),
)
EXTRA_REPO_FILES = ("scripts/feed_gap_alarm.py",)
RELEASE_MODULES = (
    "alert_ranker/storage.py", "alert_ranker/discord.py", "alert_ranker/scanner_legacy.py",
    "alert_ranker/paper_v1.py", "alert_ranker/multisetup_scanner.py",
    "alert_ranker/signa_context_store.py", "sources/signa_client.py",
    "sources/signa_snapshot_store.py", "notifications/discord_notifier.py",
)
# GitHub blob SHAs (imanisworld/autonomous-futures-system), fetched 2026-09-24.
# main = 34177183d2a2abbb2442b4bd2dee3f6579875140 ; deployed = 47ae01ac...
KNOWN_BLOBS = {
    "786c8adc9d03c4368573b9369edd5d3899ffd014": "ops/feed_gap_alarm.py@main(799a89e2,#932)",
    "52d6efbe0d358532982d1b1f7900f157ec408ec7": "ops/feed_gap_alarm.py@3a3d4259(#927,superseded)",
    "06beb9e6fc22498555271c153ebefce345cdcb0b": "ops/feed_gap_alarm.py@47ae01ac(67961486,#297)",
    "0fc061fa537f0c9f305c49602a7a0b9a6dc36403": "scripts/feed_gap_alarm.py@main+47ae01ac",
    "571c370d9f50e218fae36050f21ca6f3b5b6fa8c": "ops/options_daily_pnl_report.py@main(6c8c6792,#972)",
    "bcec93bd4a7ddf88936c16efdd080e54efe4317e": "ops/shadow_daily_pnl_report.py@main(032c4781,#971)",
    "ff0455b7c3d51d859ca34ca369c1b81e010af8e5": "alert_ranker/storage.py@main+47ae01ac",
    "4898638d2d2e1718a0f94b058d42be23e8bc8f19": "alert_ranker/discord.py@main+47ae01ac",
    "e12b6fe689b391d7de0fd16a211ccbaf81df3d62": "alert_ranker/scanner_legacy.py@main+47ae01ac",
    "c800d193c0c859b9dfe065d0caa6e0fc092f0133": "alert_ranker/paper_v1.py@main+47ae01ac",
    "1c8b02aa8732b84131c3e1fc994491ac8dc6e41f": "alert_ranker/multisetup_scanner.py@main+47ae01ac",
    "684d845ab42a55441d160264c271a4db87e7e63e": "alert_ranker/signa_context_store.py@main+47ae01ac",
    "fba3018fd82629239adaa5b251823c135b45f447": "sources/signa_client.py@main+47ae01ac",
    "ec73adfe8b0240ded8f9126b8556c29584027b16": "sources/signa_snapshot_store.py@main+47ae01ac",
    "c3fe26529f3313d648a50a135c7b951f33baa9fb": "notifications/discord_notifier.py@47ae01ac",
    "6145d7e587257629bdb196f1ad6deaa2786442a4": "notifications/discord_notifier.py@main",
}
JSON_STRING_KEYS = ("day", "generated_at", "cost_model", "authority")
JSON_STRING_SUFFIXES = (".status", ".day", ".date", ".mode")
LOG_TAIL_LINES = 40
FEEDGAP_MAX_LINES = 20

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

FEEDGAP_MATCH = re.compile(r"MNQ|MES|INSTRUMENT|[Ss][Yy][Mm][Bb][Oo][Ll]")
FEEDGAP_SKIP = re.compile(r"(?i)KEY|SECRET|TOKEN|WEBHOOK|PASSWORD|https?://")
# '(?:ht)tp' matches the letters of a URL scheme without spelling that token here.
LOG_MASK = re.compile(r"(?i)webhook|token|(?:ht)tp")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_PERM_ERRORS = []


def _clean_line(text, limit):
    text = _CTRL.sub("?", text.rstrip("\r\n"))
    if len(text) > limit:
        text = "%s...(+%d)" % (text[:limit], len(text) - limit)
    return text


def _perm(path, exc):
    _PERM_ERRORS.append(path)
    out("WARNING: cannot_read path=%s error=%s" % (path, type(exc).__name__))


def git_blob_sha1(data):
    return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()


def hash_file(path):
    """(size, sha256, git_blob_sha1) streaming; the blob header needs the size first."""
    with open(path, "rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        h256 = hashlib.sha256()
        h1 = hashlib.sha1(b"blob %d\x00" % size)
        read = 0
        while True:
            chunk = handle.read(1 << 20)
            if not chunk:
                break
            read += len(chunk)
            h256.update(chunk)
            h1.update(chunk)
    if read != size:
        raise OSError("file size changed while hashing")
    return size, h256.hexdigest(), h1.hexdigest()


def file_record(group, name, path):
    rec = {"group": group, "name": name, "path": path, "blob": None, "exists": False}
    try:
        os.lstat(path)
    except FileNotFoundError:
        emit("FILE", ("group", group), ("name", name), ("path", path, 200), ("exists", "no"))
        return rec
    except PermissionError as exc:
        _perm(path, exc)
        emit("FILE", ("group", group), ("name", name), ("path", path, 200), ("exists", "unknown_permission_denied"))
        return rec
    ftype = "symlink" if os.path.islink(path) else ("file" if os.path.isfile(path) else "other")
    real = os.path.realpath(path)
    try:
        st = os.stat(path)
        size, s256, blob = hash_file(path) if os.path.isfile(path) else (st.st_size, None, None)
    except FileNotFoundError:
        emit("FILE", ("group", group), ("name", name), ("path", path, 200), ("exists", "dangling_symlink"),
             ("realpath", real, 200))
        return rec
    except (PermissionError, OSError) as exc:
        _perm(path, exc)
        emit("FILE", ("group", group), ("name", name), ("path", path, 200), ("exists", "yes"),
             ("type", ftype), ("readable", "no"))
        return rec
    rec.update(exists=True, blob=blob)
    emit("FILE", ("group", group), ("name", name), ("path", path, 200), ("exists", "yes"),
         ("type", ftype), ("realpath", real if real != path else "=", 200), ("size", size),
         ("mtime_et", fmt_mtime(st)),
         ("sha256", s256, 64), ("git_blob_sha1", blob, 40),
         ("known_as", KNOWN_BLOBS.get(blob, "unknown") if blob else None, 120))
    return rec


def _read_small(path, limit=65536):
    with open(path, "rb") as handle:
        return handle.read(limit).decode("utf-8", "replace")


def repo_head(repo):
    """HEAD from .git/HEAD, refs and packed-refs only (never .git/config)."""
    gitdir = os.path.join(repo, ".git")
    try:
        if os.path.isfile(gitdir):                     # linked worktree: 'gitdir: <path>'
            line = _read_small(gitdir).strip()
            if line.startswith("gitdir:"):
                gitdir = os.path.normpath(os.path.join(repo, line[7:].strip()))
        if not os.path.isdir(gitdir):
            out("REPO_HEAD repo=%s git_dir=absent" % repo)
            return
        common = gitdir
        if os.path.isfile(os.path.join(gitdir, "commondir")):
            common = os.path.normpath(os.path.join(gitdir, _read_small(os.path.join(gitdir, "commondir")).strip()))
        head = _read_small(os.path.join(gitdir, "HEAD")).strip()
        ref, commit = None, None
        if head.startswith("ref:"):
            ref = head[4:].strip()
            for base in (gitdir, common):
                candidate = os.path.join(base, *ref.split("/"))
                if os.path.isfile(candidate):
                    commit = _read_small(candidate).strip()
                    break
            if commit is None:
                packed = os.path.join(common, "packed-refs")
                if os.path.isfile(packed):
                    for line in _read_small(packed, 8 << 20).splitlines():
                        parts = line.split()
                        if len(parts) == 2 and parts[1] == ref:
                            commit = parts[0]
        else:
            commit = head
        commit_ok = bool(commit) and re.fullmatch(r"[0-9a-f]{40}", commit) is not None
        emit("REPO_HEAD", ("repo", repo, 200), ("ref", ref, 120),
             ("commit", commit if commit_ok else None, 40),
             ("equals_deployed", "yes" if commit == DEPLOYED_COMMIT else "no"),
             ("note", "worktree_dirty_state_not_checked"))
    except (PermissionError, OSError) as exc:
        _perm(gitdir, exc)


def feedgap_lines(label, path):
    out("## feed_gap_alarm watched-symbol lines copy=%s path=%s" % (label, path))
    try:
        text = open(path, "rb").read().decode("utf-8", "replace")
    except (PermissionError, OSError) as exc:
        _perm(path, exc)
        return
    shown = skipped = matched = 0
    for lineno, line in enumerate(text.splitlines(), 1):
        if not FEEDGAP_MATCH.search(line):
            continue
        matched += 1
        if FEEDGAP_SKIP.search(line):
            skipped += 1
            continue
        if shown < FEEDGAP_MAX_LINES:
            out("FEEDGAP| %s %d: %s" % (label, lineno, _clean_line(line, 200)))
            shown += 1
    out("FEEDGAP_SUMMARY copy=%s matched=%d shown=%d skipped_secretish=%d truncated=%d "
        "mentions_word_option=%s" % (label, matched, shown, skipped, max(0, matched - skipped - shown),
                                 "yes" if re.search(r"(?i)option", text) else "no"))


def tail_log(path, count):
    out("## section=pnl_log path=%s" % path)
    try:
        st = os.stat(path)
    except FileNotFoundError:
        out("PNL_LOG exists=no")
        return
    except PermissionError as exc:
        _perm(path, exc)
        return
    out("PNL_LOG exists=yes size=%d mtime_et=%s" % (st.st_size, fmt_mtime(st)))
    try:
        with open(path, "rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            block = min(size, 256 * 1024)
            handle.seek(size - block)
            data = handle.read(block)
    except (PermissionError, OSError) as exc:
        _perm(path, exc)
        return
    lines = data.decode("utf-8", "replace").splitlines()
    if block < size and lines:
        lines = lines[1:]                               # first line may be partial
    masked = 0
    for line in lines[-count:]:
        if LOG_MASK.search(line) or _SECRETISH.search(line):
            masked += 1
            out("LOG| <masked line>")
        else:
            out("LOG| %s" % _clean_line(line, 300))
    out("PNL_LOG_TAIL lines_shown=%d masked=%d" % (min(count, len(lines)), masked))


def _flatten(prefix, value, items):
    if isinstance(value, dict):
        for key in sorted(value, key=str):
            _flatten("%s.%s" % (prefix, key) if prefix else str(key), value[key], items)
    else:
        items.append((prefix, value))


def pnl_json(label, path):
    try:
        st = os.stat(path)
    except FileNotFoundError:
        out("PNL_JSON file=%s exists=no path=%s" % (label, path))
        return
    except PermissionError as exc:
        _perm(path, exc)
        return
    out("PNL_JSON file=%s exists=yes size=%d mtime_et=%s path=%s" % (label, st.st_size, fmt_mtime(st), path))
    try:
        doc = json.loads(open(path, "rb").read(16 << 20).decode("utf-8", "replace"))
    except (PermissionError, OSError) as exc:
        _perm(path, exc)
        return
    except ValueError:
        out("PNL_JSON file=%s parse_error=yes" % label)
        return
    if not isinstance(doc, dict):
        out("PNL_JSON file=%s top_level=%s" % (label, type(doc).__name__))
        return
    items = []
    _flatten("", doc, items)
    shown = skipped = 0
    for key, value in items:
        if _SECRETISH.search(key):
            skipped += 1
            continue
        if shown >= 40:
            break
        if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
            text = safe(value, 40)
        elif isinstance(value, str):
            ok = key in JSON_STRING_KEYS or key.endswith(JSON_STRING_SUFFIXES)
            text = safe(value, 80) if ok else "<string omitted>"
        elif isinstance(value, list):
            text = "<list len=%d>" % len(value)
        else:
            text = "<%s>" % type(value).__name__
        out("JSON_KV file=%s %s=%s" % (label, safe(key, 80), text))
        shown += 1
    truncated_note("json_kv_" + label, len(items) - skipped, shown)
    if skipped:
        out("JSON_KV file=%s skipped_secretish_keys=%d" % (label, skipped))


def _recent_daily_jsons():
    try:
        names = [n for n in os.listdir(LOG_DIR) if re.fullmatch(r"options_daily_pnl_\d{4}-\d{2}-\d{2}\.json", n)]
    except (PermissionError, OSError) as exc:
        _perm(LOG_DIR, exc)
        return
    names.sort()
    out("PNL_JSON_DAILY_FILES count=%d newest=%s" % (len(names), ",".join(names[-5:]) or "-"))


def main(now=None):
    now = now or datetime.now(UTC)
    et_date, start, end = et_day_bounds(now)
    print_header(SCRIPT, VERSION, now, et_date, start, end)
    if not os.path.isdir(SHARED_DIR):
        fail(2, "shared directory not found (or not traversable): %s" % SHARED_DIR)
    release_real = os.path.realpath(RELEASE_DIR)
    out("## section=paths")
    out("PATHS shared_dir=%s repo_dir=%s log_dir=%s" % (SHARED_DIR, REPO_DIR, LOG_DIR))
    out("PATHS release_dir_constant=%s release_dir_realpath=%s release_dir_exists=%s deployed_commit=%s" % (
        RELEASE_DIR, release_real, "yes" if os.path.isdir(release_real) else "no", DEPLOYED_COMMIT))
    repo_head(REPO_DIR)
    repo_head(release_real)

    out("## section=files")
    records = {}
    for name, repo_rel in DRIFT_SCRIPTS:
        records[(name, "shared")] = file_record("SHARED", name, os.path.join(SHARED_DIR, name))
        records[(name, "repo")] = file_record("REPO", name, os.path.join(REPO_DIR, repo_rel))
        records[(name, "release")] = file_record("RELEASE", name, os.path.join(release_real, repo_rel))
    for rel in EXTRA_REPO_FILES:
        file_record("REPO", rel, os.path.join(REPO_DIR, rel))
        file_record("RELEASE", rel, os.path.join(release_real, rel))
    for rel in RELEASE_MODULES:
        file_record("RELEASE", rel, os.path.join(release_real, rel))

    out("## section=drift")
    main_labels = dict((sha, label) for sha, label in KNOWN_BLOBS.items() if "@main" in label)
    for name, repo_rel in DRIFT_SCRIPTS:
        blobs = dict((g, records[(name, g)]["blob"]) for g in ("shared", "repo", "release"))
        main_blob = [s for s, lab in main_labels.items() if lab.startswith(repo_rel + "@")]
        main_blob = main_blob[0] if main_blob else None
        present = [b for b in blobs.values() if b]
        if not blobs["shared"]:
            verdict = "shared_copy_missing_or_unreadable"
        elif blobs["shared"] == main_blob:
            verdict = "shared_matches_github_main"
        elif blobs["shared"] in KNOWN_BLOBS:
            verdict = "shared_matches_older_known_blob"
        else:
            verdict = "shared_UNKNOWN_blob_(local_edit_or_newer)"
        emit("DRIFT", ("name", name), ("shared", blobs["shared"], 40), ("repo_worktree", blobs["repo"], 40),
             ("release", blobs["release"], 40), ("github_main", main_blob, 40),
             ("all_present_copies_identical", "yes" if present and len(set(present)) == 1 else "no"),
             ("verdict", verdict, 60))

    distinct = collections.OrderedDict()
    for group in ("shared", "repo", "release"):
        rec = records[("feed_gap_alarm.py", group)]
        if rec["blob"] and rec["blob"] not in distinct:
            distinct[rec["blob"]] = (group, rec["path"])
    for blob, (group, path) in distinct.items():
        feedgap_lines(group, path)
    if not distinct:
        out("FEEDGAP_SUMMARY no_readable_copy=yes")

    tail_log(PNL_LOG, LOG_TAIL_LINES)
    out("## section=pnl_json")
    _recent_daily_jsons()
    pnl_json("latest", PNL_JSON_LATEST)
    pnl_json("today_" + et_date.isoformat(), PNL_JSON_DAY_FMT % et_date.isoformat())

    if _PERM_ERRORS:
        fail(3, "permission/read errors on %d path(s): %s" % (
            len(_PERM_ERRORS), safe(",".join(_PERM_ERRORS), 400)))
    return finish(0)


if __name__ == "__main__":
    run(main)
