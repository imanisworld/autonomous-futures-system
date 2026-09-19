"""Proven CME equity-index trading-day identity shared by runtime and replay.

This module contains the C14 calendar rule proven against TradingView Pine
time_tradingday fixtures for MES/MNQ. It is intentionally small and has no
runtime side effects so evidence collectors and replay use the same day key.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
_UTC = timezone.utc

CME_EQUITY_INDEX_INSTRUMENTS = frozenset({"MES", "MNQ", "M2K"})
CALENDAR_CME_EQUITY_INDEX = "cme_equity_index"


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    candidate = next_month - timedelta(days=1)
    return candidate - timedelta(days=(candidate.weekday() - weekday) % 7)


def _observed_fixed_holiday(year: int, month: int, day: int) -> date:
    holiday = date(year, month, day)
    if holiday.weekday() == 5:
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:
        return holiday + timedelta(days=1)
    return holiday


def cme_equity_index_non_trade_dates(year: int) -> frozenset[date]:
    """Civil dates that are not CME equity-index futures trade dates."""
    closed = {
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _last_weekday(year, 5, 0),
        _observed_fixed_holiday(year, 7, 4),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed_fixed_holiday(year, 12, 25),
    }
    if date(year, 1, 1).weekday() != 5:
        closed.add(_observed_fixed_holiday(year, 1, 1))
    if year >= 2022:
        closed.add(_observed_fixed_holiday(year, 6, 19))
    return frozenset(closed)


def trading_day_calendar(instrument: str) -> str | None:
    if str(instrument).strip().upper() in CME_EQUITY_INDEX_INSTRUMENTS:
        return CALENDAR_CME_EQUITY_INDEX
    return None


def _mechanical_1800_day(et: datetime) -> date:
    return et.date() + (timedelta(days=1) if et.time() >= time(18, 0) else timedelta(0))


def cme_trading_day(ts: int | datetime, instrument: str) -> date:
    """Return the exchange trade date used by Pine/CME daily identity."""
    if isinstance(ts, int):
        dt = datetime.fromtimestamp(ts, tz=_UTC)
    else:
        dt = ts
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_UTC)
    et = dt.astimezone(_ET)
    day = _mechanical_1800_day(et)
    if trading_day_calendar(instrument) != CALENDAR_CME_EQUITY_INDEX:
        return day
    closed = (
        cme_equity_index_non_trade_dates(day.year)
        | cme_equity_index_non_trade_dates(day.year + 1)
    )
    while day.weekday() >= 5 or day in closed:
        day += timedelta(days=1)
    return day
