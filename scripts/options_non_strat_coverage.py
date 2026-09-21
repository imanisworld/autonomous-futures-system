"""After-close, observer-only non-Strat options coverage collection.

Examples:
    python scripts/options_non_strat_coverage.py --date 2026-09-18 --dry-run
    python scripts/options_non_strat_coverage.py --date 2026-09-18
    python scripts/options_non_strat_coverage.py --report 2026-09-18

The lane uses consolidated SIP 5-minute equity bars and writes only to its own
SQLite file. It never imports the scanner, Discord, options contracts, risk,
broker, webhook, or execution modules.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alert_ranker.bar_provider import AlpacaBarProvider, CONSOLIDATED_FEED  # noqa: E402
from alert_ranker.causal_bars import MINUTE_5, missing_bar_starts, session_bars  # noqa: E402
from alert_ranker.config import resolve_alpaca_credentials  # noqa: E402
from alert_ranker.non_strat_coverage import (  # noqa: E402
    OBSERVER_ID,
    OBSERVER_VERSION,
    NonStratEvent,
    OutcomeView,
    measure_outcomes,
    observe_session,
    summarize,
)
from alert_ranker.session_calendar import Session, nyse_session_for  # noqa: E402
from scripts.options_coverage_observer import (  # noqa: E402
    DEFAULT_UNIVERSE,
    fetch_all,
    load_universe,
    sessions_through,
)

DEFAULT_SQLITE = ROOT / "logs" / "options_non_strat_coverage.sqlite"
INDEX_SYMBOLS = ("SPY", "QQQ")
LOOKBACK_DAYS = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS non_strat_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observer_id TEXT NOT NULL,
    observer_version TEXT NOT NULL,
    session_date TEXT NOT NULL,
    ran_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    universe_source TEXT NOT NULL,
    symbols_requested INTEGER NOT NULL,
    symbols_observable INTEGER NOT NULL,
    events INTEGER NOT NULL,
    episodes INTEGER NOT NULL,
    summary_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS non_strat_symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observer_version TEXT NOT NULL,
    session_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    observable INTEGER NOT NULL,
    reason TEXT NOT NULL,
    UNIQUE(observer_version, session_date, symbol)
);
CREATE TABLE IF NOT EXISTS non_strat_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observer_id TEXT NOT NULL,
    observer_version TEXT NOT NULL,
    symbol TEXT NOT NULL,
    session_date TEXT NOT NULL,
    bar_start TEXT NOT NULL,
    bar_close TEXT NOT NULL,
    family TEXT NOT NULL,
    direction TEXT NOT NULL,
    level_name TEXT NOT NULL,
    level_value REAL NOT NULL,
    trigger_price REAL NOT NULL,
    episode_id TEXT NOT NULL,
    market_aligned INTEGER,
    row_json TEXT NOT NULL,
    UNIQUE(observer_version, symbol, bar_start, family, level_name)
);
CREATE TABLE IF NOT EXISTS non_strat_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observer_version TEXT NOT NULL,
    symbol TEXT NOT NULL,
    session_date TEXT NOT NULL,
    family TEXT NOT NULL,
    direction TEXT NOT NULL,
    episode_id TEXT NOT NULL,
    event_bar_start TEXT NOT NULL,
    horizon TEXT NOT NULL,
    close_return_bps REAL,
    mfe_bps REAL,
    mae_bps REAL,
    row_json TEXT NOT NULL,
    UNIQUE(observer_version, symbol, event_bar_start, family, horizon)
);
"""


def _bool(value: bool | None) -> int | None:
    if value is None:
        return None
    return 1 if value else 0


def _session_slice(bars, session: Session):
    return session_bars(bars, MINUTE_5, session.open, session.close)


def _complete(bars, session: Session) -> bool:
    return not missing_bar_starts(
        list(bars),
        MINUTE_5,
        session.open,
        session.close,
        through=session.close,
    )


def _history_before(bars, session: Session):
    return [bar for bar in bars if bar.start_utc < session.open]


