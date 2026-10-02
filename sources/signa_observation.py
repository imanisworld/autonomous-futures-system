"""Pure parser for the current documented Signa Action Card response.

This module is intentionally observation-only. It has no network client, no
strategy/risk imports, no broker/execution surface, and no authority over any
trade decision. Its only job is to normalize the richer current Signa signal
contract into durable telemetry fields that can be compared with later outcomes.

It is not wired into the runtime by this change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class SignaActionCardObservation:
    ok: bool
    symbol: str | None = None
    timeframe: str | None = None
    direction: str | None = None
    score: float | None = None
    grade: str | None = None
    confidence: float | None = None
    strength: float | None = None
    factor_count: int = 0
    factor_conflicts: tuple[str, ...] = field(default_factory=tuple)
    observation_rating: str = "N/A"
    observation_rating_basis: str | None = None
    entry_low: float | None = None
    entry_high: float | None = None
    stop_loss: float | None = None
    targets: tuple[float, ...] = field(default_factory=tuple)
    reward_to_risk: float | None = None
    component_scores: dict[str, float | None] = field(default_factory=dict)
    model_version: str | None = None
    request_id: str | None = None
    server_time: str | None = None
    data_as_of: str | None = None
    retrieved_at: str | None = None
    cached: bool = False
    error: str | None = None
    raw: dict[str, Any] | None = None

    def telemetry_fields(self) -> dict[str, Any]:
        """Return namespaced telemetry only; never legacy trade-authority fields."""
        return {
            "signa_v2_ok": self.ok,
            "signa_v2_symbol": self.symbol,
            "signa_v2_timeframe": self.timeframe,
            "signa_v2_direction": self.direction,
            "signa_v2_score": self.score,
            "signa_v2_grade": self.grade,
            "signa_v2_confidence": self.confidence,
            "signa_v2_strength": self.strength,
            "signa_v2_factor_count": self.factor_count,
            "signa_v2_factor_conflicts": list(self.factor_conflicts),
            "signa_v2_observation_rating": self.observation_rating,
            "signa_v2_observation_rating_basis": self.observation_rating_basis,
            "signa_v2_entry_low": self.entry_low,
            "signa_v2_entry_high": self.entry_high,
            "signa_v2_stop_loss": self.stop_loss,
            "signa_v2_targets": list(self.targets),
            "signa_v2_reward_to_risk": self.reward_to_risk,
            "signa_v2_component_scores": dict(self.component_scores),
            "signa_v2_model_version": self.model_version,
            "signa_v2_request_id": self.request_id,
            "signa_v2_server_time": self.server_time,
            "signa_v2_data_as_of": self.data_as_of,
            "signa_v2_retrieved_at": self.retrieved_at,
            "signa_v2_cached": self.cached,
            "signa_v2_error": self.error,
        }


def parse_action_card(
    payload: dict[str, Any] | None,
    *,
    retrieved_at: str | None = None,
) -> SignaActionCardObservation:
    """Parse the current documented ``/api/v1/signals/{symbol}`` response.

    Missing or malformed values remain ``None``. The parser never manufactures
    alignment, freshness, grade, confidence, targets, or component values.
    A structurally missing ``data.signal`` block returns ``ok=False`` rather
    than falling back to the legacy contract.
    """
    retrieved_at = retrieved_at or datetime.now(timezone.utc).isoformat()
    if not isinstance(payload, dict):
        return SignaActionCardObservation(
            ok=False,
            retrieved_at=retrieved_at,
            error="payload_not_object",
            raw=payload if isinstance(payload, dict) else None,
        )

    data = _dict(payload.get("data"))
    signal = _dict(data.get("signal"))
    if not signal:
        return SignaActionCardObservation(
            ok=False,
            request_id=_str_or_none(payload.get("request_id")),
            server_time=_str_or_none(payload.get("server_time")),
            data_as_of=_str_or_none(payload.get("data_as_of")),
            retrieved_at=retrieved_at,
            error="signal_missing",
            raw=payload,
        )

    entry_zone = _dict(signal.get("entry_zone"))
    raw_components = _dict(signal.get("component_scores"))
    components = {
        str(name): _float_or_none(value)
        for name, value in sorted(raw_components.items())
    }
    targets = tuple(
        value
        for value in (_float_or_none(item) for item in _list(signal.get("targets")))
        if value is not None
    )
    direction = _upper_or_none(signal.get("direction"))
    score = _float_or_none(signal.get("score"))
    strength = _float_or_none(signal.get("strength"))
    if strength is None and score is not None:
        # Signa defines strength as distance from neutral (50), scaled to 0-100.
        strength = min(100.0, abs(score - 50.0) * 2.0)
    factor_count = sum(value is not None for value in components.values())
    factor_conflicts = _factor_conflicts(direction, components)
    confidence = _float_or_none(signal.get("confidence"))
    observation_ok = bool(payload.get("success", True))
    observation_rating, observation_rating_basis = _observation_rating(
        ok=observation_ok,
        direction=direction,
        score=score,
        confidence=confidence,
        strength=strength,
        factor_count=factor_count,
        factor_conflicts=factor_conflicts,
    )

    return SignaActionCardObservation(
        ok=observation_ok,
        symbol=_upper_or_none(signal.get("symbol")),
        timeframe=_str_or_none(signal.get("timeframe")),
        direction=direction,
        score=score,
        grade=_upper_or_none(signal.get("grade")),
        confidence=confidence,
        strength=strength,
        factor_count=factor_count,
        factor_conflicts=factor_conflicts,
        observation_rating=observation_rating,
        observation_rating_basis=observation_rating_basis,
        entry_low=_float_or_none(entry_zone.get("low")),
        entry_high=_float_or_none(entry_zone.get("high")),
        stop_loss=_float_or_none(signal.get("stop_loss")),
        targets=targets,
        reward_to_risk=_float_or_none(signal.get("reward_to_risk")),
        component_scores=components,
        model_version=_str_or_none(signal.get("model_version")),
        request_id=_str_or_none(payload.get("request_id")),
        server_time=_str_or_none(payload.get("server_time")),
        data_as_of=_str_or_none(payload.get("data_as_of")),
        retrieved_at=retrieved_at,
        raw=payload,
    )


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _upper_or_none(value: Any) -> str | None:
    text = _str_or_none(value)
    return text.upper() if text else None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None

    

_BULLISH = frozenset({"LONG", "BUY", "BULL", "BULLISH", "CALL", "UP"})
_BEARISH = frozenset({"SHORT", "SELL", "BEAR", "BEARISH", "PUT", "DOWN"})


def _observation_rating(
    *,
    ok: bool,
    direction: str | None,
    score: float | None,
    confidence: float | None,
    strength: float | None,
    factor_count: int,
    factor_conflicts: tuple[str, ...],
) -> tuple[str, str]:
    """Rate evidence quality only; never trade quality or permission.

    A = core fields present + factor coverage + no observed factor conflicts.
    B = core fields present + factor coverage + one or more observed conflicts.
    C = payload parsed but core evidence or factor coverage is incomplete,
        or the direction is NEUTRAL/unrecognized (agreement not evaluable).
    N/A = observation unavailable/failed.
    """
    if not ok:
        return "N/A", "unavailable"

    missing: list[str] = []
    if direction is None:
        missing.append("direction")
    elif direction.upper() not in _BULLISH | _BEARISH:
        # NEUTRAL/unknown: factor agreement cannot be evaluated, so never A.
        missing.append("direction_unrecognized")
    if score is None:
        missing.append("score")
    if confidence is None:
        missing.append("confidence")
    if strength is None:
        missing.append("strength")
    if factor_count <= 0:
        missing.append("factor_coverage")
    if missing:
        return "C", "partial:" + ",".join(missing)
    if factor_conflicts:
        return "B", f"complete_core;factor_conflicts={len(factor_conflicts)}"
    return "A", "complete_core;factor_coverage;no_conflicts"


def _factor_conflicts(
    direction: str | None,
    components: dict[str, float | None],
) -> tuple[str, ...]:
    """Return factor names that lean against the published direction.

    A score of exactly 50 is neutral, not a conflict. This is observation-only
    metadata; it does not reject or approve a trade.
    """
    normalized = (direction or "").upper()
    bullish = normalized in _BULLISH
    bearish = normalized in _BEARISH
    if not bullish and not bearish:
        return ()
    conflicts: list[str] = []
    for name, value in sorted(components.items()):
        if value is None or value == 50:
            continue
        if bullish and value < 50:
            conflicts.append(str(name))
        elif bearish and value > 50:
            conflicts.append(str(name))
    return tuple(conflicts)
