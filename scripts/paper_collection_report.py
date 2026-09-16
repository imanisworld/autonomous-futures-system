"""Read-only EOD/EOW evidence rollups for futures and options.

This module does not collect market data, mutate strategy state, place orders,
or promote strategies. It summarizes evidence that existing collectors have
already written and posts the summaries directly to the dedicated optional
Discord webhook environment variables:

- ``DISCORD_ROUTE_PAPER_COLLECTION_FUTURES``
- ``DISCORD_ROUTE_PAPER_COLLECTION_OPTIONS``

Usage::

    python -m scripts.paper_collection_report --period eod
    python -m scripts.paper_collection_report --period eow

The report is intentionally conservative: missing or unreadable inputs are
shown explicitly instead of inferred as zero activity.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


FUTURES_ENV = "DISCORD_ROUTE_PAPER_COLLECTION_FUTURES"
OPTIONS_ENV = "DISCORD_ROUTE_PAPER_COLLECTION_OPTIONS"


def period_bounds(ref: date, period: str) -> tuple[date, date]:
    if period == "eod":
        return ref, ref
    if period == "eow":
        monday = ref - timedelta(days=ref.weekday())
        return monday, ref
    raise ValueError(f"unsupported period: {period}")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    except OSError:
        return []
    return rows


def _futures_rows(log_dir: Path, start: date, end: date) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    day = start
    while day <= end:
        rows.extend(_read_jsonl(log_dir / f"journal_{day.isoformat()}.jsonl"))
        day += timedelta(days=1)
    return rows


def summarize_futures(rows: list[dict[str, Any]]) -> dict[str, Any]:
    decisions: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()
    strategies: Counter[str] = Counter()
    instruments: Counter[str] = Counter()
    for row in rows:
        decision = row.get("decision")
        if decision:
            decisions[str(decision)] += 1
        if row.get("type") == "OUTCOME" or row.get("record_type") == "OUTCOME":
            outcome = row.get("outcome") if isinstance(row.get("outcome"), dict) else row
            result = outcome.get("result") if isinstance(outcome, dict) else None
            outcomes[str(result or "UNKNOWN")] += 1
        strategy = row.get("strategy") or row.get("setup_type")
        if strategy:
            strategies[str(strategy)] += 1
        instrument = row.get("instrument") or row.get("ticker")
        if instrument:
            instruments[str(instrument)] += 1
    return {
        "rows": len(rows),
        "decisions": dict(decisions),
        "outcomes": dict(outcomes),
        "strategies": dict(strategies),
        "instruments": dict(instruments),
    }


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info([{table}])")}
    except sqlite3.Error:
        return set()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is not None
    except sqlite3.Error:
        return False


def _count_table_range(
    conn: sqlite3.Connection, table: str, start: date, end: date
) -> dict[str, Any]:
    if not _table_exists(conn, table):
        return {"status": "MISSING_TABLE", "rows": None}
    columns = _table_columns(conn, table)
    timestamp_column = next(
        (c for c in ("timestamp", "created_at", "observed_at", "ts") if c in columns),
        None,
    )
    if timestamp_column is None:
        try:
            total = int(conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0])
        except sqlite3.Error:
            return {"status": "QUERY_ERROR", "rows": None}
        return {"status": "NO_TIMESTAMP_COLUMN", "rows": total}
    try:
        cur = conn.execute(
            f"SELECT * FROM [{table}] WHERE substr([{timestamp_column}], 1, 10) BETWEEN ? AND ?",
            (start.isoformat(), end.isoformat()),
        )
        names = [d[0] for d in (cur.description or [])]
        rows = cur.fetchall()
    except sqlite3.Error:
        return {"status": "QUERY_ERROR", "rows": None}

    status_counts: Counter[str] = Counter()
    if "status" in names:
        idx = names.index("status")
        for row in rows:
            status_counts[str(row[idx] or "UNKNOWN")] += 1
    return {
        "status": "OK",
        "rows": len(rows),
        "status_counts": dict(status_counts),
        "timestamp_column": timestamp_column,
    }


def summarize_options(sqlite_path: Path, start: date, end: date) -> dict[str, Any]:
    if not sqlite_path.exists():
        return {"status": "MISSING_DB", "path": str(sqlite_path), "tables": {}}
    try:
        conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return {"status": "OPEN_ERROR", "path": str(sqlite_path), "tables": {}}
    try:
        tables = {
            name: _count_table_range(conn, name, start, end)
            for name in ("scans", "options_shadow_journal")
        }
    finally:
        conn.close()
    return {"status": "OK", "path": str(sqlite_path), "tables": tables}


def run_collector_census(log_dir: Path) -> dict[str, Any]:
    """Run the existing read-only census and return its JSON payload."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "ops.collector_census", "--log-dir", str(log_dir), "--json"],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "ERROR", "error": type(exc).__name__}
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        return {"status": "ERROR", "exit_code": proc.returncode, "error": "invalid_json"}
    if isinstance(payload, dict):
        payload.setdefault("exit_code", proc.returncode)
        payload.setdefault("status", "OK" if proc.returncode == 0 else "ATTENTION")
        return payload
    return {"status": "ERROR", "error": "unexpected_payload", "exit_code": proc.returncode}


