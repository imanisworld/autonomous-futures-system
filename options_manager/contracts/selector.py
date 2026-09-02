"""Pure Phase-1 options contract shortlist.

This module does not fetch an option chain and does not place or prepare an
order. It consumes caller-supplied contract candidates and delegates the core
liquidity/DTE/Greeks/event checks to the existing
``evaluate_contract_constraints`` authority.

Selection-specific policy is explicit and has no trading defaults. In
particular, the caller must supply DTE, liquidity, premium, theta, and delta
limits. A preferred delta is optional: without one, multiple valid contracts
remain a shortlist and this module refuses to pretend it knows which contract
is best. This makes the ordering useful for a forward shadow campaign without
claiming an unproven contract-selection edge.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Optional

from .base import ContractConstraintsInputs
from .contract_validator import evaluate_contract_constraints

Direction = Literal["CALL", "PUT"]
RiskLevel = Literal["NONE", "LOW", "HIGH"]
SelectionStatus = Literal[
    "INVALID_REQUEST",
    "NO_ELIGIBLE",
    "CAUTION_ONLY",
    "SHORTLIST",
    "SOLE_ELIGIBLE",
    "PREFERRED_CANDIDATE",
]


@dataclass(frozen=True, kw_only=True)
class ContractCandidate:
    """One normalized option-chain candidate; every value is caller supplied."""

    symbol: str
    ticker: str
    direction: Direction
    expiration: Optional[str]
    dte: Optional[int]
    strike: Optional[float]
    premium: Optional[float]
    bid: Optional[float]
    ask: Optional[float]
    volume: Optional[int]
    open_interest: Optional[int]
    delta: Optional[float]
    theta: Optional[float]
    iv: Optional[float]
    earnings_risk: Optional[RiskLevel]
    event_risk: Optional[RiskLevel]


def _finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(parsed)


@dataclass(frozen=True, kw_only=True)
class ContractSelectionPolicy:
    """Explicit operator/shadow-campaign limits; no numeric defaults."""

    max_premium_per_share: float
    max_spread_percent: float
    min_volume: int
    min_open_interest: int
    min_dte: int
    max_theta_abs: float
    min_abs_delta: float
    max_abs_delta: float
    preferred_abs_delta: Optional[float] = None

    def validation_errors(self) -> tuple[str, ...]:
        errors: list[str] = []
        numeric_positive = (
            ("max_premium_per_share", self.max_premium_per_share),
            ("max_spread_percent", self.max_spread_percent),
            ("max_theta_abs", self.max_theta_abs),
        )
        for name, value in numeric_positive:
            if not _finite_number(value) or float(value) <= 0:
                errors.append(f"{name} must be a finite number > 0")
        if isinstance(self.min_volume, bool) or not isinstance(self.min_volume, int) or self.min_volume < 0:
            errors.append("min_volume must be an integer >= 0")
        if (
            isinstance(self.min_open_interest, bool)
            or not isinstance(self.min_open_interest, int)
            or self.min_open_interest < 0
        ):
            errors.append("min_open_interest must be an integer >= 0")
        if isinstance(self.min_dte, bool) or not isinstance(self.min_dte, int) or self.min_dte < 0:
            errors.append("min_dte must be an integer >= 0")

        delta_range_valid = (
            _finite_number(self.min_abs_delta)
            and _finite_number(self.max_abs_delta)
            and float(self.min_abs_delta) > 0
            and float(self.max_abs_delta) <= 1
            and float(self.min_abs_delta) <= float(self.max_abs_delta)
        )
        if not delta_range_valid:
            errors.append("delta range must satisfy 0 < min_abs_delta <= max_abs_delta <= 1")
        if self.preferred_abs_delta is not None:
            preferred_valid = _finite_number(self.preferred_abs_delta) and delta_range_valid
            if preferred_valid:
                preferred = float(self.preferred_abs_delta)
                preferred_valid = (
                    float(self.min_abs_delta) <= preferred <= float(self.max_abs_delta)
                )
            if not preferred_valid:
                errors.append("preferred_abs_delta must fall inside the configured delta range")
        return tuple(errors)


@dataclass(frozen=True, kw_only=True)
class ContractSelectionRequest:
    ticker: str
    direction: Direction
    candidates: tuple[ContractCandidate, ...]
    policy: ContractSelectionPolicy


@dataclass(frozen=True, kw_only=True)
class EvaluatedContractCandidate:
    candidate: ContractCandidate
    validator_status: str
    reason_code: str
    reason: str
    spread_percent: Optional[float]
    warnings: tuple[str, ...] = ()
    delta_distance: Optional[float] = None

    @property
    def valid_without_caution(self) -> bool:
        return self.validator_status == "VALID"


@dataclass(frozen=True, kw_only=True)
class ContractShortlistResult:
    status: SelectionStatus
    selected: Optional[EvaluatedContractCandidate]
    eligible: tuple[EvaluatedContractCandidate, ...]
    rejected: tuple[EvaluatedContractCandidate, ...]
    blocking_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def _spread_percent(bid: object, ask: object) -> Optional[float]:
    """Raw quote arithmetic only; never a fallback value.

    A crossed or non-positive quote still yields its actual (zero or negative)
    spread so the shared validator can reject it with its own ``bid_invalid`` /
    ``ask_invalid`` reason instead of a misleading ``missing_spread_percent``.
    ``None`` is returned only when the arithmetic is undefined: a missing or
    non-finite side, or a non-positive midpoint.
    """
    try:
        bid_value = float(bid)
        ask_value = float(ask)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(bid_value) or not math.isfinite(ask_value):
        return None
    midpoint = (bid_value + ask_value) / 2.0
    if not math.isfinite(midpoint) or midpoint <= 0:
        return None
    spread = (ask_value - bid_value) / midpoint * 100.0
    return spread if math.isfinite(spread) else None


def _local_rejection(
    candidate: ContractCandidate,
    reason_code: str,
    reason: str,
    *,
    spread_percent: Optional[float] = None,
) -> EvaluatedContractCandidate:
    return EvaluatedContractCandidate(
        candidate=candidate,
        validator_status="INVALID",
        reason_code=reason_code,
        reason=reason,
        spread_percent=spread_percent,
    )


def _malformed_numeric_reason(candidate: ContractCandidate) -> Optional[str]:
    for name in (
        "dte",
        "strike",
        "premium",
        "bid",
        "ask",
        "volume",
        "open_interest",
        "delta",
        "theta",
        "iv",
    ):
        value = getattr(candidate, name)
        if value is not None and not _finite_number(value):
            return name
    return None


def _evaluate_candidate(
    candidate: ContractCandidate,
    *,
    ticker: str,
    direction: Direction,
    policy: ContractSelectionPolicy,
) -> EvaluatedContractCandidate:
    normalized_ticker = str(candidate.ticker or "").strip().upper()
    if not str(candidate.symbol or "").strip():
        return _local_rejection(candidate, "missing_contract_symbol", "contract symbol is required")
    if normalized_ticker != ticker:
        return _local_rejection(
            candidate,
            "ticker_mismatch",
            f"candidate ticker {normalized_ticker!r} does not match request {ticker!r}",
        )
    if candidate.direction != direction:
        return _local_rejection(
            candidate,
            "direction_mismatch",
            f"candidate direction {candidate.direction!r} does not match request {direction!r}",
        )
    if candidate.earnings_risk is None or candidate.event_risk is None:
        return _local_rejection(
            candidate,
            "event_risk_missing",
            "earnings_risk and event_risk must both be explicitly resolved",
        )
    valid_risk_levels = ("NONE", "LOW", "HIGH")
    if (
        candidate.earnings_risk not in valid_risk_levels
        or candidate.event_risk not in valid_risk_levels
    ):
        return _local_rejection(
            candidate,
            "event_risk_invalid",
            "earnings_risk and event_risk must be NONE, LOW, or HIGH",
        )

    malformed = _malformed_numeric_reason(candidate)
    if malformed is not None:
        return _local_rejection(
            candidate,
            f"invalid_{malformed}",
            f"{malformed} must be finite numeric data when supplied",
        )
    if candidate.strike is not None and candidate.strike <= 0:
        return _local_rejection(candidate, "invalid_strike", "strike must be > 0")
    if candidate.premium is not None and candidate.premium <= 0:
        return _local_rejection(candidate, "invalid_premium", "premium must be > 0")
    if candidate.iv is not None and candidate.iv <= 0:
        return _local_rejection(candidate, "invalid_iv", "iv must be > 0")
    if candidate.delta is None:
        return _local_rejection(candidate, "missing_delta", "delta is required")

    abs_delta = abs(float(candidate.delta))
    if abs_delta < policy.min_abs_delta or abs_delta > policy.max_abs_delta:
        return _local_rejection(
            candidate,
            "delta_out_of_range",
            f"abs(delta) {abs_delta:g} outside configured range "
            f"[{policy.min_abs_delta:g}, {policy.max_abs_delta:g}]",
        )

    spread = _spread_percent(candidate.bid, candidate.ask)
    constraints = ContractConstraintsInputs(
        direction=direction,
        ticker=ticker,
        expiration=candidate.expiration,
        dte=candidate.dte,
        strike=candidate.strike,
        premium=candidate.premium,
        bid=candidate.bid,
        ask=candidate.ask,
        spread_percent=spread,
        volume=candidate.volume,
        open_interest=candidate.open_interest,
        delta=candidate.delta,
        theta=candidate.theta,
        iv=candidate.iv,
        max_premium=policy.max_premium_per_share,
        max_spread_percent=policy.max_spread_percent,
        min_volume=policy.min_volume,
        min_open_interest=policy.min_open_interest,
        min_dte=policy.min_dte,
        max_theta_abs=policy.max_theta_abs,
        earnings_risk=candidate.earnings_risk,
        event_risk=candidate.event_risk,
    )
    result = evaluate_contract_constraints(constraints)
    delta_distance = (
        abs(abs_delta - policy.preferred_abs_delta)
        if policy.preferred_abs_delta is not None
        else None
    )
    return EvaluatedContractCandidate(
        candidate=candidate,
        validator_status=result.status,
        reason_code=result.reason_code,
        reason=result.reason,
        spread_percent=spread,
        warnings=tuple(result.warnings),
        delta_distance=delta_distance,
    )


def _ranking_key(evaluated: EvaluatedContractCandidate) -> tuple[float, float, int, int, float]:
    """Transparent shortlist ordering, not an assertion of trading edge."""
    candidate = evaluated.candidate
    delta_distance = evaluated.delta_distance if evaluated.delta_distance is not None else math.inf
    spread = evaluated.spread_percent if evaluated.spread_percent is not None else math.inf
    oi = candidate.open_interest if candidate.open_interest is not None else -1
    volume = candidate.volume if candidate.volume is not None else -1
    strike = candidate.strike if candidate.strike is not None else math.inf
    return (delta_distance, spread, -oi, -volume, strike)


def shortlist_contracts(request: ContractSelectionRequest) -> ContractShortlistResult:
    """Validate and shortlist contracts without market-data or order side effects.

    Multiple clean contracts are not collapsed to a single candidate unless the
    caller explicitly supplied ``preferred_abs_delta``. CAUTION contracts are
    retained for human/shadow review but are never automatically selected.
    """

    ticker = str(request.ticker or "").strip().upper()
    request_errors: list[str] = list(request.policy.validation_errors())
    if not ticker:
        request_errors.append("ticker is required")
    if request.direction not in ("CALL", "PUT"):
        request_errors.append("direction must be CALL or PUT")
    if not request.candidates:
        request_errors.append("at least one contract candidate is required")
    if request_errors:
        return ContractShortlistResult(
            status="INVALID_REQUEST",
            selected=None,
            eligible=(),
            rejected=(),
            blocking_reasons=tuple(request_errors),
        )

    evaluated = tuple(
        _evaluate_candidate(
            candidate,
            ticker=ticker,
            direction=request.direction,
            policy=request.policy,
        )
        for candidate in request.candidates
    )
    eligible = tuple(item for item in evaluated if item.validator_status != "INVALID")
    rejected = tuple(item for item in evaluated if item.validator_status == "INVALID")
    clean = tuple(item for item in eligible if item.valid_without_caution)
    cautions = tuple(item for item in eligible if not item.valid_without_caution)
    warnings = tuple(
        dict.fromkeys(warning for item in cautions for warning in item.warnings)
    )

    if not eligible:
        return ContractShortlistResult(
            status="NO_ELIGIBLE",
            selected=None,
            eligible=(),
            rejected=rejected,
            blocking_reasons=("no contract passed the explicit selection policy",),
        )
    if not clean:
        return ContractShortlistResult(
            status="CAUTION_ONLY",
            selected=None,
            eligible=eligible,
            rejected=rejected,
            warnings=warnings,
        )

    ranked_clean = tuple(sorted(clean, key=_ranking_key))
    ranked_eligible = (*ranked_clean, *cautions)
    if len(ranked_clean) == 1:
        return ContractShortlistResult(
            status="SOLE_ELIGIBLE",
            selected=ranked_clean[0],
            eligible=ranked_eligible,
            rejected=rejected,
            warnings=warnings,
        )
    if request.policy.preferred_abs_delta is None:
        return ContractShortlistResult(
            status="SHORTLIST",
            selected=None,
            eligible=ranked_eligible,
            rejected=rejected,
            warnings=warnings,
        )
    return ContractShortlistResult(
        status="PREFERRED_CANDIDATE",
        selected=ranked_clean[0],
        eligible=ranked_eligible,
        rejected=rejected,
        warnings=warnings,
    )
