"""Pure normalization for Public chart bars used by options evidence collectors.

Public's DAY chart exposes regular-session 5-minute bars plus a live partial row,
and WEEK exposes regular-session 30-minute bars plus session-close/current partial
rows.  This module admits only grid-aligned intervals that are causally complete
at the caller-supplied decision timestamp.  It performs no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Mapping

from .causal_bars import Bar, Timeframe
from .session_calendar import Session

PUBLIC_CHART_SOURCE = "public:/userapigateway/historicdata/{type}/{symbol}/{period}"


@dataclass(frozen=True)
class PublicChartBars:
    bars: tuple[Bar, ...]
    ignored_live_or_partial_rows: int
    ignored_off_grid_rows: int
    ignored_outside_session_rows: int


def _aware(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("public chart bar timestamp missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("public chart bar timestamp invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("public chart bar timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"public chart {name} invalid")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"public chart {name} invalid") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"public chart {name} invalid")
    return parsed


def _bar(item: Mapping[str, Any], start: datetime) -> Bar:
    open_ = _number(item.get("open"), "open")
    high = _number(item.get("high"), "high")
    low = _number(item.get("low"), "low")
    close = _number(item.get("close"), "close")
    volume = _number(item.get("volume", 0), "volume")
    if high < low or not (low <= open_ <= high) or not (low <= close <= high):
        raise ValueError("public chart OHLC geometry invalid")
    if volume < 0:
        raise ValueError("public chart volume invalid")
    return Bar(start=start, open=open_, high=high, low=low, close=close, volume=volume, vwap=None)


def parse_regular_market_bars(
    payload: Mapping[str, Any],
    *,
    timeframe: Timeframe,
    decision_ts: datetime,
    session: Session,
) -> PublicChartBars:
    """Return only complete regular-session grid bars known by ``decision_ts``.

    Off-grid rows are ignored rather than rounded. Public emits a live point in
    DAY responses whose timestamp is the request-time instant; treating it as a
    canonical 5-minute start would manufacture a bar.  Likewise, an aligned
    interval whose close is still in the future is partial and excluded.
    """

    if decision_ts.tzinfo is None or decision_ts.utcoffset() is None:
        raise ValueError("decision_ts must be timezone-aware")
    point = decision_ts.astimezone(timezone.utc)
    regular = payload.get("regularMarket")
    if not isinstance(regular, Mapping):
        raise ValueError("public chart regularMarket missing")
    items = regular.get("bars")
    if not isinstance(items, list):
        raise ValueError("public chart regularMarket bars missing")

    open_utc = session.open.astimezone(timezone.utc)
    close_utc = session.close.astimezone(timezone.utc)
    step = timeframe.seconds
    out: list[Bar] = []
    seen: set[datetime] = set()
    partial = off_grid = outside = 0

    for raw in items:
        if not isinstance(raw, Mapping):
            raise ValueError("public chart bar row invalid")
        start = _aware(raw.get("timestamp"))
        if start < open_utc or start >= close_utc:
            outside += 1
            continue
        offset = (start - open_utc).total_seconds()
        if start.second != 0 or start.microsecond != 0 or offset < 0 or offset % step != 0:
            off_grid += 1
            continue
        if start + timeframe.delta > point:
            partial += 1
            continue
        if start in seen:
            raise ValueError(f"duplicate public chart bar start: {start.isoformat()}")
        seen.add(start)
        out.append(_bar(raw, start))

    out.sort(key=lambda item: item.start_utc)
    return PublicChartBars(
        bars=tuple(out),
        ignored_live_or_partial_rows=partial,
        ignored_off_grid_rows=off_grid,
        ignored_outside_session_rows=outside,
    )
