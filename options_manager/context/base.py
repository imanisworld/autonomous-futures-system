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
    """Explicit, caller-supplied market context only. `direction`,
    `ticker`, and `underlying_price` are always required; every other
    field defaults to None (not yet resolved) and must fail closed to
    INVALID, exactly like omitting it entirely — a context validator must
    never assume favorable context by default."""

    direction: Literal["CALL", "PUT"]
    ticker: Optional[str]
    underlying_price: Optional[float]
    spy_trend: Optional[Trend] = None
    qqq_trend: Optional[Trend] = None
    spy_above_flip: Optional[bool] = None
    qqq_above_flip: Optional[bool] = None
    gex_regime: Optional[GexRegime] = None
    price_above_gex_flip: Optional[bool] = None
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


def _invalid(reason_code: str, reason: str) -> MarketContextResult:
    return MarketContextResult(
        status="INVALID", confirmed=False, reason_code=reason_code, reason=reason
    )
