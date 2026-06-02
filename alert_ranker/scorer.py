"""Deterministic setup scoring for advisory options alerts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .options_valuation import evaluate_option_value


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


def _signa_component(data: dict[str, Any], direction: str) -> int:
    grade = str(data.get("signa_grade") or "").strip().upper()
    signa_direction = str(data.get("signa_daily_direction") or data.get("signa_direction") or "").strip().upper()
    if not grade and not signa_direction:
        return 0
    if grade[:1] in {"C", "D", "F"}:
        return -3
    opposes = (direction == "LONG" and signa_direction == "DOWN") or (direction == "SHORT" and signa_direction == "UP")
    if opposes:
        return -3
    aligns = (direction == "LONG" and signa_direction == "UP") or (direction == "SHORT" and signa_direction == "DOWN")
    if grade[:1] in {"A", "B"} and aligns:
        return 2
    if grade[:1] in {"A", "B"}:
        return 1
    return 0


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
    valuation = evaluate_option_value(data)

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

    # Build full component breakdown before applying hard gates so callers
    # can always see what would have scored (useful for debugging and logging).
    components = {
        "strat_pattern": 3 if pattern and pattern.upper() != "N/A" else 0,
        "vwap": 2 if vwap_pass else 0,
        "trend": 2 if trend_pass else 0,
        "volume": 2 if volume_ratio is not None and volume_ratio > 1.2 else 0,
        "iv_rank": 0,
        "premium_value": 0,
        "signa": _signa_component(data, direction),
        "session": 1 if is_ny_open(now) else 0,
    }
    if iv_rank is not None:
        if iv_rank < 30:
            components["iv_rank"] = 2
        elif iv_rank > 50:
            components["iv_rank"] = -3

    enriched = dict(data)
    if valuation is not None:
        components["premium_value"] = valuation.component_score
        enriched["option_theoretical_value"] = valuation.theoretical_value
        enriched["option_edge_percent"] = valuation.edge_percent
        enriched["option_value_verdict"] = valuation.verdict
        enriched["option_value_reason"] = valuation.reason

    # VWAP and trend are hard structural gates — a setup trading against either
    # is not actionable regardless of other factors. Score is forced to 0 but
    # the full component dict is preserved so callers can see what failed and why.
    if not vwap_pass:
        return ScoreResult(ticker, direction, 0, pattern, components, enriched, "against_vwap")
    if not trend_pass:
        return ScoreResult(ticker, direction, 0, pattern, components, enriched, "against_trend")

    score = max(0, min(10, sum(components.values())))
    return ScoreResult(ticker, direction, score, pattern, components, enriched, "")
