"""Human-readable rendering for namespaced Signa v2 observation telemetry."""

from __future__ import annotations

from typing import Any


def render_signa_v2(raw: dict[str, Any]) -> str:
    """Render v2 context without implying authority or trade permission."""
    if not isinstance(raw, dict):
        return "N/A"

    error = raw.get("signa_v2_error")
    if error:
        return f"V2 observational · unavailable ({error})"
    if raw.get("signa_v2_ok") is not True:
        return "N/A"

    parts = ["V2 observational"]
    symbol = raw.get("signa_v2_symbol")
    timeframe = raw.get("signa_v2_timeframe")
    grade = raw.get("signa_v2_grade")
    score = raw.get("signa_v2_score")
    confidence = raw.get("signa_v2_confidence")
    direction = raw.get("signa_v2_direction")
    reward_to_risk = raw.get("signa_v2_reward_to_risk")

    if symbol:
        parts.append(str(symbol))
    if grade:
        parts.append(f"grade {grade}")
    if score is not None:
        parts.append(f"score {_number(score, 0)}")
    if confidence is not None:
        parts.append(f"confidence {_number(confidence, 0)}%")
    if direction:
        parts.append(str(direction))
    if reward_to_risk is not None:
        parts.append(f"R:R {_number(reward_to_risk, 2)}")
    if timeframe:
        parts.append(str(timeframe))
    return " · ".join(parts)


def _number(value: Any, decimals: int) -> str:
    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return str(value)
