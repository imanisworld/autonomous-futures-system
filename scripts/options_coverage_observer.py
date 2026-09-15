"""Run the read-only 30m options coverage observer for one completed session.

    python scripts/options_coverage_observer.py --date 2026-09-15
    python scripts/options_coverage_observer.py --date 2026-09-15 --symbols AAPL,NVDA
    python scripts/options_coverage_observer.py --report 2026-09-15

Observer-only.  Reads consolidated (SIP) 30Min bars for the universe (plus
5Min bars used solely to price the first-sight moment), runs
:mod:`alert_ranker.coverage_observer`, and writes rows to its OWN sqlite file
(``OPTIONS_COVERAGE_SQLITE_PATH``, default ``logs/options_coverage_observer.sqlite``).
It never opens the V1 scanner database, never alerts, never selects a
contract, never consumes risk, and never writes an episode block.

Universe: ``research/coverage/options_watchlist_150.csv`` (the pre-registered
equity-corpus watchlist, sha256 2770c80b…) unless ``--symbols`` or
``--universe`` overrides it.  SPY and QQQ are always fetched because the V1
alignment test needs them.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alert_ranker.bar_provider import AlpacaBarProvider, BarProviderError, CONSOLIDATED_FEED  # noqa: E402
from alert_ranker.causal_bars import MINUTE_5, MINUTE_30, Bar  # noqa: E402
from alert_ranker.config import resolve_alpaca_credentials  # noqa: E402
from alert_ranker.coverage_observer import (  # noqa: E402
    DEFAULT_DELAY_BUFFER,
    OBSERVER_ID,
    OBSERVER_VERSION,
    CoverageEvent,
    SymbolSeries,
    build_symbol_series,
    funnel,
    observe_symbol,
)
from alert_ranker.session_calendar import Session, nyse_session_for  # noqa: E402

DEFAULT_UNIVERSE = ROOT / "research" / "coverage" / "options_watchlist_150.csv"
DEFAULT_SQLITE = ROOT / "logs" / "options_coverage_observer.sqlite"
INDEX_SYMBOLS = ("SPY", "QQQ")
FETCH_BATCH = 40
LOOKBACK_DAYS = 10

SCHEMA = """
CREATE TABLE IF NOT EXISTS coverage_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observer_id TEXT NOT NULL,
    observer_version TEXT NOT NULL,
    session_date TEXT NOT NULL,
    ran_at TEXT NOT NULL,
    universe_source TEXT NOT NULL,
    symbols_requested INTEGER NOT NULL,
    symbols_observable INTEGER NOT NULL,
    events INTEGER NOT NULL,
    funnel_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS coverage_symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observer_version TEXT NOT NULL,
    session_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    observable INTEGER NOT NULL,
    reason TEXT NOT NULL,
    bars_current_session INTEGER NOT NULL,
    expected_current_bars INTEGER NOT NULL,
    sessions_loaded INTEGER NOT NULL,
    UNIQUE(observer_version, session_date, symbol)
);
CREATE TABLE IF NOT EXISTS coverage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observer_id TEXT NOT NULL,
    observer_version TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    session_date TEXT NOT NULL,
    bar_start TEXT NOT NULL,
    bar_close TEXT NOT NULL,
    family TEXT NOT NULL,
    sequence TEXT NOT NULL,
    requested_family INTEGER NOT NULL,
    v1_supported INTEGER NOT NULL,
    direction TEXT NOT NULL,
    entry_trigger REAL NOT NULL,
    invalidation REAL NOT NULL,
    risk REAL NOT NULL,
    nearest_rr_1 REAL,
    nearest_geometry_ok INTEGER NOT NULL,
    floor_rr_1 REAL,
    floor_geometry_ok INTEGER NOT NULL,
    floor_rescued INTEGER NOT NULL,
    alignment_ok INTEGER NOT NULL,
    alignment_failures TEXT NOT NULL,
    first_sight_at TEXT NOT NULL,
    first_sight_after_close INTEGER NOT NULL,
    first_sight_price REAL,
    nearest_remaining_rr REAL,
    floor_remaining_rr REAL,
    late_nearest INTEGER,
    late_floor INTEGER,
    would_qualify_v1_rule INTEGER,
    would_qualify_floor_rule INTEGER,
    row_json TEXT NOT NULL,
    UNIQUE(observer_version, symbol, timeframe, bar_start, family)
);
"""


def load_universe(path: Path) -> list[str]:
    symbols: list[str] = []
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        key = "Ticker" if "Ticker" in (reader.fieldnames or []) else (reader.fieldnames or ["symbol"])[0]
        for row in reader:
            value = (row.get(key) or "").strip().upper()
            if value:
                symbols.append(value)
    seen: set[str] = set()
    return [s for s in symbols if not (s in seen or seen.add(s))]


def sessions_through(day: date, lookback_days: int) -> list[Session]:
    sessions: list[Session] = []
    cursor = day - timedelta(days=lookback_days)
    while cursor <= day:
        session = nyse_session_for(cursor)
        if session is not None:
            sessions.append(session)
        cursor += timedelta(days=1)
    return sessions


def _chunks(items: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


async def fetch_all(provider: AlpacaBarProvider, symbols: Sequence[str], timeframe, start: datetime, end: datetime) -> tuple[dict[str, list[Bar]], dict[str, str]]:
    bars: dict[str, list[Bar]] = {}
    errors: dict[str, str] = {}
    for chunk in _chunks(list(symbols), FETCH_BATCH):
        try:
            got = await provider.fetch_bars(chunk, timeframe, start, end)
            bars.update(got)
        except BarProviderError as exc:
            # A batch failure hides which symbol was at fault; retry singly so
            # one bad ticker cannot blank the whole batch.
            for symbol in chunk:
                try:
                    bars.update(await provider.fetch_bars([symbol], timeframe, start, end))
                except BarProviderError as single:
                    errors[symbol] = f"{single.reason}:{single.detail}"[:120]
            if not errors:
                errors["__batch__"] = f"{exc.reason}:{exc.detail}"[:120]
    return bars, errors


def _bool(value: Any) -> int | None:
    if value is None:
        return None
    return 1 if value else 0


def write_rows(conn: sqlite3.Connection, session_date: str, universe_source: str, requested: int, series_by_symbol: dict[str, SymbolSeries], events: Sequence[CoverageEvent], report: dict[str, Any]) -> None:
    conn.executescript(SCHEMA)
    for symbol, series in series_by_symbol.items():
        current = series.sessions[-1].date if series.sessions else None
        conn.execute(
            "INSERT OR REPLACE INTO coverage_symbols (observer_version, session_date, symbol, observable, reason, bars_current_session, expected_current_bars, sessions_loaded) VALUES (?,?,?,?,?,?,?,?)",
            (
                OBSERVER_VERSION,
                session_date,
                symbol,
                1 if series.observable else 0,
                series.reason,
                len(series.by_session.get(current, [])) if current else 0,
                series.expected_current_bars,
                len(series.by_session),
            ),
        )
    for event in events:
        row = event.to_row()
        conn.execute(
            "INSERT OR REPLACE INTO coverage_events (observer_id, observer_version, symbol, timeframe, session_date, bar_start, bar_close, family, sequence, requested_family, v1_supported, direction, entry_trigger, invalidation, risk, nearest_rr_1, nearest_geometry_ok, floor_rr_1, floor_geometry_ok, floor_rescued, alignment_ok, alignment_failures, first_sight_at, first_sight_after_close, first_sight_price, nearest_remaining_rr, floor_remaining_rr, late_nearest, late_floor, would_qualify_v1_rule, would_qualify_floor_rule, row_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row["observer_id"], row["observer_version"], row["symbol"], row["timeframe"], row["session_date"], row["bar_start"], row["bar_close"], row["family"], row["sequence"],
                _bool(row["requested_family"]), _bool(row["v1_supported"]), row["direction"], row["entry_trigger"], row["invalidation"], row["risk"],
                row["nearest_rr_1"], _bool(row["nearest_geometry_ok"]), row["floor_rr_1"], _bool(row["floor_geometry_ok"]), _bool(row["floor_rescued"]),
                _bool(row["alignment_ok"]), row["alignment_failures"], row["first_sight_at"], _bool(row["first_sight_after_close"]), row["first_sight_price"], row["nearest_remaining_rr"], row["floor_remaining_rr"],
                _bool(row["late_nearest"]), _bool(row["late_floor"]), _bool(row["would_qualify_v1_rule"]), _bool(row["would_qualify_floor_rule"]), json.dumps(row, sort_keys=True),
            ),
        )
    observable = sum(1 for s in series_by_symbol.values() if s.observable)
    conn.execute(
        "INSERT INTO coverage_runs (observer_id, observer_version, session_date, ran_at, universe_source, symbols_requested, symbols_observable, events, funnel_json) VALUES (?,?,?,?,?,?,?,?,?)",
        (OBSERVER_ID, OBSERVER_VERSION, session_date, datetime.now(timezone.utc).isoformat(), universe_source, requested, observable, len(events), json.dumps(report, sort_keys=True)),
    )
    conn.commit()


def print_funnel(report: dict[str, Any], symbols: dict[str, SymbolSeries] | None = None, errors: dict[str, str] | None = None) -> None:
    print(f"{report['observer_id']} {report['observer_version']} timeframe={report['timeframe']}")
    if symbols is not None:
        observable = [s for s, v in symbols.items() if v.observable]
        print(f"symbols: requested={len(symbols)} observable={len(observable)} with_events={report['symbols_with_events']}")
        bad = {s: v.reason for s, v in symbols.items() if not v.observable}
        for symbol, reason in sorted(bad.items()):
            print(f"  not observable: {symbol}: {reason}")
        for symbol, reason in sorted((errors or {}).items()):
            print(f"  provider error: {symbol}: {reason}")
    for rule in ("v1_rule", "floor_rule"):
        f = report[rule]
        print(f"[{rule}]")
        for key in ("all_structural_events", "v1_supported", "unsupported_setup_family", "unsupported_timeframe", "target_geometry_failure", "market_alignment_failure", "first_sight_after_close", "late_at_first_sight", "first_sight_unpriced", "would_otherwise_qualify"):
            print(f"  {key:28s} {f[key]}")
    print("[by_family]  count | as-if-supported, V1 rule: geom-fail/align-fail/late/qualify | floor rule: geom-fail/align-fail/late/qualify")
    for family, info in sorted(report["by_family"].items(), key=lambda kv: -kv[1]["count"]):
        a = info["as_if_supported_v1_rule"]
        b = info["as_if_supported_floor_rule"]
        tag = "V1" if info["v1_supported"] else ("REQ" if info["requested"] else "other")
        print(f"  {family:28s} {info['count']:4d} {tag:5s} | {a['target_geometry_failure']}/{a['market_alignment_failure']}/{a['late_at_first_sight']}/{a['would_otherwise_qualify']} | {b['target_geometry_failure']}/{b['market_alignment_failure']}/{b['late_at_first_sight']}/{b['would_otherwise_qualify']}")


def report_from_db(path: Path, session_date: str) -> int:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    rows = conn.execute("SELECT row_json FROM coverage_events WHERE session_date=? AND observer_version=?", (session_date, OBSERVER_VERSION)).fetchall()
    events = [json.loads(r[0]) for r in rows]
    print_funnel(funnel(events))
    return 0


async def run(args: argparse.Namespace) -> int:
    day = date.fromisoformat(args.date)
    session = nyse_session_for(day)
    if session is None:
        print(f"{day} is not an NYSE session", file=sys.stderr)
        return 2
    sessions = sessions_through(day, args.lookback_days)

    if args.symbols:
        universe = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        source = "cli"
    else:
        path = Path(args.universe) if args.universe else DEFAULT_UNIVERSE
        universe = load_universe(path)
        source = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
    wanted = list(dict.fromkeys([*universe, *INDEX_SYMBOLS]))

    api_key, secret_key = resolve_alpaca_credentials()
    if not api_key or not secret_key:
        print("Alpaca credentials not configured (ALPACA_API_KEY/ALPACA_KEY + secret)", file=sys.stderr)
        return 2
    provider = AlpacaBarProvider(
        base_url=os.environ.get("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets").rstrip("/"),
        api_key=api_key,
        secret_key=secret_key,
        feed=CONSOLIDATED_FEED,
    )

    start = sessions[0].open - timedelta(hours=1)
    # Provider windows are inclusive; stop at the session close so no
    # after-hours bar can be mistaken for session structure.
    end = session.close
    bars30, errors = await fetch_all(provider, wanted, MINUTE_30, start, end)
    # 5Min bars price the first-sight moment only; bars up to close + delay
    # buffer + one grid step are enough.
    fine_end = session.close + DEFAULT_DELAY_BUFFER + timedelta(minutes=10)
    bars5, errors5 = await fetch_all(provider, wanted, MINUTE_5, session.open, fine_end)
    for symbol, reason in errors5.items():
        errors.setdefault(symbol, f"5Min:{reason}")

    series_by_symbol = {symbol: build_symbol_series(symbol, bars30.get(symbol, []), sessions) for symbol in wanted}
    spy = series_by_symbol.get("SPY")
    qqq = series_by_symbol.get("QQQ")

    events: list[CoverageEvent] = []
    for symbol in universe:
        series = series_by_symbol[symbol]
        events.extend(observe_symbol(series, session, spy=spy, qqq=qqq, fine_bars=bars5.get(symbol), fine_timeframe=MINUTE_5))

    report = funnel(events)
    report["session_date"] = session.date.isoformat()
    report["symbols_requested"] = len(universe)
    report["symbols_observable"] = sum(1 for s in universe if series_by_symbol[s].observable)
    report["index_context"] = {"SPY": spy.observable if spy else False, "QQQ": qqq.observable if qqq else False}
    print_funnel(report, {s: series_by_symbol[s] for s in universe}, errors)

    if args.dry_run:
        print("(dry run: nothing written)")
        return 0
    sqlite_path = Path(args.sqlite or os.environ.get("OPTIONS_COVERAGE_SQLITE_PATH") or DEFAULT_SQLITE)
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(sqlite_path)
    try:
        write_rows(conn, session.date.isoformat(), source, len(universe), {s: series_by_symbol[s] for s in universe}, events, report)
    finally:
        conn.close()
    print(f"wrote {len(events)} events to {sqlite_path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only 30m options coverage observer")
    parser.add_argument("--date", help="Session date YYYY-MM-DD to observe (after the close)")
    parser.add_argument("--report", metavar="DATE", help="Print the stored funnel for DATE instead of fetching")
    parser.add_argument("--symbols", help="Comma-separated override of the universe")
    parser.add_argument("--universe", help="CSV with a Ticker column (default: research/coverage/options_watchlist_150.csv)")
    parser.add_argument("--sqlite", help="Observer sqlite path (default: logs/options_coverage_observer.sqlite)")
    parser.add_argument("--lookback-days", type=int, default=LOOKBACK_DAYS)
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print, write nothing")
    args = parser.parse_args(argv)
    if args.report:
        return report_from_db(Path(args.sqlite or os.environ.get("OPTIONS_COVERAGE_SQLITE_PATH") or DEFAULT_SQLITE), args.report)
    if not args.date:
        parser.error("--date or --report is required")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
