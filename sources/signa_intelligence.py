"""Pure parsers for current Signa intelligence payloads.

AUDIT/OBSERVATION ONLY. This module performs no network I/O and has no strategy,
risk, broker, order, or execution authority. It exists so capability-probe
responses can be normalized and compared without wiring any new data into trade
logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SignaActionCardObservation:
    symbol: str | None = None
    timeframe: str | None = None
    direction: str | None = None
    confidence: float | None = None
    grade: str | None = None
    score: float | None = None
    entry_low: float | None = None
    entry_high: float | None = None
    stop_loss: float | None = None
    targets: tuple[float, ...] = ()
    reward_to_risk: float | None = None
    model_version: str | None = None
    component_scores: dict[str, float | None] = field(default_factory=dict)
    data_as_of: str | None = None
    server_time: str | None = None


@dataclass(frozen=True)
class SignaAnalysisObservation:
    symbol: str | None = None
    summary: str | None = None
    patterns: tuple[str, ...] = ()
    sentiment: str | None = None
    catalysts: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    technicals: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SignaEarningsObservation:
    symbol: str | None = None
    period: str | None = None
    report_date: str | None = None
    signal: str | None = None
    signal_score: float | None = None
    eps_surprise_pct: float | None = None
    revenue_surprise_pct: float | None = None
    bullish_points: tuple[str, ...] = ()
    bearish_points: tuple[str, ...] = ()
    minutes_since_report: float | None = None
    fetched_at: str | None = None
    cached: bool | None = None


@dataclass(frozen=True)
class SignaDatasetObservation:
    dataset: str
    symbol: str | None = None
    count: int | None = None
    direction: str | None = None
    sentiment: str | None = None
    top_level_fields: tuple[str, ...] = ()


def parse_action_card(payload: dict[str, Any]) -> SignaActionCardObservation:
    data = _dict(payload.get("data"))
    signal = _dict(data.get("signal"))
    entry = _dict(signal.get("entry_zone"))
    components_raw = _dict(signal.get("component_scores"))
    components = {str(k): _float(v) for k, v in components_raw.items()}
    targets = tuple(v for v in (_float(x) for x in _list(signal.get("targets"))) if v is not None)
    return SignaActionCardObservation(
        symbol=_text(signal.get("symbol")),
        timeframe=_text(signal.get("timeframe")),
        direction=_text(signal.get("direction")),
        confidence=_float(signal.get("confidence")),
        grade=_text(signal.get("grade")),
        score=_float(signal.get("score")),
        entry_low=_float(entry.get("low")),
        entry_high=_float(entry.get("high")),
        stop_loss=_float(signal.get("stop_loss")),
        targets=targets,
        reward_to_risk=_float(signal.get("reward_to_risk")),
        model_version=_text(signal.get("model_version")),
        component_scores=components,
        data_as_of=_text(payload.get("data_as_of")),
        server_time=_text(payload.get("server_time")),
    )


def parse_analysis(payload: dict[str, Any]) -> SignaAnalysisObservation:
    analysis = _dict(payload.get("analysis"))
    return SignaAnalysisObservation(
        symbol=_text(payload.get("symbol")),
        summary=_text(analysis.get("summary")),
        patterns=_text_tuple(analysis.get("patterns")),
        sentiment=_text(analysis.get("sentiment")),
        catalysts=_text_tuple(analysis.get("catalysts")),
        risks=_text_tuple(analysis.get("risks")),
        technicals=_dict(analysis.get("technicals")),
    )


def parse_earnings(payload: dict[str, Any]) -> SignaEarningsObservation:
    eps = _dict(payload.get("eps"))
    revenue = _dict(payload.get("revenue"))
    return SignaEarningsObservation(
        symbol=_text(payload.get("symbol")),
        period=_text(payload.get("period")),
        report_date=_text(payload.get("reportDate")),
        signal=_text(payload.get("signal")),
        signal_score=_float(payload.get("signalScore")),
        eps_surprise_pct=_float(eps.get("surprisePct")),
        revenue_surprise_pct=_float(revenue.get("surprisePct")),
        bullish_points=_text_tuple(payload.get("bullishPoints")),
        bearish_points=_text_tuple(payload.get("bearishPoints")),
        minutes_since_report=_float(payload.get("minutesSinceReport")),
        fetched_at=_text(payload.get("fetchedAt")),
        cached=_bool(payload.get("cached")),
    )


def summarize_dataset(dataset: str, payload: dict[str, Any], *, symbol: str | None = None) -> SignaDatasetObservation:
    """Produce conservative metadata for variable/undocumented intelligence feeds.

    Options-flow, dark-pool, tide, and congressional payload schemas have changed
    over time. Until a Founding-key probe proves their live shapes, do not invent a
    strict parser. Record only fields that can be identified generically.
    """
    count = _int(payload.get("count"))
    if count is None:
        for key in ("results", "data", "trades", "prints", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                count = len(value)
                break
    direction = _first_text(payload, "direction", "bias", "flow_direction", "signal")
    sentiment = _first_text(payload, "sentiment", "net_sentiment", "tide")
    return SignaDatasetObservation(
        dataset=str(dataset),
        symbol=(symbol or _text(payload.get("symbol")) or _text(payload.get("ticker"))),
        count=count,
        direction=direction,
        sentiment=sentiment,
        top_level_fields=tuple(sorted(str(k) for k in payload.keys())),
    )


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _text_tuple(value: Any) -> tuple[str, ...]:
    return tuple(text for item in _list(value) if (text := _text(item)) is not None)


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _first_text(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _text(payload.get(key))
        if value is not None:
            return value
    return None
