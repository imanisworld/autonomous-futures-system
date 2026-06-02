"""Option premium valuation helpers for the advisory options scanner.

Inspired by Stockpile's options-scanner / finance-utility lane, but kept small
and deterministic for this project. These helpers never place orders; they only
add pricing context to alert scoring.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import erf, exp, log, sqrt
from typing import Any


@dataclass(frozen=True)
class OptionValuation:
    theoretical_value: float
    edge_percent: float
    verdict: str
    component_score: int
    reason: str


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _norm_cdf(value: float) -> float:
    return 0.5 * (1.0 + erf(value / sqrt(2.0)))


def black_scholes_price(
    *,
    option_type: str,
    underlying_price: float,
    strike: float,
    dte: float,
    implied_volatility: float,
    risk_free_rate: float = 0.045,
) -> float:
    """Return Black-Scholes fair value for a European call/put.

    `implied_volatility` may be sent as either 0.22 or 22.
    `dte` is calendar days to expiration.
    """

    option = option_type.lower()
    if option not in {"call", "put", "c", "p"}:
        raise ValueError(f"Unsupported option_type: {option_type}")
    if underlying_price <= 0 or strike <= 0:
        raise ValueError("underlying_price and strike must be positive")
    if dte <= 0:
        raise ValueError("dte must be positive")
    if implied_volatility <= 0:
        raise ValueError("implied_volatility must be positive")

    sigma = implied_volatility / 100.0 if implied_volatility > 3 else implied_volatility
    time_years = dte / 365.0
    d1 = (
        log(underlying_price / strike)
        + (risk_free_rate + 0.5 * sigma * sigma) * time_years
    ) / (sigma * sqrt(time_years))
    d2 = d1 - sigma * sqrt(time_years)

    if option in {"call", "c"}:
        value = underlying_price * _norm_cdf(d1) - strike * exp(-risk_free_rate * time_years) * _norm_cdf(d2)
    else:
        value = strike * exp(-risk_free_rate * time_years) * _norm_cdf(-d2) - underlying_price * _norm_cdf(-d1)
    return round(max(value, 0.0), 4)


def evaluate_option_value(data: dict[str, Any]) -> OptionValuation | None:
    """Score whether the quoted option premium is reasonable.

    Returns None when required option fields are missing. This keeps the scanner
    useful for pure setup alerts while enriching alerts that include contract
    data.
    """

    mark = _num(data.get("option_mark") or data.get("mark") or data.get("premium"))
    theoretical = _num(data.get("theoretical_value") or data.get("fair_value"))

    if theoretical is None:
        underlying = _num(data.get("underlying_price") or data.get("spot") or data.get("price"))
        strike = _num(data.get("strike"))
        dte = _num(data.get("dte") or data.get("days_to_expiration"))
        iv = _num(data.get("implied_volatility") or data.get("iv"))
        option_type = str(data.get("option_type") or data.get("right") or "").lower()
        if None in {underlying, strike, dte, iv} or option_type not in {"call", "put", "c", "p"}:
            return None
        theoretical = black_scholes_price(
            option_type=option_type,
            underlying_price=float(underlying),
            strike=float(strike),
            dte=float(dte),
            implied_volatility=float(iv),
            risk_free_rate=float(_num(data.get("risk_free_rate")) or 0.045),
        )

    if mark is None:
        return OptionValuation(
            theoretical_value=round(theoretical, 4),
            edge_percent=0.0,
            verdict="fair_value_only",
            component_score=0,
            reason="fair_value_available_no_mark",
        )
    if mark <= 0 or theoretical <= 0:
        return OptionValuation(
            theoretical_value=round(max(theoretical, 0.0), 4),
            edge_percent=0.0,
            verdict="invalid",
            component_score=-3,
            reason="invalid_option_premium",
        )

    edge = (theoretical - mark) / mark * 100.0
    if edge >= 12:
        verdict = "discount"
        component = 2
        reason = "premium_discount_to_model"
    elif edge <= -15:
        verdict = "overpriced"
        component = -3
        reason = "premium_overpriced_vs_model"
    else:
        verdict = "fair"
        component = 0
        reason = "premium_near_model"

    return OptionValuation(
        theoretical_value=round(theoretical, 4),
        edge_percent=round(edge, 2),
        verdict=verdict,
        component_score=component,
        reason=reason,
    )
