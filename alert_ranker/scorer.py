"""Deterministic setup scoring for advisory options alerts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ScoreResult:
    ticker: str
    direction: str
    score: int
    pattern: str
    components: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def direction_from_data(data: dict[str, Any]) -> str:
    price = _num(data.get("price"))
    vwap = _num(data.get("vwap"))
    ema20 = _num(data.get("ema20"))
    if price is None or vwap is None or ema20 is None:
        return "UNKNOWN"
    if price > vwap and price > ema20:
        return "LONG"
    if price < vwap and price < ema20:
        return "SHORT"
    return "UNKNOWN"


def is_ny_open(now: datetime, tz_name: str = "America/New_York") -> bool:
    local = now.astimezone(ZoneInfo(tz_name))
    return local.weekday() < 5 and (
        (local.hour == 9 and local.minute >= 30)
        or local.hour == 10
        or (local.hour == 11 and local.minute < 30)
    )


def score_setup(data: dict[str, Any], now: datetime | None = None) -> ScoreResult:
    now = now or datetime.now(ZoneInfo("America/New_York"))
    ticker = str(data.get("ticker") or "").upper()
    pattern = str(data.get("pattern") or "N/A")
    direction = str(data.get("direction") or "").upper() or direction_from_data(data)

    price = _num(data.get("price"))
    vwap = _num(data.get("vwap"))
    ema20 = _num(data.get("ema20"))
    volume_ratio = _num(data.get("volume_ratio"))
    iv_rank = _num(data.get("iv_rank"))

    vwap_pass = bool(
        (direction == "LONG" and price is not None and vwap is not None and price > vwap)
        or (direction == "SHORT" and price is not None and vwap is not None and price < vwap)
    )
    trend_pass = bool(
        (direction == "LONG" and price is not None and ema20 is not None and price > ema20)
        or (direction == "SHORT" and price is not None and ema20 is not None and price < ema20)
    )

    if direction not in {"LONG", "SHORT"}:
        return ScoreResult(ticker, "UNKNOWN", 0, pattern, {}, dict(data), "direction_unknown")
    if not vwap_pass:
        return ScoreResult(ticker, direction, 0, pattern, {"vwap": 0}, dict(data), "against_vwap")
    if not trend_pass:
        return ScoreResult(ticker, direction, 0, pattern, {"trend": 0}, dict(data), "against_trend")

    components = {
        "strat_pattern": 3 if pattern and pattern.upper() != "N/A" else 0,
        "vwap": 2,
        "trend": 2,
        "volume": 2 if volume_ratio is not None and volume_ratio > 1.2 else 0,
        "iv_rank": 0,
        "session": 1 if is_ny_open(now) else 0,
    }
    if iv_rank is not None:
        if iv_rank < 30:
            components["iv_rank"] = 2
        elif iv_rank > 50:
            components["iv_rank"] = -3

    score = max(0, min(10, sum(components.values())))
    return ScoreResult(ticker, direction, score, pattern, components, dict(data), "")
