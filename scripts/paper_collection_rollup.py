"""Read-only EOD/EOW evidence rollup for futures and options paper collection.

This does NOT advance collectors, mutate trading state, select trades, or contact a
broker. It only reads the existing evidence stores, writes its own report artifact,
and optionally posts concise summaries to two Discord webhook env vars:

    DISCORD_ROUTE_PAPER_COLLECTION_FUTURES
    DISCORD_ROUTE_PAPER_COLLECTION_OPTIONS

Examples:
    python -m scripts.paper_collection_rollup --period daily
    python -m scripts.paper_collection_rollup --period weekly

The report intentionally separates collection health from strategy evaluation. The
options coverage observer counts below are RAW observer events, not independent
validation episodes and never a promotion signal.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from ops.collector_census import ABSENT, DEAD, FRESH, STALE, build_census

_ET = ZoneInfo("America/New_York")
FUTURES_WEBHOOK_ENV = "DISCORD_ROUTE_PAPER_COLLECTION_FUTURES"
OPTIONS_WEBHOOK_ENV = "DISCORD_ROUTE_PAPER_COLLECTION_OPTIONS"
DEFAULT_LOG_DIR = Path("logs")
DEFAULT_COVERAGE_DB = Path("/root/afs-shared/coverage/options_coverage_observer.sqlite")
DEFAULT_REPORT_DIR = Path("logs/paper_collection_reports")
PROSPECTIVE_CUTOFF = "2026-09-15"

_FUTURES_COLLECTORS = {
    "futures journal",
    "bars MNQ",
    "bars MES",
    "strategy context",
    "feed gap alarm",
    "bars MNQ 5m",
    "daily_22 swing state",
    "mes_122 lane journal",
    "forward A/B campaign",
}
_OPTIONS_COLLECTORS = {
    "options scans",
    "options shadow journal",
    "options companion",
}

_TS_KEYS = ("timestamp", "ts", "observed_at", "signal_timestamp", "checked_utc", "created_at")


@dataclass(frozen=True)
class Window:
    label: str
    start_utc: datetime
    end_utc: datetime
    start_date: date
    end_date: date


def _parse_ref(value: str | None) -> date:
    return date.fromisoformat(value) if value else datetime.now(_ET).date()


def period_window(period: str, ref: date) -> Window:
    if period == "daily":
        start_date = ref
        end_date = ref
    elif period == "weekly":
        start_date = ref - timedelta(days=ref.weekday())
        end_date = start_date + timedelta(days=6)
    else:  # pragma: no cover - argparse rejects it
        raise ValueError(period)
    start_local = datetime.combine(start_date, time.min, tzinfo=_ET)
    end_local = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=_ET)
    label = start_date.isoformat() if period == "daily" else f"{start_date.isoformat()}→{end_date.isoformat()}"
    return Window(label, start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc), start_date, end_date)


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _row_ts(row: dict[str, Any]) -> datetime | None:
    for key in _TS_KEYS:
        parsed = _parse_ts(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _in_window(moment: datetime | None, window: Window) -> bool:
    return moment is not None and window.start_utc <= moment < window.end_utc


def _count_jsonl_period(path: Path, window: Window) -> tuple[int, int, datetime | None]:
    """Return (rows in period, total parseable rows, newest timestamp)."""
    period_rows = 0
    total_rows = 0
    newest: datetime | None = None
    try:
        with path.open(encoding="utf-8") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                total_rows += 1
                stamp = _row_ts(row)
                if stamp is not None and (newest is None or stamp > newest):
                    newest = stamp
                if _in_window(stamp, window):
                    period_rows += 1
    except OSError:
        return 0, 0, None
    return period_rows, total_rows, newest


def changed_jsonl_streams(log_dir: Path, window: Window) -> list[dict[str, Any]]:
    """Inventory every JSONL evidence stream that actually wrote in the period.

    This catches new strategy/campaign files even when the expected-collector census
    has not yet been taught their name. Files whose mtime predates the period are
    skipped without opening them.
    """
    rows: list[dict[str, Any]] = []
    if not log_dir.exists():
        return rows
    for path in sorted(log_dir.rglob("*.jsonl")):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime < window.start_utc:
            continue
        in_period, total, newest = _count_jsonl_period(path, window)
        if in_period <= 0:
            continue
        rows.append(
            {
                "path": str(path.relative_to(log_dir)),
                "period_rows": in_period,
                "total_rows": total,
                "last": newest.isoformat() if newest else None,
            }
        )
    return sorted(rows, key=lambda row: (-row["period_rows"], row["path"]))


def _open_ro(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is not None
    except sqlite3.Error:
        return False


def _sqlite_period_count(conn: sqlite3.Connection, table: str, column: str, window: Window) -> int | None:
    if not _table_exists(conn, table):
        return None
    try:
        return int(
            conn.execute(
                f"SELECT COUNT(*) FROM [{table}] WHERE [{column}] >= ? AND [{column}] < ?",
                (window.start_utc.isoformat(), window.end_utc.isoformat()),
            ).fetchone()[0]
        )
    except sqlite3.Error:
        return None


def options_scanner_summary(path: Path, window: Window) -> dict[str, Any]:
    out: dict[str, Any] = {
        "db": str(path),
        "scans": None,
        "journal_rows": None,
        "journal_status": {},
        "episode_blocks": None,
        "current_status": {},
    }
    conn = _open_ro(path)
    if conn is None:
        return out
    try:
        out["scans"] = _sqlite_period_count(conn, "scans", "timestamp", window)
        out["journal_rows"] = _sqlite_period_count(conn, "options_shadow_journal", "timestamp", window)
        out["episode_blocks"] = _sqlite_period_count(conn, "options_episode_blocks", "blocked_at", window)
        if _table_exists(conn, "options_shadow_journal"):
            try:
                status_rows = conn.execute(
                    "SELECT status, COUNT(*) n FROM options_shadow_journal "
                    "WHERE timestamp >= ? AND timestamp < ? GROUP BY status",
                    (window.start_utc.isoformat(), window.end_utc.isoformat()),
                ).fetchall()
                out["journal_status"] = {str(row[0]): int(row[1]) for row in status_rows}
                current = conn.execute(
                    "SELECT status, COUNT(*) n FROM options_shadow_journal GROUP BY status"
                ).fetchall()
                out["current_status"] = {str(row[0]): int(row[1]) for row in current}
            except sqlite3.Error:
                pass
    finally:
        conn.close()
    return out


def coverage_summary(path: Path, window: Window) -> dict[str, Any]:
    out: dict[str, Any] = {
        "db": str(path),
        "sessions": 0,
        "runs": [],
        "events": 0,
        "by_sequence": {},
        "prospective_raw": {},
        "note": "raw observer events; NOT independent validation episodes",
    }
    conn = _open_ro(path)
    if conn is None:
        return out
    try:
        lo, hi = window.start_date.isoformat(), window.end_date.isoformat()
        if _table_exists(conn, "coverage_runs"):
            # Latest run per session avoids double-counting a rerun of the same day.
            runs = conn.execute(
                "SELECT r.session_date, r.symbols_requested, r.symbols_observable, r.events, r.observer_version "
                "FROM coverage_runs r JOIN (SELECT session_date, MAX(id) id FROM coverage_runs "
                "WHERE session_date >= ? AND session_date <= ? GROUP BY session_date) x ON r.id=x.id "
                "ORDER BY r.session_date",
                (lo, hi),
            ).fetchall()
            out["runs"] = [dict(row) for row in runs]
            out["sessions"] = len(runs)
        if _table_exists(conn, "coverage_events"):
            grouped = conn.execute(
                "SELECT sequence, COUNT(*) n FROM coverage_events "
                "WHERE session_date >= ? AND session_date <= ? GROUP BY sequence ORDER BY n DESC",
                (lo, hi),
            ).fetchall()
            out["by_sequence"] = {str(row[0]): int(row[1]) for row in grouped}
            out["events"] = sum(out["by_sequence"].values())
            prospective = conn.execute(
                "SELECT sequence, COUNT(*) n FROM coverage_events WHERE session_date > ? "
                "AND sequence IN ('strat_212_reversal','strat_122') GROUP BY sequence",
                (PROSPECTIVE_CUTOFF,),
            ).fetchall()
            out["prospective_raw"] = {str(row[0]): int(row[1]) for row in prospective}
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return out


def _collector_subset(census: dict[str, Any], names: set[str]) -> list[dict[str, Any]]:
    return [row for row in census.get("collectors", []) if row.get("name") in names]


def _health_counts(rows: Iterable[dict[str, Any]]) -> Counter:
    return Counter(str(row.get("status") or "UNKNOWN") for row in rows)


def _fmt_health(rows: list[dict[str, Any]]) -> str:
    c = _health_counts(rows)
    return f"{c[FRESH]} fresh · {c[STALE]} stale · {c[DEAD] + c[ABSENT]} dead/absent"


def _bad_collectors(rows: list[dict[str, Any]]) -> list[str]:
    return [f"{row['name']}={row['status']}" for row in rows if row.get("status") != FRESH]


def _is_options_stream(path: str) -> bool:
    low = path.lower()
    return "option" in low or "coverage" in low or "alert_ranker" in low


def _top_streams(streams: list[dict[str, Any]], *, options: bool, limit: int = 8) -> list[dict[str, Any]]:
    matched = [row for row in streams if _is_options_stream(row["path"]) == options]
    return matched[:limit]


def build_futures_report(census: dict[str, Any], streams: list[dict[str, Any]], window: Window, period: str) -> str:
    collectors = _collector_subset(census, _FUTURES_COLLECTORS)
    changed = _top_streams(streams, options=False)
    lanes = census.get("hypothetical_lanes") or {}
    open_lanes = list(lanes.get("open_positions") or [])
    campaign = (census.get("campaign_arms") or {}).get("configured") or {}
    campaign_text = ", ".join(f"{name}={row.get('count', 0)}" for name, row in sorted(campaign.items())) or "none"
    lines = [
        f"📦 **PAPER COLLECTION — FUTURES {period.upper()}** · {window.label}",
        f"Collector health: **{_fmt_health(collectors)}**",
    ]
    bad = _bad_collectors(collectors)
    if bad:
        lines.append("Attention: " + "; ".join(bad))
    if changed:
        lines.append("Evidence written: " + " · ".join(f"{r['path']} +{r['period_rows']}" for r in changed))
    else:
        lines.append("Evidence written: **none detected in the period**")
    lines.append("Forward A/B cumulative candidates: " + campaign_text)
    lines.append("Open isolated paper lanes: " + (", ".join(open_lanes) if open_lanes else "none"))
    if lanes.get("mnq_position_exposed_without_fresh_5m_bars"):
        lines.append("⚠️ MNQ paper position exposed without fresh 5m bars")
    return "\n".join(lines)


def build_options_report(census: dict[str, Any], scanner: dict[str, Any], coverage: dict[str, Any], streams: list[dict[str, Any]], window: Window, period: str) -> str:
    collectors = _collector_subset(census, _OPTIONS_COLLECTORS)
    changed = _top_streams(streams, options=True)
    statuses = scanner.get("journal_status") or {}
    status_text = ", ".join(f"{k}={v}" for k, v in sorted(statuses.items())) or "none"
    runs = coverage.get("runs") or []
    if runs:
        observability = ", ".join(
            f"{r['session_date']} {r['symbols_observable']}/{r['symbols_requested']}" for r in runs[-5:]
        )
    else:
        observability = "no coverage run in period"
    raw = coverage.get("prospective_raw") or {}
    lines = [
        f"📦 **PAPER COLLECTION — OPTIONS {period.upper()}** · {window.label}",
        f"Collector health: **{_fmt_health(collectors)}**",
        f"Scanner: scans={scanner.get('scans')} · journal={scanner.get('journal_rows')} · episode-blocks={scanner.get('episode_blocks')}",
        f"Journal statuses this period: {status_text}",
        f"Coverage observer: {coverage.get('sessions', 0)} sessions · {coverage.get('events', 0)} raw events · {observability}",
        (
            "Prospective watch (raw events only, validation uses reducer episodes): "
            f"2-1-2 reversal={raw.get('strat_212_reversal', 0)} · 1-2-2={raw.get('strat_122', 0)}"
        ),
    ]
    bad = _bad_collectors(collectors)
    if bad:
        lines.append("Attention: " + "; ".join(bad))
    if changed:
        lines.append("Evidence written: " + " · ".join(f"{r['path']} +{r['period_rows']}" for r in changed))
    return "\n".join(lines)


def _post_webhook(env_var: str, content: str) -> bool:
    url = os.getenv(env_var, "").strip()
    if not url:
        return False
    try:
        from notifications.discord_notifier import _post_json

        body = json.dumps({"content": content}).encode("utf-8")
        _post_json(url, body, {"Content-Type": "application/json"})
        return True
    except Exception:
        return False


def _artifact_path(report_dir: Path, period: str, ref: date) -> Path:
    if period == "daily":
        stem = ref.isoformat()
    else:
        year, week, _ = ref.isocalendar()
        stem = f"{year}-W{week:02d}"
    return report_dir / f"paper_collection_{period}_{stem}.json"


def run(*, period: str, ref: date, log_dir: Path, coverage_db: Path, report_dir: Path, send: bool) -> dict[str, Any]:
    window = period_window(period, ref)
    now = datetime.now(timezone.utc)
    census = build_census(log_dir, now=now)
    streams = changed_jsonl_streams(log_dir, window)
    scanner = options_scanner_summary(log_dir / "options_scanner.sqlite", window)
    coverage = coverage_summary(coverage_db, window)
    futures_report = build_futures_report(census, streams, window, period)
    options_report = build_options_report(census, scanner, coverage, streams, window, period)

    artifact = {
        "schema_version": 1,
        "generated_at": now.isoformat(),
        "period": period,
        "window": {
            "label": window.label,
            "start_utc": window.start_utc.isoformat(),
            "end_utc": window.end_utc.isoformat(),
        },
        "census": census,
        "changed_jsonl_streams": streams,
        "options_scanner": scanner,
        "options_coverage": coverage,
        "reports": {"futures": futures_report, "options": options_report},
    }
    try:
        report_dir.mkdir(parents=True, exist_ok=True)
        _artifact_path(report_dir, period, ref).write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        pass

    sent = {"futures": False, "options": False}
    if send:
        sent["futures"] = _post_webhook(FUTURES_WEBHOOK_ENV, futures_report)
        sent["options"] = _post_webhook(OPTIONS_WEBHOOK_ENV, options_report)
    artifact["sent"] = sent
    return artifact


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--period", choices=("daily", "weekly"), required=True)
    parser.add_argument("--date", help="New York reference date (YYYY-MM-DD); default=today")
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--coverage-db", type=Path, default=DEFAULT_COVERAGE_DB)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--no-send", action="store_true", help="write/print only; never post to Discord")
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv

        load_dotenv(".env")
    except Exception:
        pass

    artifact = run(
        period=args.period,
        ref=_parse_ref(args.date),
        log_dir=args.log_dir,
        coverage_db=args.coverage_db,
        report_dir=args.report_dir,
        send=not args.no_send,
    )
    print(artifact["reports"]["futures"])
    print()
    print(artifact["reports"]["options"])
    print("\nsent:", artifact["sent"])
    # Reporting is fail-soft. Health problems are visible in the message/artifact;
    # they do not make the oneshot timer retry or touch trading state.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
