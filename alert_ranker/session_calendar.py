"""Equity market session calendar — the authority on whether a session exists.

Measured on the VPS (2026-09-01): a market holiday can still return intraday
bars while having no session and no daily bar (2025-11-27 returned 23 AAPL
one-minute bars). Presence of bars therefore proves nothing about whether the
market was open, and session boundaries must come from a calendar.

The regular session also moves in UTC across daylight saving (13:30-20:00Z
under EDT, 14:30-21:00Z under EST) and shortens to a 13:00 ET close on early
closes such as 2025-11-28 and 2025-12-24. Both are handled by converting the
calendar's exchange-local times through the exchange timezone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable, Protocol
from zoneinfo import ZoneInfo

import httpx

__all__ = [
    "Session",
    "SessionCalendar",
    "StaticSessionCalendar",
    "AlpacaSessionCalendar",
    "SessionCalendarError",
    "EXCHANGE_TIMEZONE",
    "REGULAR_CLOSE",
    "calendar_url",
    "nyse_session_for",
]

EXCHANGE_TIMEZONE = "America/New_York"
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)


class SessionCalendarError(RuntimeError):
    """Raised when the calendar cannot be established. Never guessed around."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class Session:
    """One regular trading session, with UTC boundaries."""

    date: date
    open: datetime
    close: datetime
    is_early_close: bool = False

    @property
    def minutes(self) -> int:
        return int((self.close - self.open).total_seconds() // 60)


class SessionCalendar(Protocol):
    async def session_for(self, day: date) -> Session | None:
        """The session for ``day``, or ``None`` when the market was closed."""
        ...


@dataclass
class StaticSessionCalendar:
    """In-memory calendar. Used by tests and by any caller with a fixed set."""

    sessions: dict[date, Session]

    @classmethod
    def from_sessions(cls, sessions: Iterable[Session]) -> "StaticSessionCalendar":
        return cls({session.date: session for session in sessions})

    async def session_for(self, day: date) -> Session | None:
        return self.sessions.get(day)


def _observed_fixed_holiday(year: int, month: int, day: int) -> date:
    holiday = date(year, month, day)
    if holiday.weekday() == 5:
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:
        return holiday + timedelta(days=1)
    return holiday


def _easter_sunday(year: int) -> date:
    """Return Gregorian Easter using the Meeus/Jones/Butcher algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f, g = divmod(b + 8, 25)
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    candidate = next_month - timedelta(days=1)
    return candidate - timedelta(days=(candidate.weekday() - weekday) % 7)


def _nyse_closed_days(year: int) -> set[date]:
    closed = {
        _observed_fixed_holiday(year, 1, 1),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _easter_sunday(year) - timedelta(days=2),
        _last_weekday(year, 5, 0),
        _observed_fixed_holiday(year, 7, 4),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed_fixed_holiday(year, 12, 25),
    }
    if year >= 2022:
        closed.add(_observed_fixed_holiday(year, 6, 19))
    return closed


def nyse_session_for(day: date) -> Session | None:
    """Return the NYSE regular session, including holidays and early closes."""
    if day.weekday() >= 5 or day in _nyse_closed_days(day.year):
        return None
    close = REGULAR_CLOSE
    if day == _nth_weekday(day.year, 11, 3, 4) + timedelta(days=1):
        close = EARLY_CLOSE
    christmas = date(day.year, 12, 25)
    christmas_eve = christmas - timedelta(days=2) if christmas.weekday() == 6 else christmas - timedelta(days=1)
    if day == christmas_eve:
        close = EARLY_CLOSE
    independence = date(day.year, 7, 4)
    independence_eve = independence - timedelta(days=2) if independence.weekday() == 6 else independence - timedelta(days=1)
    if day == independence_eve:
        close = EARLY_CLOSE
    tz = ZoneInfo(EXCHANGE_TIMEZONE)
    open_local = datetime.combine(day, time(9, 30), tzinfo=tz)
    close_local = datetime.combine(day, close, tzinfo=tz)
    return Session(day, open_local.astimezone(timezone.utc), close_local.astimezone(timezone.utc), close < REGULAR_CLOSE)



def calendar_url(base_url: str) -> str:
    """Build the calendar URL from a configured API base.

    The deployed ``ALPACA_ENDPOINT`` already ends in ``/v2``; naively appending
    ``/v2/calendar`` produced a 404 during the evidence pass. Normalise so
    either spelling of the base works.
    """
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise SessionCalendarError("calendar_unconfigured", "no API base URL configured")
    if base.endswith("/v2"):
        base = base[: -len("/v2")]
    return f"{base}/v2/calendar"


def parse_session(entry: dict[str, Any], tz_name: str = EXCHANGE_TIMEZONE) -> Session:
    """Convert one calendar entry (exchange-local strings) into a UTC session."""
    try:
        day = date.fromisoformat(str(entry["date"]))
        open_h, open_m = (int(part) for part in str(entry["open"]).split(":"))
        close_h, close_m = (int(part) for part in str(entry["close"]).split(":"))
    except (KeyError, ValueError, TypeError) as exc:
        raise SessionCalendarError("calendar_malformed", str(exc)) from exc

    tz = ZoneInfo(tz_name)
    open_local = datetime.combine(day, time(open_h, open_m), tzinfo=tz)
    close_local = datetime.combine(day, time(close_h, close_m), tzinfo=tz)
    if close_local <= open_local:
        raise SessionCalendarError(
            "calendar_malformed", f"{day}: close {close_local} not after open {open_local}"
        )
    return Session(
        date=day,
        open=open_local.astimezone(timezone.utc),
        close=close_local.astimezone(timezone.utc),
        is_early_close=time(close_h, close_m) < REGULAR_CLOSE,
    )


class AlpacaSessionCalendar:
    """Session calendar backed by the broker's read-only calendar endpoint.

    Read-only: this issues a single GET against the calendar path and touches
    no order, position or account endpoint.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        secret_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
        tz_name: str = EXCHANGE_TIMEZONE,
    ) -> None:
        self._url = calendar_url(base_url)
        self._headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
            "Accept": "application/json",
        }
        self._client = client
        self._timeout = timeout
        self._tz_name = tz_name
        self._cache: dict[date, Session] = {}
        self._fetched_days: set[date] = set()

    async def session_for(self, day: date) -> Session | None:
        if day not in self._fetched_days:
            await self._load(day)
        return self._cache.get(day)

    async def _load(self, day: date) -> None:
        params = {"start": day.isoformat(), "end": day.isoformat()}
        client = self._client
        owns = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await client.get(self._url, params=params, headers=self._headers)
        except httpx.HTTPError as exc:
            raise SessionCalendarError("calendar_unavailable", str(exc)) from exc
        finally:
            if owns:
                await client.aclose()

        if response.status_code != 200:
            raise SessionCalendarError(
                "calendar_unavailable", f"HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise SessionCalendarError("calendar_malformed", str(exc)) from exc
        if not isinstance(payload, list):
            raise SessionCalendarError("calendar_malformed", "expected a list of sessions")

        # A holiday is simply absent from the response. Record the day as
        # fetched anyway so the absence is cached as "no session" rather than
        # retried into an accidental guess.
        for entry in payload:
            if not isinstance(entry, dict):
                raise SessionCalendarError("calendar_malformed", "non-object entry")
            session = parse_session(entry, self._tz_name)
            self._cache[session.date] = session
        self._fetched_days.add(day)
