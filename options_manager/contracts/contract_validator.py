"""options_manager/contracts/contract_validator.py

Advisory-only options contract-constraints validator — Increment 4. Pure
function of its explicit, caller-supplied inputs -> ContractConstraintsResult.
Produces real VALID/CAUTION/INVALID content instead of requiring a
caller-supplied boolean `constraints_met` the way the Increment 1
placeholder (options_manager.strategies.base.StrategyContractConstraints)
does. This module is a separate, additive, standalone evaluator — it is
not wired into strat_212.py.

Performs no I/O of any kind: no option-chain fetch, no contract
selection, no broker call, no order placement, no execution, no config
read, no credential access. Does not import options_companion (a
credentialed module with a different trust boundary).
"""

from __future__ import annotations

from typing import Optional

from .base import ContractConstraintsInputs, ContractConstraintsResult, _invalid

def _is_finite_number(value: object) -> bool:
    """True only for a real, finite int/float (bool excluded). Kept
    import-free on purpose: this module's import surface is asserted by
    tests to stay within options_manager and the stdlib names already used."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value == value and value not in (float("inf"), float("-inf"))


_NUMERIC_INPUT_FIELDS = (
    "dte",
    "strike",
    "premium",
    "bid",
    "ask",
    "spread_percent",
    "volume",
    "open_interest",
    "delta",
    "theta",
    "iv",
    "max_premium",
    "max_spread_percent",
    "min_volume",
    "min_open_interest",
    "min_dte",
    "max_theta_abs",
)
_NEAR_THRESHOLD_MARGIN = 0.20
_ELEVATED_IV_THRESHOLD = 0.80


def _near_max(value: Optional[float], max_value: Optional[float]) -> bool:
    if value is None or max_value is None:
        return False
    return value <= max_value and value >= max_value * (1 - _NEAR_THRESHOLD_MARGIN)


def _near_min(value: Optional[float], min_value: Optional[float]) -> bool:
    if value is None or min_value is None:
        return False
    return value >= min_value and value <= min_value * (1 + _NEAR_THRESHOLD_MARGIN)


def evaluate_contract_constraints(
    inputs: ContractConstraintsInputs,
) -> ContractConstraintsResult:
    """Pure function of its explicit inputs -> ContractConstraintsResult.

    Fails closed to INVALID for: an invalid direction, a missing ticker,
    expiration, dte, strike, premium, bid, ask, spread_percent, volume,
    open_interest, delta, theta, or iv, or any missing risk limit
    (max_premium, max_spread_percent, min_volume, min_open_interest,
    min_dte). Hard-rejects to INVALID for: a non-positive bid, an
    ask at or below bid, premium over max_premium, spread over
    max_spread_percent, volume/open_interest below their minimums, dte
    below min_dte, abs(theta) over max_theta_abs (only when
    max_theta_abs is supplied), or earnings_risk/event_risk == "HIGH".
    Returns CAUTION (not a false VALID) when spread/dte/volume/open_interest/
    theta are within a threshold's margin but not over it, or when iv is
    elevated (no explicit hard IV limit exists in this validator, so this
    is a soft, built-in reference check rather than a hard reject).
    Returns VALID only when nothing above triggered a warning.
    """
    direction = inputs.direction
    if direction not in ("CALL", "PUT"):
        return _invalid("invalid_direction", f"direction {direction!r} must be CALL or PUT")

    if not inputs.ticker:
        return _invalid("missing_ticker", "ticker is required")
    if not inputs.expiration:
        return _invalid("missing_expiration", "expiration is required")
    if inputs.dte is None:
        return _invalid("missing_dte", "dte is required")
    if inputs.strike is None:
        return _invalid("missing_strike", "strike is required")
    if inputs.premium is None:
        return _invalid("missing_premium", "premium is required")
    if inputs.bid is None:
        return _invalid("missing_bid", "bid is required")
    if inputs.ask is None:
        return _invalid("missing_ask", "ask is required")
    if inputs.spread_percent is None:
        return _invalid("missing_spread_percent", "spread_percent is required")
    if inputs.volume is None:
        return _invalid("missing_volume", "volume is required")
    if inputs.open_interest is None:
        return _invalid("missing_open_interest", "open_interest is required")
    if inputs.delta is None:
        return _invalid("missing_delta", "delta is required")
    if inputs.theta is None:
        return _invalid("missing_theta", "theta is required")
    if inputs.iv is None:
        return _invalid("missing_iv", "iv is required")

    if (
        inputs.max_premium is None
        or inputs.max_spread_percent is None
        or inputs.min_volume is None
        or inputs.min_open_interest is None
        or inputs.min_dte is None
    ):
        return _invalid(
            "missing_risk_limits",
            "max_premium, max_spread_percent, min_volume, min_open_interest, "
            "and min_dte are all required",
        )

    for name in _NUMERIC_INPUT_FIELDS:
        value = getattr(inputs, name)
        if value is None:
            continue
        if not _is_finite_number(value):
            return _invalid(
                f"non_finite_{name}",
                f"{name} {value!r} must be a finite number",
            )

    if inputs.bid <= 0:
        return _invalid("bid_invalid", f"bid {inputs.bid} must be positive")
    if inputs.ask <= inputs.bid:
        return _invalid(
            "ask_invalid", f"ask {inputs.ask} must be greater than bid {inputs.bid}"
        )

    if inputs.premium > inputs.max_premium:
        return _invalid(
            "premium_over_max",
            f"premium {inputs.premium} is above maximum {inputs.max_premium}",
        )
    if inputs.spread_percent > inputs.max_spread_percent:
        return _invalid(
            "spread_too_wide",
            f"spread_percent {inputs.spread_percent} is above maximum "
            f"{inputs.max_spread_percent}",
        )
    if inputs.volume < inputs.min_volume:
        return _invalid(
            "volume_too_low", f"volume {inputs.volume} is below minimum {inputs.min_volume}"
        )
    if inputs.open_interest < inputs.min_open_interest:
        return _invalid(
            "open_interest_too_low",
            f"open_interest {inputs.open_interest} is below minimum "
            f"{inputs.min_open_interest}",
        )
    if inputs.dte < inputs.min_dte:
        return _invalid("dte_too_short", f"dte {inputs.dte} is below minimum {inputs.min_dte}")

    if inputs.max_theta_abs is not None and abs(inputs.theta) > inputs.max_theta_abs:
        return _invalid(
            "theta_too_high",
            f"abs(theta) {abs(inputs.theta)} is above maximum {inputs.max_theta_abs}",
        )

    if inputs.earnings_risk == "HIGH":
        return _invalid("earnings_risk_high", "earnings_risk is HIGH")
    if inputs.event_risk == "HIGH":
        return _invalid("event_risk_high", "event_risk is HIGH")

    warnings: list[str] = []

    spread_ok = not _near_max(inputs.spread_percent, inputs.max_spread_percent)
    if not spread_ok:
        warnings.append(
            f"spread_percent {inputs.spread_percent} is close to maximum "
            f"{inputs.max_spread_percent}"
        )

    dte_ok = not _near_min(inputs.dte, inputs.min_dte)
    if not dte_ok:
        warnings.append(f"dte {inputs.dte} is close to minimum {inputs.min_dte}")

    volume_ok = not _near_min(inputs.volume, inputs.min_volume)
    if not volume_ok:
        warnings.append(f"volume {inputs.volume} barely clears minimum {inputs.min_volume}")

    oi_ok = not _near_min(inputs.open_interest, inputs.min_open_interest)
    if not oi_ok:
        warnings.append(
            f"open_interest {inputs.open_interest} barely clears minimum "
            f"{inputs.min_open_interest}"
        )

    theta_ok = not (
        inputs.max_theta_abs is not None and _near_max(abs(inputs.theta), inputs.max_theta_abs)
    )
    if not theta_ok:
        warnings.append(
            f"abs(theta) {abs(inputs.theta)} is close to maximum {inputs.max_theta_abs}"
        )

    iv_ok = inputs.iv < _ELEVATED_IV_THRESHOLD
    if not iv_ok:
        warnings.append(
            f"iv {inputs.iv} is elevated (no explicit hard IV limit was supplied)"
        )

    contract_score = float(
        sum([spread_ok, dte_ok, volume_ok, oi_ok, theta_ok, iv_ok])
    )

    if warnings:
        return ContractConstraintsResult(
            status="CAUTION",
            confirmed=True,
            reason_code="contract_mixed",
            reason="; ".join(warnings),
            warnings=warnings,
            contract_score=contract_score,
        )

    return ContractConstraintsResult(
        status="VALID",
        confirmed=True,
        reason_code="contract_confirmed",
        reason=f"contract meets all {direction} constraints",
        contract_score=contract_score,
    )
