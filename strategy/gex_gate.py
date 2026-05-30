"""GEX/gamma deterministic gate."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from context.market_context import MarketState

GEXStatus = Literal[
    "GREEN_LIGHT_LONG", "GREEN_LIGHT_SHORT", "RED_LIGHT", "BREAKOUT_MODE", "NEUTRAL"
]


@dataclass(frozen=True)
class GEXGateResult:
    status: GEXStatus
    failed_gate: str | None = None
    reasons: list[str] = field(default_factory=list)
    warning_only: bool = False


def evaluate_gex(state: MarketState, direction: str | None) -> GEXGateResult:
    gex = state.gex
    if gex is None or not any((gex.gex_flip, gex.call_wall, gex.put_wall, gex.mid_upper, gex.mid_lower)):
        return GEXGateResult("NEUTRAL", reasons=["GEX data absent"])
    if direction not in {"LONG", "SHORT"}:
        return GEXGateResult("NEUTRAL", reasons=["direction absent"])

    price = state.price.last
    zone = _norm(state.icc.indication_type if state.icc else None)
    orb = _norm(state.orb.status)
    vwap = _norm(state.vwap.price_vs_vwap)
    room = _room_to_next_level(price, gex.call_wall if direction == "LONG" else gex.put_wall)

    if direction == "LONG":
        if zone == "supply":
            return GEXGateResult("RED_LIGHT", "GEX_ZONE_CONFLICT", ["long into supply"])
        if gex.call_wall is not None and price >= gex.call_wall:
            return GEXGateResult("RED_LIGHT", "GEX_UNDER_CALL_WALL", ["buying into/above call wall"])
        if gex.call_wall is not None and room is not None and room < _min_room(state):
            return GEXGateResult("RED_LIGHT", "GEX_NO_ROOM", ["target room too small"])
        if _near(price, gex.gex_flip) and (orb in {"above", "reclaimed_high"} or vwap == "above") and zone in {"", "demand"}:
            return GEXGateResult("GREEN_LIGHT_LONG", reasons=["GEX support/flip reclaim"])
        if gex.vol_trigger_up is not None and price > gex.vol_trigger_up:
            return GEXGateResult("BREAKOUT_MODE", reasons=["clean upside break requires acceptance/retest"])

    if direction == "SHORT":
        if zone == "demand":
            return GEXGateResult("RED_LIGHT", "GEX_ZONE_CONFLICT", ["short into demand"])
        if gex.put_wall is not None and price <= gex.put_wall:
            return GEXGateResult("RED_LIGHT", "GEX_ABOVE_PUT_WALL", ["selling into/below put wall"])
        if gex.put_wall is not None and room is not None and room < _min_room(state):
            return GEXGateResult("RED_LIGHT", "GEX_NO_ROOM", ["target room too small"])
        if _near(price, gex.gex_flip) and (orb in {"below", "rejected_high", "rejected_low"} or vwap == "below") and zone in {"", "supply"}:
            return GEXGateResult("GREEN_LIGHT_SHORT", reasons=["GEX resistance rejection"])
        if gex.vol_trigger_down is not None and price < gex.vol_trigger_down:
            return GEXGateResult("BREAKOUT_MODE", reasons=["clean downside break requires acceptance/retest"])

    if gex.mid_lower is not None and gex.mid_upper is not None and gex.mid_lower < price < gex.mid_upper:
        return GEXGateResult("RED_LIGHT", "GEX_MID_RANGE", ["mid-range GEX"])
    return GEXGateResult("NEUTRAL", reasons=["GEX present but not decisive"])


def _room_to_next_level(price: float, level: float | None) -> float | None:
    if level is None:
        return None
    return abs(level - price)


def _min_room(state: MarketState) -> float:
    return 4.0 if state.instrument in {"MNQ", "MES"} else 1.0


def _near(price: float, level: float | None) -> bool:
    if level is None:
        return False
    return abs(price - level) <= max(abs(price) * 0.001, 2.0)


def _norm(value: object) -> str:
    return str(value or "").strip().lower()
