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
"""

from __future__ import annotations

from .base import MarketContextInputs, MarketContextResult, _invalid

_WEAK_SIGNA_GRADES = {"D", "F", "weak"}


def evaluate_market_context(inputs: MarketContextInputs) -> MarketContextResult:
    """Pure function of its explicit inputs -> MarketContextResult.

    Fails closed to INVALID for: an invalid direction, a missing ticker
    or underlying_price, missing SPY/QQQ context, missing GEX context,
    missing Signa context, missing higher-timeframe alignment, an
    unresolved or high event risk, a gamma level too close to price (only
    when a min_distance_to_gamma_level threshold is supplied), both SPY
    and QQQ trending against the requested direction, a strong Signa
    conflict, or a fully opposite higher-timeframe alignment. Returns
    CAUTION (not a false VALID) whenever the context is merely mixed —
    e.g. only one of SPY/QQQ aligned, price on the wrong side of the GEX
    flip, a weak/low-grade Signa conflict, or a mixed higher-timeframe
    alignment. Returns VALID only when nothing above triggered a warning.
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

    if inputs.gex_regime is None or inputs.price_above_gex_flip is None:
        return _invalid(
            "missing_gex_context", "gex_regime and price_above_gex_flip are required"
        )

    if (
        inputs.signa_direction is None
        or inputs.signa_grade is None
        or inputs.signa_score is None
    ):
        return _invalid(
            "missing_signa_context",
            "signa_direction, signa_grade, and signa_score are all required",
        )

    if inputs.higher_timeframe_alignment is None:
        return _invalid(
            "missing_htf_alignment", "higher_timeframe_alignment is required"
        )

    if inputs.event_risk is None:
        return _invalid("missing_event_risk", "event_risk is required")
    if inputs.event_risk == "high":
        return _invalid("event_risk_high", "event_risk is high")

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

    opposing_trend = "bearish" if direction == "CALL" else "bullish"
    aligned_trend = "bullish" if direction == "CALL" else "bearish"

    spy_opposing = inputs.spy_trend == opposing_trend
    qqq_opposing = inputs.qqq_trend == opposing_trend
    if spy_opposing and qqq_opposing:
        return _invalid(
            "market_conflict",
            f"both SPY and QQQ trend {opposing_trend}, opposing a {direction} setup",
        )

    warnings: list[str] = []

    opposing_signa = "bearish" if direction == "CALL" else "bullish"
    if inputs.signa_direction == opposing_signa:
        if inputs.signa_grade in _WEAK_SIGNA_GRADES:
            warnings.append(
                f"signa_direction is {inputs.signa_direction} but grade "
                f"{inputs.signa_grade!r} is weak — not treated as a hard conflict"
            )
        else:
            return _invalid(
                "signa_conflict",
                f"signa_direction {inputs.signa_direction!r} (grade "
                f"{inputs.signa_grade!r}) conflicts with {direction}",
            )

    spy_aligned = inputs.spy_trend == aligned_trend
    qqq_aligned = inputs.qqq_trend == aligned_trend
    if not (spy_aligned and qqq_aligned):
        warnings.append(
            f"SPY/QQQ trend not both aligned with {direction} "
            f"(spy_trend={inputs.spy_trend!r}, qqq_trend={inputs.qqq_trend!r})"
        )

    gex_aligned = (
        inputs.price_above_gex_flip if direction == "CALL" else not inputs.price_above_gex_flip
    )
    if not gex_aligned:
        warnings.append(
            f"underlying price is not on the preferred side of the GEX flip for {direction}"
        )

    if inputs.gex_regime == "positive" and inputs.signa_direction == "neutral":
        warnings.append("gex_regime is positive but signa_direction is neutral")

    if inputs.higher_timeframe_alignment == "opposite":
        return _invalid(
            "htf_opposite",
            "higher_timeframe_alignment is opposite the requested direction",
        )
    if inputs.higher_timeframe_alignment == "mixed":
        warnings.append("higher_timeframe_alignment is mixed, not fully aligned")

    aligned_signa = "bullish" if direction == "CALL" else "bearish"
    context_score = (
        (1.0 if spy_aligned else 0.0)
        + (1.0 if qqq_aligned else 0.0)
        + (1.0 if gex_aligned else 0.0)
        + (1.0 if inputs.higher_timeframe_alignment == "aligned" else 0.0)
        + (1.0 if inputs.signa_direction == aligned_signa else 0.0)
    )

    if warnings:
        return MarketContextResult(
            status="CAUTION",
            confirmed=True,
            reason_code="context_mixed",
            reason="; ".join(warnings),
            warnings=warnings,
            context_score=context_score,
        )

    return MarketContextResult(
        status="VALID",
        confirmed=True,
        reason_code="context_confirmed",
        reason=f"market context supports a {direction} setup",
        context_score=context_score,
    )
