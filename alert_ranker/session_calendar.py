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
from datetime import date, datetime, time, timezone
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
]

EXCHANGE_TIMEZONE = "America/New_York"
REGULAR_CLOSE = time(16, 0)


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
