"""options_manager/context/market_validator.py

Advisory-only options market-context validator — Increment 3. Pure
function of its explicit, caller-supplied inputs -> MarketContextResult.
Produces real VALID/CAUTION/INVALID content instead of requiring a
caller-supplied boolean `confirmed` the way the Increment 1 placeholder
(options_manager.strategies.base.StrategyMarketContext) does. This module
is a separate, additive, standalone evaluator — it is not wired into
strat_212.py.

Performs no I/O of any kind: no market-data fetch, no broker call, no
order placement, no execution, no config read, no credential access.
Does not import the existing live context/market_context.py loader.

Signa fields are accepted only as observational caller telemetry. The
completed effectiveness audit found no material incremental value, so
missing, aligned, neutral, or opposed Signa state cannot change status,
confirmation, warnings, or context_score in this validator.
"""

from __future__ import annotations

from .base import MarketContextInputs, MarketContextResult, _invalid


def evaluate_market_context(inputs: MarketContextInputs) -> MarketContextResult:
    """Pure function of explicit market-structure inputs -> result.

    Fails closed to INVALID for an invalid direction, a missing ticker or
    underlying price, missing SPY/QQQ context, missing higher-timeframe
    alignment, unresolved/high event risk, a gamma level too close to price
    when real GEX is available and a threshold is supplied, both SPY and QQQ
    trending against the requested direction, or fully opposite
    higher-timeframe alignment. Returns CAUTION when the actionable context is
    merely mixed.

    GEX is optional enrichment, not a required dependency. When absent the
    result carries ``gex_available=False`` and a ``GEX_UNAVAILABLE`` warning,
    gamma-wall targeting is skipped, and the GEX component drops out of both
    ``context_score`` and ``context_score_max``. No neutral regime or flip side
    is invented.

    Signa is observational only. ``signa_direction``, ``signa_grade``, and
    ``signa_score`` may be present, absent, aligned, neutral, stale, or opposed;
    none of those states can authorize, veto, caution, confirm, or score the
    setup. Callers may retain the raw Signa fields separately for journaling.
    """
    direction = inputs.direction
    if direction not in ("CALL", "PUT"):
        return _invalid("invalid_direction", f"direction {direction!r} must be CALL or PUT")

    if not inputs.ticker:
        return _invalid("missing_ticker", "ticker is required")

    if inputs.underlying_price is None:
        return _invalid("missing_underlying_price", "underlying_price is required")

    if (
        inputs.spy_trend is None
        or inputs.qqq_trend is None
        or inputs.spy_above_flip is None
        or inputs.qqq_above_flip is None
    ):
        return _invalid(
            "missing_spy_qqq_context",
            "spy_trend, qqq_trend, spy_above_flip, and qqq_above_flip are all required",
        )

    if inputs.higher_timeframe_alignment is None:
        return _invalid(
            "missing_htf_alignment", "higher_timeframe_alignment is required"
        )

    if inputs.event_risk is None:
        return _invalid("missing_event_risk", "event_risk is required")
    if inputs.event_risk == "high":
        return _invalid("event_risk_high", "event_risk is high")

    # GEX is optional enrichment, not a gate input. Both fields must be present
    # for any GEX-derived judgement to run; a half-supplied block is unusable,
    # and inferring the missing half would fabricate context.
    gex_available = inputs.gex_regime is not None and inputs.price_above_gex_flip is not None

    warnings: list[str] = []

    if not gex_available:
        warnings.append(
            "GEX_UNAVAILABLE: no gex_regime/price_above_gex_flip supplied; "
            "evaluated on SPY/QQQ + higher-timeframe context only"
        )

    # Gamma-wall targeting runs ONLY against real GEX data. Without it there are
    # no walls to measure distance to, so the threshold cannot be applied.
    if gex_available:
        if direction == "CALL":
            if (
                inputs.min_distance_to_gamma_level is not None
                and inputs.distance_to_gamma_resistance is not None
                and inputs.distance_to_gamma_resistance < inputs.min_distance_to_gamma_level
            ):
                return _invalid(
                    "gamma_resistance_too_close",
                    f"distance_to_gamma_resistance {inputs.distance_to_gamma_resistance} is "
                    f"below minimum {inputs.min_distance_to_gamma_level}",
                )
        else:
            if (
                inputs.min_distance_to_gamma_level is not None
                and inputs.distance_to_gamma_support is not None
                and inputs.distance_to_gamma_support < inputs.min_distance_to_gamma_level
            ):
                return _invalid(
                    "gamma_support_too_close",
                    f"distance_to_gamma_support {inputs.distance_to_gamma_support} is below "
                    f"minimum {inputs.min_distance_to_gamma_level}",
                )
    elif inputs.min_distance_to_gamma_level is not None and (
        inputs.distance_to_gamma_resistance is not None
        or inputs.distance_to_gamma_support is not None
    ):
        # Say so rather than silently dropping a threshold the caller set.
        warnings.append(
            "gamma-distance inputs ignored: min_distance_to_gamma_level cannot be "
            "enforced without GEX context"
        )

    opposing_trend = "bearish" if direction == "CALL" else "bullish"
    aligned_trend = "bullish" if direction == "CALL" else "bearish"

    spy_opposing = inputs.spy_trend == opposing_trend
    qqq_opposing = inputs.qqq_trend == opposing_trend
    if spy_opposing and qqq_opposing:
        return _invalid(
            "market_conflict",
            f"both SPY and QQQ trend {opposing_trend}, opposing a {direction} setup",
        )

    spy_aligned = inputs.spy_trend == aligned_trend
    qqq_aligned = inputs.qqq_trend == aligned_trend
    if not (spy_aligned and qqq_aligned):
        warnings.append(
            f"SPY/QQQ trend not both aligned with {direction} "
            f"(spy_trend={inputs.spy_trend!r}, qqq_trend={inputs.qqq_trend!r})"
        )

    gex_aligned: bool | None = None
    if gex_available:
        gex_aligned = (
            inputs.price_above_gex_flip
            if direction == "CALL"
            else not inputs.price_above_gex_flip
        )
        if not gex_aligned:
            warnings.append(
                f"underlying price is not on the preferred side of the GEX flip for {direction}"
            )

    if inputs.higher_timeframe_alignment == "opposite":
        return _invalid(
            "htf_opposite",
            "higher_timeframe_alignment is opposite the requested direction",
        )
    if inputs.higher_timeframe_alignment == "mixed":
        warnings.append("higher_timeframe_alignment is mixed, not fully aligned")

    # Actionable context score contains only independent market-structure
    # components. Signa is deliberately absent from numerator and denominator.
    context_score = (
        (1.0 if spy_aligned else 0.0)
        + (1.0 if qqq_aligned else 0.0)
        + (1.0 if inputs.higher_timeframe_alignment == "aligned" else 0.0)
    )
    context_score_max = 3.0
    if gex_available:
        context_score += 1.0 if gex_aligned else 0.0
        context_score_max = 4.0

    if warnings:
        return MarketContextResult(
            status="CAUTION",
            confirmed=True,
            reason_code="context_mixed",
            reason="; ".join(warnings),
            warnings=warnings,
            context_score=context_score,
            context_score_max=context_score_max,
            gex_available=gex_available,
        )

    return MarketContextResult(
        status="VALID",
        confirmed=True,
        reason_code="context_confirmed",
        reason=f"market context supports a {direction} setup",
        context_score=context_score,
        context_score_max=context_score_max,
        gex_available=gex_available,
    )
