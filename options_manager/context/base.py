"""options_manager/context/base.py

Advisory-only options market-context model — Increment 3. Shared,
fail-closed contract for the market-context validator
(market_validator.py). Every input here is caller-supplied; this module
never fetches SPY/QQQ quotes, GEX levels, or Signa output, never reads
config, never holds credentials, and performs no I/O of any kind.

This is a separate, additive model from options_manager/strategies/base.py's
placeholder StrategyMarketContext (Increment 1) — it is not wired into
strat_212.py. Nothing here imports options_manager's own risk_gate,
contract_quality, dry_run_review, human_confirm, order_ticket,
broker_boundary, mock_broker_preview, storage, http_api, or app modules,
the strategies or levels packages, alert_ranker, options_companion,
execution, webhook, risk/risk_engine.py, or the existing live
context.market_context loader (which pulls config and enriches data).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

MarketContextStatus = Literal["VALID", "CAUTION", "INVALID"]

Trend = Literal["bullish", "bearish", "neutral"]
GexRegime = Literal["positive", "negative", "neutral"]
SignaDirection = Literal["bullish", "bearish", "neutral"]
HtfAlignment = Literal["aligned", "mixed", "opposite"]
GapDirection = Literal["up", "down", "none"]
EventRisk = Literal["none", "low", "high"]


@dataclass(frozen=True)
class MarketContextInputs:
    """Explicit, caller-supplied market context only.

    ``direction``, ``ticker``, and ``underlying_price`` are always required.
    SPY/QQQ context, higher-timeframe alignment, and event risk remain
    fail-closed inputs for the market validator.

    The GEX block (``gex_regime``, ``price_above_gex_flip``, and the two
    ``distance_to_gamma_*`` fields) is optional enrichment. Absent GEX degrades
    to a ``GEX_UNAVAILABLE`` warning rather than INVALID; no regime or flip is
    invented.

    The Signa fields are also optional, but for a different reason: they are
    observational telemetry only. The effectiveness audit did not establish
    incremental value, so ``signa_direction``, ``signa_grade``, and
    ``signa_score`` must not authorize, veto, caution, confirm, or score a
    setup. They remain on this input model solely so callers can carry the
    observed vendor state alongside the independently evaluated context.
    """

    direction: Literal["CALL", "PUT"]
    ticker: Optional[str]
    underlying_price: Optional[float]
    spy_trend: Optional[Trend] = None
    qqq_trend: Optional[Trend] = None
    spy_above_flip: Optional[bool] = None
    qqq_above_flip: Optional[bool] = None
    # GEX is OPTIONAL enrichment. Both fields absent => the validator emits
    # GEX_UNAVAILABLE and drops gamma-wall targeting; it does NOT reject and it
    # does NOT substitute a neutral regime or a fake flip.
    gex_regime: Optional[GexRegime] = None
    price_above_gex_flip: Optional[bool] = None
    # Signa is OBSERVATIONAL ONLY. These fields may be absent or contradictory
    # without changing the market-context verdict or context score.
    signa_direction: Optional[SignaDirection] = None
    signa_grade: Optional[str] = None
    signa_score: Optional[float] = None
    higher_timeframe_alignment: Optional[HtfAlignment] = None
    gap_direction: Optional[GapDirection] = None
    distance_to_gamma_resistance: Optional[float] = None
    distance_to_gamma_support: Optional[float] = None
    event_risk: Optional[EventRisk] = None
    min_distance_to_gamma_level: Optional[float] = None


@dataclass(kw_only=True)
class MarketContextResult:
    """Advisory-only output. Never a broker call, never an order, never
    an execution side effect — a pure description of whether the broader
    market context supports a hypothetical CALL/PUT setup, for a human or
    a downstream advisory pipeline to independently re-evaluate."""

    status: MarketContextStatus
    confirmed: bool
    reason_code: str
    reason: str = ""
    warnings: list[str] = field(default_factory=list)
    context_score: Optional[float] = None
    # False when the caller supplied no GEX context. GEX is OPTIONAL
    # enrichment, not a requirement: absent GEX degrades the context to a
    # warning (see GEX_UNAVAILABLE in market_validator) instead of rejecting.
    # Never infer a regime or a flip side to fill the gap.
    gex_available: bool = True
    # Denominator for ``context_score``. Signa is never included; GEX is
    # included only when available. This prevents an absent/observational
    # component from being silently scored as aligned or opposed.
    context_score_max: Optional[float] = None


def _invalid(reason_code: str, reason: str) -> MarketContextResult:
    return MarketContextResult(
        status="INVALID", confirmed=False, reason_code=reason_code, reason=reason
    )
