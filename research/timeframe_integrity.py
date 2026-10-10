"""Offline time-frame integrity for NEW futures research only.

Exclude partial, gapped and contract-mixed 5/15/30/60/240/720-minute bars.
Do not alter the sealed MGC forward scorer or any runtime/order path.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from math import isfinite
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
_PERIODS = frozenset({5, 15, 30, 60, 240, 720})
_SOURCE_MINUTES = frozenset({5, 15})


@dataclass(frozen=True)
class ConfirmedBars:
    bars: tuple[dict, ...]
    rejected: dict[str, int]
    checked_buckets: int


def _bucket_key(ts: int, timeframe_minutes: int) -> tuple[int, int] | None:
    """Return (CME bucket start, session start) using 18:00 New York roll."""
    local = datetime.fromtimestamp(ts, timezone.utc).astimezone(_ET)
    if time(17) <= local.time() < time(18):
        return None
    session_date = local.date() if local.time() >= time(18) else local.date() - timedelta(days=1)
    start = int(datetime.combine(session_date, time(18), tzinfo=_ET).timestamp())
    if ts < start:
        raise ValueError("source timestamp precedes its session")
    width = timeframe_minutes * 60
    return (start + ((ts - start) // width) * width, start)


def confirmed_session_bars(raw15: Sequence[Mapping], *, minutes: int, source_minutes: int = 15) -> ConfirmedBars:
    """Only produce closed, contiguous, single-contract bars.

    Each source 5m/15m bar must supply UTC timestamp-open SECONDS as ts,
    a dated ticker, and OHLCV. Fail closed on missing provenance or invalid
    source chronology/geometry. Partial HTF buckets never become signals.
    """
    if type(minutes) is not int or minutes not in _PERIODS:
        raise ValueError("timeframe must be one of 5, 15, 30, 60, 240, 720 minutes")
    if type(source_minutes) is not int or source_minutes not in _SOURCE_MINUTES or minutes % source_minutes:
        raise ValueError("source_minutes must divide timeframe and be 5 or 15")
    if not raw15:
        return ConfirmedBars((), {}, 0)
    source_seconds = source_minutes * 60
    groups: dict[tuple[int, int], list[dict]] = defaultdict(list)
    rejected: Counter[str] = Counter()
    previous = None
    for row in raw15:
        if not isinstance(row.get("ts"), int) or isinstance(row["ts"], bool):
            raise ValueError("source ts must be UTC epoch seconds (integer)")
        ts = row["ts"]
        if previous is not None and ts <= previous:
            raise ValueError("source timestamps must be unique and increasing")
        previous = ts
        if ts % source_seconds:
            raise ValueError("source timestamp is not on the declared source time boundary")
        ticker = str(row.get("ticker") or "").strip()
        if not ticker:
            raise ValueError("dated source ticker is required for roll safety")
        try:
            o, h, l, c, v = (float(row[k]) for k in ("open", "high", "low", "close", "volume"))
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("source bar has invalid/missing OHLCV") from exc
        if not all(isfinite(x) for x in (o, h, l, c, v)) or l <= 0 or v < 0 or l > min(o, c) or h < max(o, c) or h < l:
            raise ValueError("source bar has nonfinite/contradictory OHLCV")
        bucket = _bucket_key(ts, minutes)
        if bucket is None:
            rejected["maintenance_bar"] += 1
            continue
        groups[bucket].append({
            "ts": ts, "ticker": ticker, "open": o, "high": h,
            "low": l, "close": c, "volume": v,
        })
    out: list[dict] = []
    width_bars = minutes // source_minutes
    for (bucket_start, session_start), items in sorted(groups.items()):
        expected = [bucket_start + source_seconds * n for n in range(width_bars)]
        if [b["ts"] for b in items] != expected:
            rejected["partial_or_gap"] += 1
            continue
        if len({b["ticker"] for b in items}) != 1:
            rejected["contract_roll"] += 1
            continue
        out.append({
            "ts": bucket_start,
            "close_ts": bucket_start + minutes * 60,
            "session_start_ts": session_start,
            "timeframe_minutes": minutes,
            "ticker": items[0]["ticker"],
            "open": items[0]["open"],
            "high": max(b["high"] for b in items),
            "low": min(b["low"] for b in items),
            "close": items[-1]["close"],
            "volume": sum(b["volume"] for b in items),
        })
    return ConfirmedBars(tuple(out), dict(rejected), len(groups))
