"""stocks_advisory/csv_loader.py

Local-file-only CSV ingestion for the Stock/ETF Backtest v1 engine
(`tqqq_sqqq_backtest.py`). Converts raw QQQ/TQQQ/SQQQ bar exports into
the `Bar`/`DaySession` objects `run_backtest()` consumes.

Research/backtest only. Reads exactly the file paths the caller
supplies -- no network fetch, no provider/API client, no broker, no
clock access (every "today"/"previous day" notion here comes from the
dates present in the supplied files, never `datetime.now()`). Imports
nothing from `execution`, `webhook`, `strategy`, `risk`, or
`options_manager`.

Accepts the TradingView/BATS export shape used for the first real-data
run (`time,open,high,low,close,Bar Type 1 Label,Bar Type 2 Label,
Bar Type 3 Label,Volume`) as well as a plain `timestamp,open,high,low,
close,volume` header -- column matching is case-insensitive and only
the required OHLCV + timestamp columns are read; unrecognized columns
(e.g. the "Bar Type N Label" fields) are ignored rather than rejected.

Regular-hours filter assumes the supplied timestamps already carry the
correct US-equities local (ET) UTC offset, as the BATS/TradingView
export does -- it compares wall-clock time-of-day directly rather than
re-deriving a timezone, so it stays correct across the DST boundary
without adding a timezone-database dependency.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Optional

from .backtest_models import Bar, DaySession

RTH_START = dt_time(9, 30)
RTH_END = dt_time(16, 0)

_TIMESTAMP_COLUMN_ALIASES = ("timestamp", "time")
_REQUIRED_NUMERIC_COLUMNS = ("open", "high", "low", "close", "volume")


class CsvValidationError(ValueError):
    """Raised when a CSV file is missing a required column. Never
    raised for a data-quality issue in the rows themselves -- those are
    reported back to the caller as skip reasons instead of raised."""


@dataclass(frozen=True, kw_only=True)
class LoadedSymbolCsv:
    symbol: str
    path: str
    all_bars: tuple[Bar, ...]
    rth_bars: tuple[Bar, ...]
    rows_read: int
    rows_outside_regular_hours: int


@dataclass(frozen=True, kw_only=True)
class SessionBuildReport:
    """Full accounting of how many candidate trading days made it into
    a built `DaySession`, and why any day that did not was excluded --
    the "validate: no missing execution bars" check the caller asked
    for is this report, not a silent drop."""

    qqq_dates: tuple[str, ...]
    tqqq_dates: tuple[str, ...]
    sqqq_dates: tuple[str, ...]
    common_dates: tuple[str, ...]
    sessions_built: tuple[str, ...]
    excluded_dates: tuple[tuple[str, str], ...]  # (date, reason)


def _find_column(header: list[str], aliases: tuple[str, ...]) -> Optional[str]:
    lowered = {col.lower(): col for col in header}
    for alias in aliases:
        if alias in lowered:
            return lowered[alias]
    return None


def load_bars_from_csv(path: str | Path) -> LoadedSymbolCsv:
    """Parses one CSV file into `Bar` objects, sorted by timestamp.

    Raises `CsvValidationError` if the header is missing the timestamp
    column or any required OHLCV column. Extra columns (label/indicator
    columns some export tools add) are ignored.
    """
    path = Path(path)
    symbol = path.stem.split("_")[-2] if "_" in path.stem else path.stem
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        ts_column = _find_column(header, _TIMESTAMP_COLUMN_ALIASES)
        if ts_column is None:
            raise CsvValidationError(
                f"{path}: missing a timestamp column (expected one of {_TIMESTAMP_COLUMN_ALIASES})"
            )
        numeric_columns: dict[str, str] = {}
        missing = []
        for name in _REQUIRED_NUMERIC_COLUMNS:
            found = _find_column(header, (name,))
            if found is None:
                missing.append(name)
            else:
                numeric_columns[name] = found
        if missing:
            raise CsvValidationError(f"{path}: missing required column(s): {', '.join(missing)}")

        bars: list[Bar] = []
        rows_read = 0
        for row in reader:
            rows_read += 1
            raw_ts = row[ts_column]
            parsed = datetime.fromisoformat(raw_ts)
            bars.append(
                Bar(
                    timestamp=parsed.isoformat(),
                    open=float(row[numeric_columns["open"]]),
                    high=float(row[numeric_columns["high"]]),
                    low=float(row[numeric_columns["low"]]),
                    close=float(row[numeric_columns["close"]]),
                    volume=int(float(row[numeric_columns["volume"]])),
                )
            )

    bars.sort(key=lambda b: b.timestamp)
    rth_bars = [b for b in bars if RTH_START <= datetime.fromisoformat(b.timestamp).time() < RTH_END]
    return LoadedSymbolCsv(
        symbol=symbol.upper(),
        path=str(path),
        all_bars=tuple(bars),
        rth_bars=tuple(rth_bars),
        rows_read=rows_read,
        rows_outside_regular_hours=len(bars) - len(rth_bars),
    )


def _group_by_date(bars: tuple[Bar, ...]) -> dict[str, list[Bar]]:
    grouped: dict[str, list[Bar]] = {}
    for bar in bars:
        date_key = datetime.fromisoformat(bar.timestamp).date().isoformat()
        grouped.setdefault(date_key, []).append(bar)
    return grouped


def build_day_sessions(
    qqq: LoadedSymbolCsv,
    tqqq: LoadedSymbolCsv,
    sqqq: LoadedSymbolCsv,
) -> tuple[list[DaySession], SessionBuildReport]:
    """Builds one `DaySession` per date where all three symbols have
    regular-hours data AND a prior QQQ regular-hours session exists in
    the same file to supply `qqq_previous_close/high/low` AND the
    day's first QQQ bar starts at the regular-hours open (09:30) -- a
    later first bar means the file is missing the open print for that
    date, which would silently corrupt the gap-percent calculation, so
    that date is excluded rather than evaluated on a truncated day.
    """
    qqq_by_date = _group_by_date(qqq.rth_bars)
    tqqq_by_date = _group_by_date(tqqq.rth_bars)
    sqqq_by_date = _group_by_date(sqqq.rth_bars)

    qqq_dates = sorted(qqq_by_date)
    common_dates = sorted(set(qqq_dates) & set(tqqq_by_date) & set(sqqq_by_date))

    sessions: list[DaySession] = []
    excluded: list[tuple[str, str]] = []
    for i, date_key in enumerate(qqq_dates):
        if date_key not in common_dates:
            continue
        day_bars = sorted(qqq_by_date[date_key], key=lambda b: b.timestamp)
        first_bar_time = datetime.fromisoformat(day_bars[0].timestamp).time()
        if first_bar_time != RTH_START:
            excluded.append(
                (date_key, f"missing regular-hours open bar (first bar at {first_bar_time.isoformat()}, expected {RTH_START.isoformat()})")
            )
            continue
        if i == 0:
            excluded.append((date_key, "no prior QQQ session in file to supply qqq_previous_close/high/low"))
            continue
        prev_date_key = qqq_dates[i - 1]
        prev_bars = sorted(qqq_by_date[prev_date_key], key=lambda b: b.timestamp)

        sessions.append(
            DaySession(
                date=date_key,
                qqq_previous_close=prev_bars[-1].close,
                qqq_previous_high=max(b.high for b in prev_bars),
                qqq_previous_low=min(b.low for b in prev_bars),
                qqq_bars=tuple(day_bars),
                tqqq_bars=tuple(sorted(tqqq_by_date[date_key], key=lambda b: b.timestamp)),
                sqqq_bars=tuple(sorted(sqqq_by_date[date_key], key=lambda b: b.timestamp)),
            )
        )

    for date_key in sorted(set(qqq_dates) - set(common_dates)):
        reason = []
        if date_key not in tqqq_by_date:
            reason.append("no TQQQ regular-hours data")
        if date_key not in sqqq_by_date:
            reason.append("no SQQQ regular-hours data")
        excluded.append((date_key, "; ".join(reason) or "not common to all three symbols"))

    report = SessionBuildReport(
        qqq_dates=tuple(qqq_dates),
        tqqq_dates=tuple(sorted(tqqq_by_date)),
        sqqq_dates=tuple(sorted(sqqq_by_date)),
        common_dates=tuple(common_dates),
        sessions_built=tuple(s.date for s in sessions),
        excluded_dates=tuple(sorted(excluded)),
    )
    return sessions, report
