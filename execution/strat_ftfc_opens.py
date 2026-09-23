"""Strat full-timeframe-continuity (FTFC) opens for observation labels.

TheStrat FTFC compares the last price with the open of the current month,
week, trading day and 60-minute bar: above all four = UP, below all four =
DOWN, anything else = CONFLICT. This is NOT the ``ftfc_direction`` field that
comes from ``context/htf_loader`` (a daily + 4H trend proxy).

Pure bookkeeping over 15m bars, kept in a small JSON-serialisable dict so the
observation campaign can persist it in its own state file. An open is only
recorded when it is provably the real open:

- day open: the trading day's first bar is the session-start bar;
- week / month open: that day open, on the first trading day of a new ISO
  week / calendar month, and only when the previous trading day seen is at
  most 4 calendar days earlier (so a bot outage cannot promote a mid-week day
  to "week open");
- 60-minute open: the bar that starts at the top of the ET clock hour.

Anything unproven stays ``None`` and the label reads UNKNOWN. Opens are NOT
back-adjusted across a continuous-contract roll (``roll_adjusted: False``).

Label only: nothing here filters, gates or places anything.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

DEFINITION = "strat_ftfc_opens_v1"
_ET = ZoneInfo("America/New_York")
_MAX_CONTINUITY_DAYS = 4
OPEN_NAMES = ("month", "week", "day", "hour")


def empty_tracker() -> dict[str, Any]:
    return {
        "last_ts": None, "trading_date": None,
        "day": None, "week_key": None, "week": None,
        "month_key": None, "month": None, "hour_key": None, "hour": None,
    }


def _hour_key(ts: datetime) -> str:
    return ts.astimezone(_ET).replace(minute=0, second=0, microsecond=0).isoformat()


def update(tracker: dict[str, Any], *, bar_ts: datetime, bar_open: float,
           trading_date: date, is_session_start: bool) -> dict[str, Any]:
    """Fold one 15m bar into the tracker (in place). Stale/duplicate bars are ignored."""
    ts_iso = bar_ts.isoformat()
    if tracker.get("last_ts") is not None and ts_iso <= tracker["last_ts"]:
        return tracker
    td_iso = trading_date.isoformat()
    if td_iso != tracker.get("trading_date"):
        previous = tracker.get("trading_date")
        gap_ok = False
        if previous is not None:
            gap = (trading_date - date.fromisoformat(previous)).days
            gap_ok = 0 < gap <= _MAX_CONTINUITY_DAYS
        day_open = float(bar_open) if is_session_start else None
        week_key = "%04d-W%02d" % tuple(trading_date.isocalendar())[:2]
        month_key = trading_date.strftime("%Y-%m")
        if week_key != tracker.get("week_key"):
            tracker["week_key"] = week_key
            tracker["week"] = day_open if gap_ok else None
        if month_key != tracker.get("month_key"):
            tracker["month_key"] = month_key
            tracker["month"] = day_open if gap_ok else None
        tracker["trading_date"] = td_iso
        tracker["day"] = day_open
    key = _hour_key(bar_ts)
    if key != tracker.get("hour_key"):
        tracker["hour_key"] = key
        tracker["hour"] = float(bar_open) if bar_ts.astimezone(_ET).minute == 0 else None
    tracker["last_ts"] = ts_iso
    return tracker


def evaluate(tracker: dict[str, Any], *, bar_ts: datetime, close: float,
             trading_date: date) -> dict[str, Any]:
    """FTFC state at this bar's close from the tracker AFTER it saw this bar."""
    current = (tracker.get("trading_date") == trading_date.isoformat()
               and tracker.get("hour_key") == _hour_key(bar_ts))
    opens = {name: (tracker.get(name) if current else None) for name in OPEN_NAMES}
    if any(value is None for value in opens.values()):
        state = "UNKNOWN"
    elif all(close > value for value in opens.values()):
        state = "UP"
    elif all(close < value for value in opens.values()):
        state = "DOWN"
    else:
        state = "CONFLICT"
    return {"definition": DEFINITION, "state": state, "opens": opens, "close": float(close),
            "roll_adjusted": False}


def alignment(direction: str, state: str) -> str:
    """aligned / against / conflict / unknown for a LONG or SHORT idea."""
    if state == "UNKNOWN":
        return "unknown"
    if state == "CONFLICT":
        return "conflict"
    up = str(direction).upper() == "LONG"
    return "aligned" if (state == "UP") == up else "against"
