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

    return SignaActionCardObservation(
        ok=bool(payload.get("success", True)),
        symbol=_upper_or_none(signal.get("symbol")),
        timeframe=_str_or_none(signal.get("timeframe")),
        direction=_upper_or_none(signal.get("direction")),
        score=_float_or_none(signal.get("score")),
        grade=_upper_or_none(signal.get("grade")),
        confidence=_float_or_none(signal.get("confidence")),
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
