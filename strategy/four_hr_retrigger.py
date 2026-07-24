"""Canonical executable state machine for the resolved 4HR Re-Trigger.

The module is deliberately pure: callers provide already-arrived 5-minute bars
and the last persisted state, and receive the next state plus an optional entry
candidate.  Live runtime and ordinary replay use this same function.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

from context.bar_history import _parse_dt

ET = ZoneInfo("America/New_York")
_BAR_MINUTES = 5
_SUPPORTED = {"MNQ", "MES", "QQQ"}


def _root(instrument: str) -> str:
    value = str(instrument or "").upper().strip()
    for suffix in ("1!", "2!"):
        if value.endswith(suffix):
            return value[: -len(suffix)]
    return value


def _as_bar(raw: Any) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    parsed = _parse_dt(str(raw.get("ts") or raw.get("timestamp") or ""))
    if parsed is None:
        return None
    try:
        return {
            "ts": parsed.astimezone(ET),
            "open": float(raw["open"]),
            "high": float(raw["high"]),
            "low": float(raw["low"]),
            "close": float(raw["close"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def et_bucket_start(timestamp: datetime, minutes: int) -> datetime:
    """Return an ET wall-clock bucket start, never an epoch-width bucket."""
    local = timestamp.astimezone(ET)
    if minutes == 60:
        hour = local.hour
        minute = 0
    elif minutes == 240:
        hour = (local.hour // 4) * 4
        minute = 0
    else:
        total = local.hour * 60 + local.minute
        bucket = (total // minutes) * minutes
        hour, minute = divmod(bucket, 60)
    return datetime(
        local.year, local.month, local.day, hour, minute, tzinfo=ET
    )


def aggregate_et_bars(bars_5m: Iterable[dict], minutes: int) -> list[dict]:
    """Aggregate 5-minute bars on fixed America/New_York wall-clock anchors."""
    if minutes not in {60, 240}:
        raise ValueError("4HR aggregation supports only 60- and 240-minute bars")
    buckets: dict[datetime, dict] = {}
    for raw in bars_5m:
        bar = _as_bar(raw)
        if bar is None:
            continue
        start = et_bucket_start(bar["ts"], minutes)
        current = buckets.get(start)
        if current is None:
            buckets[start] = {
                "ts": start,
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
                "count": 1,
            }
            continue
        current["high"] = max(current["high"], bar["high"])
        current["low"] = min(current["low"], bar["low"])
        current["close"] = bar["close"]
        current["count"] += 1
    return [buckets[key] for key in sorted(buckets)]


def _exact(bars: Iterable[dict], target: datetime) -> Optional[dict]:
    return next((bar for bar in bars if bar["ts"] == target), None)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET)


def _prior_reference_day(day: date, instrument: str) -> date:
    if day.weekday() == 0:
        return day - timedelta(days=3 if instrument == "QQQ" else 1)
    return day - timedelta(days=1)


def _terminal(day: date, reason: str, *, status: str = "INVALIDATED") -> dict:
    return {
        "trading_date": day.isoformat(),
        "status": status,
        "invalidation": reason,
    }


def _classify(four_am: dict, reference: dict) -> Optional[str]:
    high_broke = four_am["high"] > reference["high"]
    low_broke = four_am["low"] < reference["low"]
    if not high_broke and low_broke:
        return "LONG"
    if high_broke and not low_broke:
        return "SHORT"
    return None


def _completed_one_hour_stop(
    bars_1h: list[dict], entry_time: datetime, direction: str
) -> tuple[Optional[float], Optional[datetime]]:
    current_hour = et_bucket_start(entry_time, 60)
    required_start = current_hour - timedelta(hours=1)
    stop_bar = _exact(bars_1h, required_start)
    if stop_bar is None:
        return None, None
    return (
        stop_bar["low"] if direction == "LONG" else stop_bar["high"],
        required_start,
    )


def advance_4hr_retrigger(
    *,
    bars_5m: Iterable[dict],
    current_bar_ts: datetime,
    instrument: str,
    persisted_state: Optional[dict] = None,
) -> tuple[dict, Optional[dict]]:
    """Advance one completed 5-minute evaluation.

    ``current_bar_ts`` is the current bar's open timestamp.  Entry time is its
    close timestamp, matching the executable bar-arrival clock.
    """
    instrument = _root(instrument)
    current_open = current_bar_ts.astimezone(ET)
    day = current_open.date()
    if instrument not in _SUPPORTED:
        return _terminal(day, "UNSUPPORTED_INSTRUMENT"), None

    previous = dict(persisted_state or {})
    if previous.get("trading_date") != day.isoformat():
        previous = {}

    available: list[dict] = []
    current_close = current_open + timedelta(minutes=_BAR_MINUTES)
    for raw in bars_5m:
        bar = _as_bar(raw)
        if bar is not None and bar["ts"] + timedelta(minutes=_BAR_MINUTES) <= current_close:
            available.append(bar)
    available.sort(key=lambda bar: bar["ts"])

    status = previous.get("status")
    if status in {"TRIGGERED", "INVALIDATED", "EXPIRED"}:
        return previous, None

    open_930 = _at(day, 9, 30)
    expiry = _at(day, 11, 0)

    if status != "ARMED":
        if current_open < open_930:
            return {
                "trading_date": day.isoformat(),
                "status": "FORMING",
                "invalidation": None,
            }, None
        if current_open > open_930:
            return _terminal(day, "SETUP_NOT_ESTABLISHED_BY_0930"), None

        bars_4h = aggregate_et_bars(available, 240)
        reference_day = _prior_reference_day(day, instrument)
        reference_ts = _at(reference_day, 16, 0)
        reference = _exact(bars_4h, reference_ts)
        four_am_ts = _at(day, 4, 0)
        four_am = _exact(bars_4h, four_am_ts)
        if reference is None or four_am is None:
            return _terminal(day, "REFERENCE_DATA_MISSING"), None

        direction = _classify(four_am, reference)
        if direction is None:
            return _terminal(day, "FOUR_AM_NOT_QUALIFIED"), None
        trigger = four_am["high"] if direction == "LONG" else four_am["low"]
        target = reference["high"] if direction == "LONG" else reference["low"]

        broken = False
        retrace_bar = None
        for bar in available:
            if not (_at(day, 8, 0) <= bar["ts"] < open_930):
                continue
            if not broken:
                broken = (
                    bar["high"] > trigger
                    if direction == "LONG"
                    else bar["low"] < trigger
                )
            # A bar may break intrabar and close back through on that same bar.
            if broken and (
                (direction == "LONG" and bar["close"] < trigger)
                or (direction == "SHORT" and bar["close"] > trigger)
            ):
                retrace_bar = bar
                break
        if retrace_bar is None:
            return _terminal(day, "BREAK_RETRIGGER_NOT_CONFIRMED"), None

        nine_thirty = _exact(available, open_930)
        if nine_thirty is None:
            return _terminal(day, "NINE_THIRTY_BAR_MISSING"), None
        price_ok = (
            nine_thirty["open"] < trigger
            if direction == "LONG"
            else nine_thirty["open"] > trigger
        )
        if not price_ok:
            return _terminal(day, "PRICE_THROUGH_TRIGGER_AT_OPEN"), None

        previous = {
            "trading_date": day.isoformat(),
            "status": "ARMED",
            "direction": direction,
            "trigger": trigger,
            "target": target,
            "reference_bar_ts": reference_ts.isoformat(),
            "reference_high": reference["high"],
            "reference_low": reference["low"],
            "four_am_bar_ts": four_am_ts.isoformat(),
            "four_am_high": four_am["high"],
            "four_am_low": four_am["low"],
            "setup_bar_ts": retrace_bar["ts"].isoformat(),
            "expires_at": expiry.isoformat(),
            "invalidation": None,
        }

    if current_open >= expiry:
        expired = dict(previous)
        expired.update(status="EXPIRED", invalidation="ENTRY_WINDOW_EXPIRED")
        return expired, None

    current = _exact(available, current_open)
    if current is None:
        return previous, None
    direction = str(previous["direction"])
    trigger = float(previous["trigger"])
    triggered = (
        current["high"] >= trigger
        if direction == "LONG"
        else current["low"] <= trigger
    )
    if not triggered:
        return previous, None

    bars_1h = aggregate_et_bars(available, 60)
    stop, stop_bar_ts = _completed_one_hour_stop(
        bars_1h, current_close, direction
    )
    if stop is None or stop_bar_ts is None:
        invalid = dict(previous)
        invalid.update(status="INVALIDATED", invalidation="COMPLETED_1H_STOP_MISSING")
        return invalid, None
    target = float(previous["target"])
    bracket_valid = (
        stop < trigger < target
        if direction == "LONG"
        else target < trigger < stop
    )
    if not bracket_valid:
        invalid = dict(previous)
        invalid.update(status="INVALIDATED", invalidation="INVALID_ENTRY_BRACKET")
        return invalid, None

    triggered_state = dict(previous)
    triggered_state.update(
        status="TRIGGERED",
        entry_time=current_close.isoformat(),
        stop=stop,
        stop_bar_ts=stop_bar_ts.isoformat(),
    )
    candidate = {
        "direction": direction,
        "entry": trigger,
        "stop": stop,
        "target": target,
        "entry_time": current_close,
        "stop_bar_ts": stop_bar_ts,
        "state": triggered_state,
    }
    return triggered_state, candidate
