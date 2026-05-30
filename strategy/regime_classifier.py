"""Deterministic market regime classifier for signal gating."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from context.market_context import MarketState
from risk.risk_engine import DailyState

RegimeName = Literal["FULL_LONG", "FULL_SHORT", "RESTRICTED", "NO_TRADE"]


@dataclass(frozen=True)
class RegimeResult:
    regime: RegimeName
    failed_gate: str | None = None
    reasons: list[str] = field(default_factory=list)
    warning_only: bool = False


def classify_regime(state: MarketState, daily_state: DailyState | None = None) -> RegimeResult:
    if daily_state is not None:
        if daily_state.trade_count <= -1:  # defensive no-op; limits are enforced preflight
            return RegimeResult("NO_TRADE", "INVALID_DAILY_STATE", ["invalid daily state"])

    strat = state.strat
    current = _norm(strat.current_bar_type if strat else None)
    htf = _norm(state.icc.htf_phase if state.icc else None)
    phase = _norm(state.icc.phase if state.icc else None)
    zone = _norm(state.icc.indication_type if state.icc else None)
    weekly = _signa_dir(state.signa.weekly_direction if state.signa else None)
    daily = _signa_dir(state.signa.daily_direction if state.signa else None)
    trend = _norm(state.trend.direction if state.trend else None)
    vwap = _norm(state.vwap.price_vs_vwap)
    orb = _norm(state.orb.status)
    market = _norm(state.market_condition)

    if current == "1" and phase in {"4h_1", "inside_4h"} and htf in {"1h_1", "inside_1h"}:
        return RegimeResult("NO_TRADE", "REGIME_ALL_INSIDE", ["daily + 4H + 1H inside"])
    if zone == "opposing_supply":
        return RegimeResult("NO_TRADE", "REGIME_INTO_SUPPLY", ["entry into opposing supply"])
    if zone == "opposing_demand":
        return RegimeResult("NO_TRADE", "REGIME_INTO_DEMAND", ["entry into opposing demand"])
    if phase == "news_window":
        return RegimeResult("NO_TRADE", "REGIME_NEWS_WINDOW", ["news window"])
    if phase == "no_clean_target":
        return RegimeResult("NO_TRADE", "REGIME_NO_CLEAN_TARGET", ["no clean target"])

    if phase in {"chase_70_extension", "extended_70", "already_ran"}:
        return RegimeResult(
            "RESTRICTED",
            "REGIME_CHASE_EXTENSION",
            ["chase greater than 70% extension"],
            warning_only=True,
        )

    restricted_reasons = []
    restricted_gate = "REGIME_RESTRICTED"
    if current == "1":
        restricted_reasons.append("daily inside bar")
    if current == "3" and weekly not in {"bullish", "bearish", "neutral", ""}:
        restricted_reasons.append("daily 3 with conflicting HTF")
    if orb == "inside":
        restricted_reasons.append("price inside ORB")
    if vwap in {"at", "chop", "inside"} or market == "choppy":
        restricted_reasons.append("VWAP/market chop")
    if trend in {"", "sideways", "unclear", "mixed"}:
        restricted_reasons.append("HTF unclear")

    long_ok = (
        (current in {"2u", "two_up"} or (current == "3" and phase in {"reclaim", "failed_low_reclaim"}))
        and weekly in {"bullish", "neutral", ""}
        and trend in {"up", "bullish"}
        and vwap in {"above", "reclaiming", "reclaimed"}
        and orb in {"above", "reclaimed_high", "reclaiming"}
        and zone not in {"supply", "opposing_supply"}
    )
    if long_ok:
        return RegimeResult("FULL_LONG", reasons=["long regime aligned"])

    short_ok = (
        (current in {"2d", "two_down"} or (current == "3" and phase in {"rejection", "failed_high_rejection"}))
        and weekly in {"bearish", "neutral", ""}
        and trend in {"down", "bearish"}
        and vwap in {"below", "rejecting", "rejected"}
        and orb in {"below", "rejected_high", "rejected_low", "rejecting"}
        and zone not in {"demand", "opposing_demand"}
    )
    if short_ok:
        return RegimeResult("FULL_SHORT", reasons=["short regime aligned"])

    if restricted_reasons:
        return RegimeResult("RESTRICTED", restricted_gate, restricted_reasons, warning_only=True)

    return RegimeResult("RESTRICTED", "REGIME_NOT_FULL", ["not enough full-regime alignment"], True)


def _signa_dir(value: str | None) -> str:
    value = _norm(value)
    if value in {"up", "long", "bull", "bullish"}:
        return "bullish"
    if value in {"down", "short", "bear", "bearish"}:
        return "bearish"
    if value in {"neutral", "mixed", "flat", ""}:
        return "neutral" if value else ""
    return value


def _norm(value: object) -> str:
    return str(value or "").strip().lower()
