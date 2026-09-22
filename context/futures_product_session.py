"""Fail-closed product-session guard for CME equity-index futures (MNQ/MES).

Answers "is this product's Globex session open at this instant?" with a
structured status, not a bare bool. PREP ONLY: nothing imports this yet. A
later PR may wire it into alerting; this module changes no runtime behavior.

Why not reuse the existing helpers:
  - ``context.futures_session.futures_session_active`` is a broad FEED-HEALTH
    label. It knows weekends and the daily 17:00-18:00 ET halt but no holidays,
    and it fails OPEN on error. That is right for a watchdog, wrong for a guard.
  - ``alert_ranker.session_calendar`` is the NYSE equity RTH calendar. Its
    session is not the Globex session.
Neither is treated as product-calendar proof here.

Holiday data comes only from ``context.cme_trading_day`` (the C14 rule proven
against Pine trade-date fixtures). That data carries DATES, not hours, so:
  - On a listed holiday date, 00:00-18:00 ET is ``holiday_closed``. CME runs an
    abbreviated morning session on some of these days; blocking the whole day
    is the conservative choice.
  - The evening before a holiday (18:00-24:00 ET) is
    ``calendar_unknown_fail_closed``. Globex reopens that evening before some
    holidays (Labor Day) and stays shut before others (Christmas, New Year's);
    the dates alone cannot tell which.
  - Good Friday is not in the repo calendar. CME closes or abbreviates it year
    by year, so it (and the evening before) is ``calendar_unknown_fail_closed``.
  - Early-close eves (day after Thanksgiving, Dec 24, Jul 3) are
    ``special_session_closed`` from 13:00 ET. CME's equity-index early close is
    12:15 CT = 13:15 ET; 13:00 fails closed 15 minutes early.

Anything the guard cannot establish (unsupported root, naive timestamp, year
outside ``SUPPORTED_YEARS``) returns a closed status. It never guesses open.

Pure datetime/zoneinfo; no I/O, no side effects.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from context.cme_trading_day import (
    _nth_weekday,
    cme_equity_index_non_trade_dates,
)

_ET = ZoneInfo("America/New_York")

MARKET_OPEN = "market_open"
MAINTENANCE_HALT = "maintenance_halt"
WEEKEND_CLOSED = "weekend_closed"
HOLIDAY_CLOSED = "holiday_closed"
SPECIAL_SESSION_CLOSED = "special_session_closed"
UNSUPPORTED_INSTRUMENT = "unsupported_instrument"
CALENDAR_UNKNOWN_FAIL_CLOSED = "calendar_unknown_fail_closed"

STATUSES = frozenset({
    MARKET_OPEN,
    MAINTENANCE_HALT,
    WEEKEND_CLOSED,
    HOLIDAY_CLOSED,
    SPECIAL_SESSION_CLOSED,
    UNSUPPORTED_INSTRUMENT,
    CALENDAR_UNKNOWN_FAIL_CLOSED,
})

CALENDAR_CME_EQUITY_INDEX_GLOBEX = "cme_equity_index_globex"
SUPPORTED_INSTRUMENTS = frozenset({"MNQ", "MES"})
# Years the holiday rule has been reviewed for. Outside this range the guard
# fails closed; extending it is a deliberate, reviewed change.
SUPPORTED_YEARS = range(2025, 2028)

_REOPEN = time(18, 0)
_HALT_START = time(17, 0)
_EARLY_CLOSE = time(13, 0)


@dataclass(frozen=True)
class ProductSessionStatus:
    instrument: str
    status: str
    calendar: str | None
    exchange_time: datetime | None
    detail: str = ""

    @property
    def is_open(self) -> bool:
        return self.status == MARKET_OPEN


def _good_friday(year: int) -> date:
    # Meeus/Jones/Butcher Gregorian Easter, minus two days.
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1) - timedelta(days=2)


def _early_close_dates(year: int, holidays: frozenset[date]) -> frozenset[date]:
    candidates = {
        _nth_weekday(year, 11, 3, 4) + timedelta(days=1),  # day after Thanksgiving
        date(year, 12, 24),
        date(year, 7, 3),
    }
    return frozenset(d for d in candidates if d.weekday() < 5 and d not in holidays)


def _holidays_near(day: date) -> frozenset[date]:
    return (
        cme_equity_index_non_trade_dates(day.year)
        | cme_equity_index_non_trade_dates(day.year + 1)
    )


def product_session_status(instrument: str, moment: datetime) -> ProductSessionStatus:
    """Structured, fail-closed Globex session status for ``instrument`` at ``moment``."""
    root = str(instrument or "").strip().upper()
    if root not in SUPPORTED_INSTRUMENTS:
        return ProductSessionStatus(root, UNSUPPORTED_INSTRUMENT, None, None,
                                    "no product calendar for this instrument")

    cal = CALENDAR_CME_EQUITY_INDEX_GLOBEX
    if not isinstance(moment, datetime) or moment.tzinfo is None or moment.utcoffset() is None:
        return ProductSessionStatus(root, CALENDAR_UNKNOWN_FAIL_CLOSED, cal, None,
                                    "timestamp is not timezone-aware")
    et = moment.astimezone(_ET)
    day, t, wd = et.date(), et.time(), et.weekday()
    next_day = day + timedelta(days=1)
    if day.year not in SUPPORTED_YEARS or next_day.year not in SUPPORTED_YEARS:
        return ProductSessionStatus(root, CALENDAR_UNKNOWN_FAIL_CLOSED, cal, et,
                                    f"{day.year} outside reviewed calendar years")

    def result(status: str, detail: str = "") -> ProductSessionStatus:
        return ProductSessionStatus(root, status, cal, et, detail)

    if wd == 5 or (wd == 6 and t < _REOPEN) or (wd == 4 and t >= _HALT_START):
        return result(WEEKEND_CLOSED)

    holidays = _holidays_near(day)
    if day in holidays and t < _REOPEN:
        return result(HOLIDAY_CLOSED, f"{day.isoformat()} is a CME non-trade date")
    if day == _good_friday(day.year) and t < _REOPEN:
        return result(CALENDAR_UNKNOWN_FAIL_CLOSED, "Good Friday hours not in repo calendar")
    if t >= _REOPEN and next_day in holidays:
        return result(CALENDAR_UNKNOWN_FAIL_CLOSED,
                      f"reopen before holiday {next_day.isoformat()} not established")
    if t >= _REOPEN and next_day == _good_friday(next_day.year):
        return result(CALENDAR_UNKNOWN_FAIL_CLOSED, "reopen before Good Friday not established")
    if day in _early_close_dates(day.year, holidays) and t >= _EARLY_CLOSE:
        return result(SPECIAL_SESSION_CLOSED, f"{day.isoformat()} early close")
    if _HALT_START <= t < _REOPEN:
        return result(MAINTENANCE_HALT)
    return result(MARKET_OPEN)
