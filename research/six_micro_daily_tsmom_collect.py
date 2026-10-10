"""Write completed sessions into the six-micro paper journal.

Nasdaq, S&P, and Russell use the scheduled quarterly front. Gold, crude, and
bitcoin use the exchange listing that expires soonest and is still open that
day. That is the dated ticker on the bar. It is not the highest-volume
contract. This does not place an order and does not score the study.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from research.six_micro_daily_tsmom_paper import (
    PaperLedger,
    SCHEDULED_ROOTS,
    SessionPrint,
)
from sources.polygon_client import PolygonBar, PolygonError, PolygonFuturesClient, contract_schedule

_ROOT_MONTHS = {
    "MGC": "GJMQVZ",
    "MCL": "FGHJKMNQUVXZ",
    "MBT": "FGHJKMNQUVXZ",
}

ET = ZoneInfo("America/New_York")
RTH_OPEN = time(9, 30)
RTH_LAST_BAR = time(15, 45)
WARMUP_START = date(2026, 6, 29)
UNSCHEDULED_ROOTS = ("MGC", "MCL", "MBT")
_POLYGON_ENV_KEYS = frozenset({"POLYGON_API_KEY", "POLYGON_BASE_URL"})
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class CollectResult:
    sessions: dict[str, int]
    round_turns: int
    skipped_roots: tuple[str, ...]


def completed_through(now: datetime) -> date:
    """Last weekday whose 16:00 New York close has already happened."""
    local = now.astimezone(ET)
    day = local.date()
    if local.weekday() >= 5 or local.time() < time(16, 0):
        day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def session_prints(
    root: str,
    bars: list[PolygonBar],
    *,
    start: date,
    end: date,
) -> list[SessionPrint]:
    """One print per day that has both the 09:30 open and the 15:45 bar."""
    by_day: dict[date, list[PolygonBar]] = {}
    for bar in bars:
        local = bar.ts.astimezone(ET)
        session = local.date()
        if session < start or session > end or local.weekday() >= 5:
            continue
        if local.time() < RTH_OPEN or local.time() > RTH_LAST_BAR:
            continue
        by_day.setdefault(session, []).append(bar)

    prints: list[SessionPrint] = []
    for session in sorted(by_day):
        day_bars = sorted(by_day[session], key=lambda bar: bar.ts)
        opening = next((bar for bar in day_bars if bar.ts.astimezone(ET).time() == RTH_OPEN), None)
        closing = next((bar for bar in day_bars if bar.ts.astimezone(ET).time() == RTH_LAST_BAR), None)
        if opening is None or closing is None:
            continue
        if opening.ticker.strip().upper() != closing.ticker.strip().upper():
            continue
        prints.append(
            SessionPrint(
                session=session,
                root=root,
                contract=opening.ticker.strip().upper(),
                rth_open=float(opening.open),
                session_close=float(closing.close),
            )
        )
    return prints


def _as_date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def listed_front(root: str, rows: list[dict], day: date) -> str | None:
    """Soonest last-trade date that is already trading and has not expired.

    Month-code order is not the front. Two listings can disagree with it.
    """
    root_name = root.strip().upper()
    candidates: list[tuple[date, str]] = []
    for row in rows:
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker.startswith(root_name):
            continue
        first = _as_date(row.get("first_trade_date"))
        last = _as_date(row.get("last_trade_date"))
        if first is None or last is None:
            continue
        if first <= day <= last:
            candidates.append((last, ticker))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]


def candidate_tickers(root: str, start: date, end: date) -> list[str]:
    """Dated tickers whose expiry month can cover the window. Not a volume rank."""
    allowed = _ROOT_MONTHS[root]
    cursor = date(start.year, start.month, 1) - timedelta(days=1)
    cursor = date(cursor.year, cursor.month, 1)
    stop = end + timedelta(days=70)
    tickers: list[str] = []
    while cursor <= stop:
        code = "FGHJKMNQUVXZ"[cursor.month - 1]
        if code in allowed:
            tickers.append(f"{root}{code}{cursor.year % 10}")
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return tickers


def prints_for_listed_front(
    root: str,
    bars: list[PolygonBar],
    rows: list[dict],
    *,
    start: date,
    end: date,
) -> list[SessionPrint]:
    """One print per day from listed_front, not from the month code."""
    by_ticker: dict[str, list[PolygonBar]] = {}
    for bar in bars:
        by_ticker.setdefault(bar.ticker.strip().upper(), []).append(bar)
    printed: dict[str, dict[date, SessionPrint]] = {}
    for ticker, ticker_bars in by_ticker.items():
        printed[ticker] = {
            item.session: item
            for item in session_prints(root, ticker_bars, start=start, end=end)
        }
    days = sorted({day for by_day in printed.values() for day in by_day})
    chosen: list[SessionPrint] = []
    for day in days:
        ticker = listed_front(root, rows, day)
        if ticker is None:
            continue
        print_ = printed.get(ticker, {}).get(day)
        if print_ is not None:
            chosen.append(print_)
    return chosen


def _with_roll_open(prints: list[SessionPrint], bars: list[PolygonBar]) -> list[SessionPrint]:
    out: list[SessionPrint] = []
    previous: SessionPrint | None = None
    for print_ in prints:
        if previous is not None and previous.contract != print_.contract:
            old_open = next(
                (
                    bar.open
                    for bar in bars
                    if bar.ticker.strip().upper() == previous.contract
                    and bar.ts.astimezone(ET).date() == print_.session
                    and bar.ts.astimezone(ET).time() == RTH_OPEN
                ),
                None,
            )
            if old_open is not None:
                print_ = replace(print_, roll_exit_open=float(old_open))
        out.append(print_)
        previous = print_
    return out


def _fetch_start(ledger: PaperLedger, root: str, start: date, end: date) -> date | None:
    if ledger.bare_close_count(root):
        return start
    stored = ledger.stored_sessions(root)
    if not stored:
        return start
    nxt = max(stored)
    if nxt >= end:
        return None
    return nxt


def _store_prints(ledger: PaperLedger, root: str, prints: list) -> int:
    if ledger.bare_close_count(root):
        ledger.mark_preregistration(root, prints)
    stored = ledger.stored_sessions(root)
    for print_ in prints:
        if print_.session in stored:
            continue
        ledger.on_session(print_)
    return len(ledger.state["closes"].get(root, []))


def collect_listed(
    journal_dir: Path,
    client,
    *,
    start: date = WARMUP_START,
    end: date,
    listings: dict[str, list[dict]] | None = None,
) -> dict[str, int]:
    """Record gold, crude, and bitcoin through ``end`` without touching stored roots.

    The day's contract is ``listed_front`` (soonest last-trade date). Candidate
    month codes only decide which tickers to download. ``listings`` injects
    those rows in tests. Production asks the client for those tickers only
    and does not page a whole product inventory.
    """
    if end > completed_through(datetime.now(ET)):
        raise ValueError("refusing a session that has not closed")
    ledger = PaperLedger(journal_dir)
    counts: dict[str, int] = {}
    for root in UNSCHEDULED_ROOTS:
        fetch_from = _fetch_start(ledger, root, start, end)
        if fetch_from is None:
            counts[root] = len(ledger.state["closes"].get(root, []))
            continue
        tickers = candidate_tickers(root, fetch_from, end)
        bars: list[PolygonBar] = []
        for ticker in tickers:
            try:
                bars.extend(client.fetch_bars(ticker, fetch_from, end, 15))
            except PolygonError:
                continue
        rows = (
            list(listings.get(root, []))
            if listings is not None
            else client.fetch_contract_listings(root, tickers)
        )
        prints = _with_roll_open(
            prints_for_listed_front(root, bars, rows, start=fetch_from, end=end),
            bars,
        )
        counts[root] = _store_prints(ledger, root, prints)
    return counts


def collect_scheduled(
    journal_dir: Path,
    client,
    *,
    start: date = WARMUP_START,
    end: date,
) -> CollectResult:
    """Record scheduled roots through ``end``. Entries stay closed before 2026-10-12."""
    if end > completed_through(datetime.now(ET)):
        raise ValueError("refusing a session that has not closed")
    ledger = PaperLedger(journal_dir)
    counts: dict[str, int] = {}
    for root in SCHEDULED_ROOTS:
        fetch_from = _fetch_start(ledger, root, start, end)
        if fetch_from is None:
            counts[root] = len(ledger.state["closes"].get(root, []))
            continue
        bars = []
        for ticker, seg_start, seg_end in contract_schedule(root, fetch_from, end):
            bars.extend(client.fetch_bars(ticker, seg_start, seg_end, 15))
        prints = _with_roll_open(
            session_prints(root, bars, start=fetch_from, end=end),
            bars,
        )
        counts[root] = _store_prints(ledger, root, prints)
    return CollectResult(
        sessions=counts,
        round_turns=ledger.round_turns,
        skipped_roots=(),
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def running_code_sha(repo: Path | None = None) -> str:
    """Identity of the code that is actually running.

    A built release has ``release_manifest.json``. The immutable release
    directory is named with the same commit and has no ``.git``. A checkout
    falls through to ``git rev-parse HEAD``.
    """
    root = repo or _repo_root()
    manifest = root / "release_manifest.json"
    if manifest.is_file():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            commit = str(payload["repo"]["commit"]).strip().lower()
        except (OSError, json.JSONDecodeError, KeyError, TypeError, AttributeError):
            commit = ""
        if _FULL_SHA.fullmatch(commit):
            return commit
    resolved = root.resolve()
    if resolved.parent.name == "afs-releases" and _FULL_SHA.fullmatch(resolved.name.lower()):
        return resolved.name.lower()
    try:
        out = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip().lower()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return ""
    return out if _FULL_SHA.fullmatch(out) else ""


def verify_release_sha(repo: Path | None = None) -> str:
    """Exit non-zero unless ``AFS_RELEASE_SHA`` is the running commit."""
    expected = os.environ.get("AFS_RELEASE_SHA", "").strip().lower()
    running = running_code_sha(repo)
    if not expected or not running or expected != running:
        raise SystemExit("release SHA pin failed")
    return running


def load_polygon_env(path: Path) -> None:
    """Load a Polygon-only env file. Refuse the shared broker env."""
    if path.name == ".env" or not path.is_file():
        raise SystemExit("env file is not polygon-only")
    keys: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or key not in _POLYGON_ENV_KEYS:
            raise SystemExit("env file is not polygon-only")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        keys[key] = value
    if "POLYGON_API_KEY" not in keys:
        raise SystemExit("env file is not polygon-only")
    for key, value in keys.items():
        os.environ[key] = value


def main(argv: list[str] | None = None) -> None:
    root = _repo_root()
    parser = argparse.ArgumentParser(description="Record completed six-micro sessions.")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--journal", type=Path, default=root / "logs" / "six-micro-daily-tsmom")
    parser.add_argument("--verify-sha", action="store_true")
    args = parser.parse_args(argv)
    verify_release_sha(root)
    if args.verify_sha:
        return
    if args.env_file is None:
        raise SystemExit("env file is not polygon-only")
    load_polygon_env(args.env_file)
    end = completed_through(datetime.now(ET))
    journal = args.journal
    client = PolygonFuturesClient(min_request_interval=13.0)
    scheduled = collect_scheduled(journal, client, end=end)
    listed = collect_listed(journal, client, end=end)
    ledger = PaperLedger(journal)
    sessions = {**scheduled.sessions, **listed}
    pre_registration = {
        name: sum(
            1 for row in rows
            if isinstance(row, dict) and row.get("pre_registration") is True
        )
        for name, rows in ledger.state["closes"].items()
    }
    print(
        f"recorded through {end.isoformat()} "
        f"sessions={sessions} "
        f"pre_registration={pre_registration} "
        f"scoring_round_turns={ledger.scoring_round_turns}"
    )


if __name__ == "__main__":
    main()
