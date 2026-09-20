#!/usr/bin/env python3
"""Read-only Signa storage report.

Reports growth and quality metrics for shared Signa evidence tables without
creating tables, mutating rows, pruning evidence, or touching scanner/futures
runtime paths.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SIGNAL_TABLE = "signa_snapshots"
CONTEXT_TABLE = "options_signa_context"


def parse_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def db_file_sizes(path: Path) -> dict[str, Any]:
    files = [path, Path(str(path) + "-wal"), Path(str(path) + "-shm")]
    items = {p.name: p.stat().st_size for p in files if p.exists()}
    return {
        "path": str(path),
        "exists": path.exists(),
        "files": items,
        "total_bytes": sum(items.values()),
    }


def connect_read_only(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def count_by(conn: sqlite3.Connection, table: str, column: str, *, limit: int = 25) -> list[dict[str, Any]]:
    if not table_exists(conn, table):
        return []
    # Table/column names are fixed internal constants or validated by caller.
    rows = conn.execute(
        f"SELECT {column} AS key, COUNT(*) AS count FROM {table} "
        f"GROUP BY {column} ORDER BY count DESC, key ASC LIMIT ?",
        (limit,),
    ).fetchall()
    return [{"key": row["key"], "count": int(row["count"])} for row in rows]


def min_max_time(conn: sqlite3.Connection, table: str, column: str) -> tuple[datetime | None, datetime | None]:
    if not table_exists(conn, table):
        return None, None
    row = conn.execute(f"SELECT MIN({column}) AS oldest, MAX({column}) AS newest FROM {table}").fetchone()
    return parse_utc(row["oldest"]), parse_utc(row["newest"])


def rows_per_day(row_count: int, oldest: datetime | None, newest: datetime | None) -> float | None:
    if row_count <= 0 or oldest is None or newest is None:
        return None
    span = max((newest - oldest).total_seconds() / 86400.0, 1.0)
    return round(row_count / span, 2)


def authority_counts(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    if not table_exists(conn, table):
        return []
    rows = conn.execute(
        f"SELECT observation_only, trade_authority, COUNT(*) AS count FROM {table} "
        "GROUP BY observation_only, trade_authority ORDER BY count DESC"
    ).fetchall()
    return [
        {
            "observation_only": bool(row["observation_only"]),
            "trade_authority": bool(row["trade_authority"]),
            "count": int(row["count"]),
        }
        for row in rows
    ]


def snapshot_endpoint_health(conn: sqlite3.Connection, *, limit: int = 50) -> list[dict[str, Any]]:
    if not table_exists(conn, SIGNAL_TABLE):
        return []
    rows = conn.execute(
        "SELECT endpoint, status, http_status, COUNT(*) AS count "
        f"FROM {SIGNAL_TABLE} GROUP BY endpoint, status, http_status"
    ).fetchall()
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        endpoint = str(row["endpoint"])
        entry = grouped.setdefault(endpoint, {"endpoint": endpoint, "total": 0, "ok": 0, "errors": 0, "http_status_counts": Counter(), "status_counts": Counter()})
        count = int(row["count"])
        status = str(row["status"] or "UNKNOWN").upper()
        entry["total"] += count
        entry["status_counts"][status] += count
        if row["http_status"] is not None:
            entry["http_status_counts"][str(row["http_status"])] += count
        if status == "OK":
            entry["ok"] += count
        else:
            entry["errors"] += count
    out: list[dict[str, Any]] = []
    for entry in grouped.values():
        total = int(entry["total"])
        errors = int(entry["errors"])
        out.append({
            "endpoint": entry["endpoint"],
            "total": total,
            "ok": int(entry["ok"]),
            "errors": errors,
            "error_rate": round(errors / total, 4) if total else 0.0,
            "status_counts": dict(sorted(entry["status_counts"].items())),
            "http_status_counts": dict(sorted(entry["http_status_counts"].items())),
        })
    return sorted(out, key=lambda x: (-x["errors"], -x["total"], x["endpoint"]))[:limit]


def duplicate_snapshot_keys(conn: sqlite3.Connection, *, limit: int = 25) -> list[dict[str, Any]]:
    if not table_exists(conn, SIGNAL_TABLE):
        return []
    rows = conn.execute(
        "SELECT source, endpoint, symbol, timeframe, params_hash, snapshot_bucket, COUNT(*) AS count "
        f"FROM {SIGNAL_TABLE} GROUP BY source, endpoint, symbol, timeframe, params_hash, snapshot_bucket "
        "HAVING COUNT(*) > 1 ORDER BY count DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(row) | {"count": int(row["count"])} for row in rows]


def duplicate_context_keys(conn: sqlite3.Connection, *, limit: int = 25) -> list[dict[str, Any]]:
    if not table_exists(conn, CONTEXT_TABLE):
        return []
    rows = conn.execute(
        "SELECT candidate_key, COUNT(*) AS count FROM options_signa_context "
        "GROUP BY candidate_key HAVING COUNT(*) > 1 ORDER BY count DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [{"candidate_key": row["candidate_key"], "count": int(row["count"])} for row in rows]


def table_row_count(conn: sqlite3.Connection, table: str) -> int | None:
    if not table_exists(conn, table):
        return None
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def recent_rows_by_day(conn: sqlite3.Connection, table: str, column: str, *, limit: int = 10) -> list[dict[str, Any]]:
    if not table_exists(conn, table):
        return []
    rows = conn.execute(
        f"SELECT substr({column}, 1, 10) AS day, COUNT(*) AS count FROM {table} "
        f"WHERE {column} IS NOT NULL GROUP BY day ORDER BY day DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [{"day": row["day"], "count": int(row["count"])} for row in rows]


def build_report(db_path: str | Path, *, now: datetime | str | None = None) -> dict[str, Any]:
    path = Path(db_path)
    generated_at = parse_utc(now) or datetime.now(timezone.utc)
    report: dict[str, Any] = {
        "schema_version": "signa_storage_report_v1",
        "generated_at": iso(generated_at),
        "read_only": True,
        "mutates_database": False,
        "prunes_database": False,
        "database": db_file_sizes(path),
        "tables": {},
        "endpoint_health": [],
        "duplicates": {},
        "notes": [
            "Report is measurement-only. It must not be used as a pruning action.",
            "Retention decisions should be made from repeated reports, not one off-session sample.",
        ],
    }
    with connect_read_only(path) as conn:
        report["sqlite"] = {
            "integrity_check": conn.execute("PRAGMA integrity_check").fetchone()[0],
            "quick_check": conn.execute("PRAGMA quick_check").fetchone()[0],
        }
        for table, time_col in ((SIGNAL_TABLE, "retrieved_at"), (CONTEXT_TABLE, "timestamp")):
            exists = table_exists(conn, table)
            summary: dict[str, Any] = {"exists": exists}
            if exists:
                count = table_row_count(conn, table) or 0
                oldest, newest = min_max_time(conn, table, time_col)
                summary.update({
                    "row_count": count,
                    "oldest": iso(oldest),
                    "newest": iso(newest),
                    "estimated_rows_per_day": rows_per_day(count, oldest, newest),
                    "authority_counts": authority_counts(conn, table),
                    "rows_by_recent_day": recent_rows_by_day(conn, table, time_col),
                })
                if table == SIGNAL_TABLE:
                    summary.update({
                        "by_source": count_by(conn, table, "source"),
                        "by_status": count_by(conn, table, "status"),
                        "by_symbol": count_by(conn, table, "symbol"),
                        "by_timeframe": count_by(conn, table, "timeframe"),
                    })
                else:
                    summary.update({
                        "by_source": count_by(conn, table, "source"),
                        "by_status": count_by(conn, table, "status"),
                        "by_ticker": count_by(conn, table, "ticker"),
                        "by_timeframe": count_by(conn, table, "timeframe"),
                    })
            report["tables"][table] = summary
        report["endpoint_health"] = snapshot_endpoint_health(conn)
        report["duplicates"] = {
            "snapshot_logical_keys": duplicate_snapshot_keys(conn),
            "context_candidate_keys": duplicate_context_keys(conn),
        }
    return report


def render_markdown(report: dict[str, Any]) -> str:
    db = report["database"]
    lines = [
        "# Signa Storage Report",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Safety",
        "",
        f"- read only: **{report['read_only']}**",
        f"- mutates database: **{report['mutates_database']}**",
        f"- prunes database: **{report['prunes_database']}**",
        "",
        "## Database",
        "",
        f"- path: `{db['path']}`",
        f"- exists: **{db['exists']}**",
        f"- total bytes: **{db['total_bytes']}**",
        "",
        "## Tables",
        "",
    ]
    for table, summary in report.get("tables", {}).items():
        lines.extend([
            f"### `{table}`",
            "",
            f"- exists: **{summary.get('exists')}**",
        ])
        if summary.get("exists"):
            lines.extend([
                f"- rows: **{summary.get('row_count')}**",
                f"- oldest: `{summary.get('oldest')}`",
                f"- newest: `{summary.get('newest')}`",
                f"- estimated rows/day: **{summary.get('estimated_rows_per_day')}**",
                f"- authority counts: `{json.dumps(summary.get('authority_counts', []), sort_keys=True)}`",
            ])
        lines.append("")
    lines.extend(["## Endpoint health", ""])
    for item in report.get("endpoint_health", [])[:15]:
        lines.append(
            f"- `{item['endpoint']}` — total **{item['total']}**, errors **{item['errors']}**, error rate **{item['error_rate']}**"
        )
    if not report.get("endpoint_health"):
        lines.append("- no endpoint rows")
    lines.extend(["", "## Duplicates", ""])
    dupes = report.get("duplicates", {})
    lines.append(f"- snapshot logical key duplicates: **{len(dupes.get('snapshot_logical_keys', []))}**")
    lines.append(f"- context candidate key duplicates: **{len(dupes.get('context_candidate_keys', []))}**")
    return "\n".join(lines) + "\n"


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Signa storage growth/quality report")
    parser.add_argument("--db", required=True, help="Path to options_scanner.sqlite")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = build_report(args.db)
    if args.format == "markdown":
        print(render_markdown(report), end="")
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
