"""Write completed sessions into the six-micro paper journal.

Nasdaq, S&P, and Russell use the scheduled quarterly front. Gold, crude, and
bitcoin stay out until the bar itself names a dated contract. This does not
place an order and does not score the study.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from research.six_micro_daily_tsmom_paper import (
    PaperLedger,
    SCHEDULED_ROOTS,
    SessionPrint,
)
from sources.polygon_client import PolygonBar, PolygonFuturesClient, contract_schedule

ET = ZoneInfo("America/New_York")
RTH_OPEN = time(9, 30)
RTH_LAST_BAR = time(15, 45)
WARMUP_START = date(2026, 6, 29)
UNSCHEDULED_ROOTS = ("MGC", "MCL", "MBT")


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
        bars: list[PolygonBar] = []
        for ticker, seg_start, seg_end in contract_schedule(root, start, end):
            bars.extend(client.fetch_bars(ticker, seg_start, seg_end, 15))
        recorded = 0
        for print_ in session_prints(root, bars, start=start, end=end):
            ledger.on_session(print_)
            recorded += 1
        counts[root] = recorded
    return CollectResult(
        sessions=counts,
        round_turns=ledger.round_turns,
        skipped_roots=UNSCHEDULED_ROOTS,
    )


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / ".env").exists() and (candidate / "research").is_dir():
            return candidate
    return here.parents[1]


def main() -> None:
    root = _repo_root()
    load_dotenv(root / ".env")
    end = completed_through(datetime.now(ET))
    result = collect_scheduled(
        root / "logs" / "six-micro-daily-tsmom",
        PolygonFuturesClient(min_request_interval=13.0),
        end=end,
    )
    print(
        f"recorded through {end.isoformat()} "
        f"sessions={result.sessions} round_turns={result.round_turns} "
        f"skipped={','.join(result.skipped_roots)}"
    )


if __name__ == "__main__":
    main()