def write_rows(
    conn: sqlite3.Connection,
    *,
    session_date: str,
    universe_source: str,
    symbols_requested: int,
    observability: dict[str, str],
    events: Sequence[NonStratEvent],
    outcomes: Sequence[OutcomeView],
    summary: dict[str, Any],
) -> None:
    conn.executescript(SCHEMA)
    for symbol, reason in observability.items():
        conn.execute(
            """INSERT OR REPLACE INTO non_strat_symbols
               (observer_version, session_date, symbol, observable, reason)
               VALUES (?,?,?,?,?)""",
            (OBSERVER_VERSION, session_date, symbol, 0 if reason else 1, reason),
        )

    for event in events:
        row = event.to_row()
        conn.execute(
            """INSERT OR REPLACE INTO non_strat_events
               (observer_id, observer_version, symbol, session_date, bar_start,
                bar_close, family, direction, level_name, level_value,
                trigger_price, episode_id, market_aligned, row_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                row["observer_id"],
                row["observer_version"],
                row["symbol"],
                row["session_date"],
                row["bar_start"],
                row["bar_close"],
                row["family"],
                row["direction"],
                row["level_name"],
                row["level_value"],
                row["trigger_price"],
                row["episode_id"],
                _bool(row["market_aligned"]),
                json.dumps(row, sort_keys=True),
            ),
        )

    for outcome in outcomes:
        row = outcome.to_row()
        conn.execute(
            """INSERT OR REPLACE INTO non_strat_outcomes
               (observer_version, symbol, session_date, family, direction,
                episode_id, event_bar_start, horizon, close_return_bps, mfe_bps,
                mae_bps, row_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                row["observer_version"],
                row["symbol"],
                row["session_date"],
                row["family"],
                row["direction"],
                row["episode_id"],
                row["event_bar_start"],
                row["horizon"],
                row["close_return_bps"],
                row["mfe_bps"],
                row["mae_bps"],
                json.dumps(row, sort_keys=True),
            ),
        )

    conn.execute(
        """INSERT INTO non_strat_runs
           (observer_id, observer_version, session_date, universe_source,
            symbols_requested, symbols_observable, events, episodes, summary_json)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            OBSERVER_ID,
            OBSERVER_VERSION,
            session_date,
            universe_source,
            symbols_requested,
            sum(1 for reason in observability.values() if not reason),
            len(events),
            len({event.episode_id for event in events}),
            json.dumps(summary, sort_keys=True),
        ),
    )
    conn.commit()


def print_summary(summary: dict[str, Any], observability: dict[str, str] | None = None) -> None:
    print(
        f"{summary['observer_id']} {summary['observer_version']} "
        f"timeframe={summary['timeframe']} events={summary['events']} "
        f"episodes={summary['episodes']}"
    )
    if observability is not None:
        good = sum(1 for reason in observability.values() if not reason)
        print(f"symbols: requested={len(observability)} observable={good}")
        for symbol, reason in sorted(observability.items()):
            if reason:
                print(f"  not observable: {symbol}: {reason}")
    print("[families]")
    for family, row in sorted(
        summary["families"].items(), key=lambda item: (-item[1]["episodes"], item[0])
    ):
        print(
            f"  {family:28s} events={row['events']:4d} episodes={row['episodes']:4d} "
            f"aligned={row['aligned']:4d} eod_mean_bps={row['eod_mean_close_return_bps']}"
        )
    print("[blocked_by_design]")
    for reason in summary["blocked_by_design"]:
        print(f"  {reason}")


def report_from_db(path: Path, session_date: str) -> int:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            """SELECT summary_json FROM non_strat_runs
               WHERE observer_version=? AND session_date=?
               ORDER BY id DESC LIMIT 1""",
            (OBSERVER_VERSION, session_date),
        ).fetchone()
        symbols = conn.execute(
            """SELECT symbol, reason FROM non_strat_symbols
               WHERE observer_version=? AND session_date=?
               ORDER BY symbol""",
            (OBSERVER_VERSION, session_date),
        ).fetchall()
    finally:
        conn.close()
    if row is None:
        print(f"no {OBSERVER_VERSION} run for {session_date}", file=sys.stderr)
        return 2
    print_summary(json.loads(row[0]), {symbol: reason for symbol, reason in symbols})
    return 0


async def run(args: argparse.Namespace) -> int:
    day = date.fromisoformat(args.date)
    target = nyse_session_for(day)
    if target is None:
        print(f"{day} is not an NYSE session", file=sys.stderr)
        return 2

    sessions = sessions_through(day, args.lookback_days)
    prior_sessions = [session for session in sessions if session.date < target.date]
    if not prior_sessions:
        print("no prior NYSE session available in lookback", file=sys.stderr)
        return 2
    prior = prior_sessions[-1]

    if args.symbols:
        universe = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        source = "cli"
    else:
        path = Path(args.universe) if args.universe else DEFAULT_UNIVERSE
        universe = load_universe(path)
        source = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
    universe = list(dict.fromkeys(universe))
    wanted = list(dict.fromkeys([*universe, *INDEX_SYMBOLS]))

    api_key, secret_key = resolve_alpaca_credentials()
    if not api_key or not secret_key:
        print("Alpaca credentials not configured", file=sys.stderr)
        return 2
    provider = AlpacaBarProvider(
        base_url=os.environ.get(
            "ALPACA_DATA_BASE_URL", "https://data.alpaca.markets"
        ).rstrip("/"),
        api_key=api_key,
        secret_key=secret_key,
        feed=CONSOLIDATED_FEED,
    )

    start = sessions[0].open
    bars5, provider_errors = await fetch_all(
        provider, wanted, MINUTE_5, start, target.close
    )

    observability: dict[str, str] = {}
    sliced: dict[str, list] = {}
    prior_sliced: dict[str, list] = {}
    history: dict[str, list] = {}

    for symbol in wanted:
        if symbol in provider_errors:
            observability[symbol] = f"provider:{provider_errors[symbol]}"
            continue
        all_bars = bars5.get(symbol, [])
        target_bars = _session_slice(all_bars, target)
        prior_bars = _session_slice(all_bars, prior)
        if not target_bars:
            observability[symbol] = "missing_target_session"
            continue
        if not prior_bars:
            observability[symbol] = "missing_prior_session"
            continue
        if not _complete(target_bars, target):
            observability[symbol] = "target_session_incomplete"
            continue
        if not _complete(prior_bars, prior):
            observability[symbol] = "prior_session_incomplete"
            continue
        observability[symbol] = ""
        sliced[symbol] = target_bars
        prior_sliced[symbol] = prior_bars
        history[symbol] = _history_before(all_bars, target)

    # Index context is metadata only, but if absent we record unknown alignment
    # rather than inventing it. A missing index does not erase the raw event.
    spy_history = history.get("SPY")
    spy_session = sliced.get("SPY")
    qqq_history = history.get("QQQ")
    qqq_session = sliced.get("QQQ")

    events: list[NonStratEvent] = []
    for symbol in universe:
        if observability.get(symbol):
            continue
        events.extend(
            observe_session(
                symbol=symbol,
                session_date=target.date.isoformat(),
                prior_session_bars=prior_sliced[symbol],
                session_bars=sliced[symbol],
                history_bars=history[symbol],
                spy_history=spy_history,
                spy_session=spy_session,
                qqq_history=qqq_history,
                qqq_session=qqq_session,
            )
        )

    outcomes = measure_outcomes(
        events,
        {symbol: sliced[symbol] for symbol in universe if symbol in sliced},
    )
    summary = summarize(events, outcomes)
    summary["session_date"] = target.date.isoformat()
    summary["symbols_requested"] = len(universe)
    summary["symbols_observable"] = sum(
        1 for symbol in universe if not observability.get(symbol)
    )
    summary["provider_errors"] = {
        symbol: reason for symbol, reason in provider_errors.items() if symbol in wanted
    }
    print_summary(summary, {symbol: observability.get(symbol, "") for symbol in universe})

    if args.dry_run:
        print("(dry run: nothing written)")
        return 0

    sqlite_path = Path(
        args.sqlite
        or os.environ.get("OPTIONS_NON_STRAT_COVERAGE_SQLITE_PATH")
        or DEFAULT_SQLITE
    )
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(sqlite_path)
    try:
        write_rows(
            conn,
            session_date=target.date.isoformat(),
            universe_source=source,
            symbols_requested=len(universe),
            observability={symbol: observability.get(symbol, "") for symbol in universe},
            events=events,
            outcomes=outcomes,
            summary=summary,
        )
    finally:
        conn.close()
    print(f"wrote {len(events)} events and {len(outcomes)} outcome views to {sqlite_path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Observer-only 5m non-Strat options coverage"
    )
    parser.add_argument("--date", help="Completed NYSE session YYYY-MM-DD")
    parser.add_argument("--report", metavar="DATE", help="Read stored summary only")
    parser.add_argument("--symbols", help="Comma-separated universe override")
    parser.add_argument("--universe", help="CSV with Ticker column")
    parser.add_argument("--sqlite", help="Dedicated observer sqlite path")
    parser.add_argument("--lookback-days", type=int, default=LOOKBACK_DAYS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    sqlite_path = Path(
        args.sqlite
        or os.environ.get("OPTIONS_NON_STRAT_COVERAGE_SQLITE_PATH")
        or DEFAULT_SQLITE
    )
    if args.report:
        return report_from_db(sqlite_path, args.report)
    if not args.date:
        parser.error("--date or --report is required")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