def _census_lines(census: dict[str, Any], *, options: bool) -> list[str]:
    collectors = census.get("collectors")
    if not isinstance(collectors, list):
        return [f"collector census: {census.get('status', 'UNKNOWN')}"]
    chosen: list[dict[str, Any]] = []
    for item in collectors:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        is_options = name.startswith("options ")
        if is_options == options:
            chosen.append(item)
    if not chosen:
        return ["collector census: no matching collectors"]
    counts = Counter(str(i.get("status") or "UNKNOWN") for i in chosen)
    bad = [str(i.get("name")) for i in chosen if i.get("status") in {"STALE", "DEAD", "ABSENT"}]
    line = "collector health: " + " · ".join(f"{k} {v}" for k, v in sorted(counts.items()))
    if bad:
        line += " · attention: " + ", ".join(bad[:8])
        if len(bad) > 8:
            line += f" (+{len(bad) - 8} more)"
    return [line]


def _top(counter: dict[str, int], limit: int = 5) -> str:
    if not counter:
        return "none"
    return ", ".join(
        f"{k}={v}" for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    )


def format_futures_report(
    summary: dict[str, Any], census: dict[str, Any], *, period: str, start: date, end: date
) -> str:
    title = "EOD" if period == "eod" else "EOW"
    lines = [
        f"**FUTURES PAPER COLLECTION — {title}** {start.isoformat()}" + ("" if start == end else f" → {end.isoformat()}"),
        f"journal rows: **{summary['rows']}**",
        f"decisions: {_top(summary['decisions'])}",
        f"outcomes: {_top(summary['outcomes'])}",
        f"strategies seen: {_top(summary['strategies'])}",
        f"instruments seen: {_top(summary['instruments'])}",
        *_census_lines(census, options=False),
    ]
    if summary["rows"] == 0:
        lines.append("⚠️ zero futures journal rows in the report window")
    lines.append("READ ONLY — evidence rollup; no promotion or execution action")
    return "\n".join(lines)


def format_options_report(
    summary: dict[str, Any], census: dict[str, Any], *, period: str, start: date, end: date
) -> str:
    title = "EOD" if period == "eod" else "EOW"
    tables = summary.get("tables") or {}
    scans = tables.get("scans") or {}
    journal = tables.get("options_shadow_journal") or {}
    lines = [
        f"**OPTIONS PAPER COLLECTION — {title}** {start.isoformat()}" + ("" if start == end else f" → {end.isoformat()}"),
        f"scanner DB: {summary.get('status', 'UNKNOWN')}",
        f"scans: **{scans.get('rows', 'unknown')}** ({scans.get('status', 'UNKNOWN')})",
        f"shadow journal: **{journal.get('rows', 'unknown')}** ({journal.get('status', 'UNKNOWN')})",
        f"journal statuses: {_top(journal.get('status_counts') or {})}",
        *_census_lines(census, options=True),
    ]
    if scans.get("rows") == 0:
        lines.append("⚠️ zero option scans in the report window")
    lines.append("READ ONLY — evidence rollup; no promotion or execution action")
    return "\n".join(lines)


def _post_discord(webhook_url: str, content: str) -> bool:
    try:
        body = json.dumps({"content": content}).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "afs-paper-collection/1"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _write_artifact(log_dir: Path, payload: dict[str, Any], *, ref: date, period: str) -> Path | None:
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / f"paper_collection_{period}_{ref.isoformat()}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return path
    except OSError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--period", choices=("eod", "eow"), required=True)
    parser.add_argument("--date", help="UTC reference date YYYY-MM-DD; default today")
    parser.add_argument("--log-dir", default=os.getenv("LOG_DIR", "logs"))
    parser.add_argument(
        "--options-db",
        default=os.getenv("OPTIONS_SCANNER_SQLITE_PATH", "logs/options_scanner.sqlite"),
    )
    parser.add_argument("--no-discord", action="store_true", help="build artifacts only")
    args = parser.parse_args(argv)

    ref = date.fromisoformat(args.date) if args.date else datetime.now(timezone.utc).date()
    start, end = period_bounds(ref, args.period)
    log_dir = Path(args.log_dir)
    options_db = Path(args.options_db)

    futures = summarize_futures(_futures_rows(log_dir, start, end))
    options = summarize_options(options_db, start, end)
    census = run_collector_census(log_dir)

    futures_report = format_futures_report(futures, census, period=args.period, start=start, end=end)
    options_report = format_options_report(options, census, period=args.period, start=start, end=end)

    payload = {
        "schema": "paper_collection_rollup_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": args.period,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "futures": futures,
        "options": options,
        "collector_census": census,
    }
    artifact = _write_artifact(log_dir, payload, ref=ref, period=args.period)

    send_failures = 0
    if not args.no_discord:
        for env_name, report in ((FUTURES_ENV, futures_report), (OPTIONS_ENV, options_report)):
            webhook = (os.getenv(env_name) or "").strip()
            if not webhook:
                print(f"[paper_collection_report] {env_name} unset; artifact only")
                continue
            ok = _post_discord(webhook, report)
            print(f"[paper_collection_report] {env_name} posted={ok}")
            if not ok:
                send_failures += 1

    print(futures_report)
    print()
    print(options_report)
    if artifact:
        print(f"[paper_collection_report] artifact={artifact}")
    return 1 if send_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
