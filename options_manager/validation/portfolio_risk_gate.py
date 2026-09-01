"""Pure advisory portfolio-risk accounting for options_manager.

This module deliberately does not impose a position-count cap. It evaluates
planned dollar risk and reports capital deployed and caller-supplied
correlation groups separately. Canonical intake derives the candidate's risk
from the validated contract premium stop; callers only supply the current
open-position snapshot.

There is no default aggregate open-risk budget. The earlier hardcoded $1,000
was never approved policy, and a limit the operator did not choose is not a
limit -- it is a number that lets a TAKE through. The budget is supplied by
the operator (``OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS``) and, when
absent, every evaluation blocks with an explicit reason. Phase 1 is shadow
and advisory; a blocked verdict costs nothing, an unapproved one is a policy
decision made by accident.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Mapping, Sequence

from .contract_quality_gate import ContractQualityInput
from .proof_packet import ProofPacket

DEFAULT_MAX_TRADE_RISK_DOLLARS = 300.0
CONTRACT_MULTIPLIER = 100

AGGREGATE_RISK_BUDGET_ENV = "OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS"
# Stable reason codes lead each message so the gate that fired is identifiable
# without parsing prose. "missing" and "invalid" are deliberately distinct: an
# operator who set a bad value needs a different fix from one who set nothing.
AGGREGATE_RISK_BUDGET_MISSING_CODE = "aggregate_risk_budget_missing"
AGGREGATE_RISK_BUDGET_INVALID_CODE = "aggregate_risk_budget_invalid"
AGGREGATE_RISK_BUDGET_UNCONFIGURED = (
    f"{AGGREGATE_RISK_BUDGET_MISSING_CODE}: {AGGREGATE_RISK_BUDGET_ENV} is not configured; "
    "no default is assumed"
)


def _invalid_budget_reason(value: float) -> str:
    return (
        f"{AGGREGATE_RISK_BUDGET_INVALID_CODE}: max_aggregate_open_risk_dollars must be a "
        f"finite number > 0 (got {value!r}; an unparseable configured value also arrives "
        "here as nan); no default is substituted"
    )


def _usable_budget(value: float | None) -> bool:
    """True only for a finite, strictly positive budget.

    Checked here, at the gate, not only at config parsing: ``nan`` slips past
    ``<= 0`` and ``inf`` passes ``> 0`` while making every ``projected > cap``
    comparison false -- an unlimited budget wearing a configured value's
    clothes. Neither may reach the comparison below.
    """
    return value is not None and math.isfinite(value) and value > 0


class PortfolioRiskVerdict(str, Enum):
    PASS = "pass"
    BLOCK = "block"


@dataclass(frozen=True, kw_only=True)
class RiskExposure:
    ticker: str
    direction: Literal["CALL", "PUT"]
    planned_dollar_risk: float
    capital_deployed: float = 0.0
    correlation_group: str = ""


@dataclass(frozen=True, kw_only=True)
class PortfolioRiskResult:
    verdict: PortfolioRiskVerdict
    open_position_count: int
    aggregate_open_risk: float
    candidate_risk: float
    projected_open_risk: float
    aggregate_capital_deployed: float
    projected_capital_deployed: float
    correlation_risk: tuple[tuple[str, float], ...] = ()
    blocking_reasons: tuple[str, ...] = ()


def evaluate_portfolio_risk(
    *,
    open_positions: Sequence[RiskExposure],
    candidate: RiskExposure,
    max_trade_risk_dollars: float = DEFAULT_MAX_TRADE_RISK_DOLLARS,
    max_aggregate_open_risk_dollars: float | None = None,
) -> PortfolioRiskResult:
    """Evaluate a candidate against planned risk dollars, never position count.

    ``max_aggregate_open_risk_dollars`` has no default. ``None`` blocks, and the
    exposure arithmetic below still runs so the caller can see what the
    projected risk *would* have been measured against.
    """
    blocking: list[str] = []

    # Same finiteness guard on the per-trade cap: a nan or inf there would
    # silently disable the per-trade comparison in exactly the same way. The
    # cap's value is not changed here.
    if not _usable_budget(max_trade_risk_dollars):
        blocking.append("missing/invalid max_trade_risk_dollars")
    if max_aggregate_open_risk_dollars is None:
        blocking.append(AGGREGATE_RISK_BUDGET_UNCONFIGURED)
    elif not _usable_budget(max_aggregate_open_risk_dollars):
        blocking.append(_invalid_budget_reason(max_aggregate_open_risk_dollars))

    exposures = tuple(open_positions)
    for index, exposure in enumerate((*exposures, candidate)):
        label = "candidate" if index == len(exposures) else f"open_positions[{index}]"
        if not exposure.ticker.strip():
            blocking.append(f"{label} missing ticker")
        if exposure.direction not in ("CALL", "PUT"):
            blocking.append(f"{label} invalid direction")
        if exposure.planned_dollar_risk < 0:
            blocking.append(f"{label} has negative planned_dollar_risk")
        if exposure.capital_deployed < 0:
            blocking.append(f"{label} has negative capital_deployed")

    aggregate_open_risk = sum(p.planned_dollar_risk for p in exposures)
    aggregate_capital = sum(p.capital_deployed for p in exposures)
    projected_open_risk = aggregate_open_risk + candidate.planned_dollar_risk
    projected_capital = aggregate_capital + candidate.capital_deployed

    if _usable_budget(max_trade_risk_dollars) and candidate.planned_dollar_risk > max_trade_risk_dollars:
        blocking.append(
            f"candidate planned risk ${candidate.planned_dollar_risk:.2f} exceeds "
            f"per-trade cap ${max_trade_risk_dollars:.2f}"
        )

    if (
        _usable_budget(max_aggregate_open_risk_dollars)
        and projected_open_risk > max_aggregate_open_risk_dollars
    ):
        blocking.append(
            f"projected aggregate open risk ${projected_open_risk:.2f} exceeds "
            f"cap ${max_aggregate_open_risk_dollars:.2f}"
        )

    grouped: dict[str, float] = {}
    for exposure in (*exposures, candidate):
        group = exposure.correlation_group.strip()
        if group:
            grouped[group] = grouped.get(group, 0.0) + exposure.planned_dollar_risk

    return PortfolioRiskResult(
        verdict=PortfolioRiskVerdict.BLOCK if blocking else PortfolioRiskVerdict.PASS,
        open_position_count=len(exposures),
        aggregate_open_risk=aggregate_open_risk,
        candidate_risk=candidate.planned_dollar_risk,
        projected_open_risk=projected_open_risk,
        aggregate_capital_deployed=aggregate_capital,
        projected_capital_deployed=projected_capital,
        correlation_risk=tuple(sorted(grouped.items())),
        blocking_reasons=tuple(blocking),
    )


def _blocked(*reasons: str) -> PortfolioRiskResult:
    return PortfolioRiskResult(
        verdict=PortfolioRiskVerdict.BLOCK,
        open_position_count=0,
        aggregate_open_risk=0.0,
        candidate_risk=0.0,
        projected_open_risk=0.0,
        aggregate_capital_deployed=0.0,
        projected_capital_deployed=0.0,
        blocking_reasons=tuple(reasons),
    )


def _coerce_exposure(payload: Any, *, label: str) -> tuple[RiskExposure | None, tuple[str, ...]]:
    if not isinstance(payload, Mapping):
        return None, (f"{label} must be a dict-like mapping",)
    try:
        ticker = str(payload["ticker"]).strip()
        direction = str(payload["direction"]).strip().upper()
        planned_dollar_risk = float(payload["planned_dollar_risk"])
        capital_deployed = float(payload.get("capital_deployed", 0.0))
        correlation_group = str(payload.get("correlation_group", ""))
    except (KeyError, TypeError, ValueError) as exc:
        return None, (f"{label} malformed: {exc}",)

    errors: list[str] = []
    if not ticker:
        errors.append(f"{label} missing ticker")
    if direction not in ("CALL", "PUT"):
        errors.append(f"{label} direction must be CALL or PUT")
    if planned_dollar_risk < 0:
        errors.append(f"{label} planned_dollar_risk must be >= 0")
    if capital_deployed < 0:
        errors.append(f"{label} capital_deployed must be >= 0")
    if errors:
        return None, tuple(errors)

    return (
        RiskExposure(
            ticker=ticker,
            direction=direction,
            planned_dollar_risk=planned_dollar_risk,
            capital_deployed=capital_deployed,
            correlation_group=correlation_group,
        ),
        (),
    )


def _proof_contract_consistency_errors(
    proof_packet: ProofPacket,
    contract: ContractQualityInput,
) -> tuple[str, ...]:
    errors: list[str] = []
    exact_pairs = (
        ("ticker", proof_packet.ticker, contract.ticker),
        ("direction", proof_packet.direction, contract.direction),
        ("expiration", proof_packet.expiration, contract.expiration),
        ("max_contracts", proof_packet.max_contracts, contract.max_contracts),
    )
    for name, proof_value, contract_value in exact_pairs:
        if proof_value != contract_value:
            errors.append(
                f"proof/contract mismatch for {name}: {proof_value!r} != {contract_value!r}"
            )

    numeric_pairs = (
        ("strike", proof_packet.strike, contract.strike),
        ("premium", proof_packet.premium, contract.premium),
        ("bid", proof_packet.bid, contract.bid),
        ("ask", proof_packet.ask, contract.ask),
        ("spread_percent", proof_packet.spread_percent, contract.spread_percent),
        ("max_dollar_risk", proof_packet.max_dollar_risk, contract.max_dollar_risk),
    )
    for name, proof_value, contract_value in numeric_pairs:
        if abs(float(proof_value) - float(contract_value)) > 1e-9:
            errors.append(
                f"proof/contract mismatch for {name}: {proof_value!r} != {contract_value!r}"
            )

    if proof_packet.volume != contract.volume:
        errors.append(
            f"proof/contract mismatch for volume: {proof_packet.volume!r} != {contract.volume!r}"
        )
    if proof_packet.open_interest != contract.open_interest:
        errors.append(
            "proof/contract mismatch for open_interest: "
            f"{proof_packet.open_interest!r} != {contract.open_interest!r}"
        )

    if contract.premium_stop is None:
        errors.append("contract_quality missing numeric premium_stop")
    else:
        try:
            proof_stop = float(proof_packet.premium_stop)
        except (TypeError, ValueError):
            errors.append("proof_packet premium_stop must be numeric for canonical intake")
        else:
            if abs(proof_stop - contract.premium_stop) > 1e-9:
                errors.append(
                    "proof/contract mismatch for premium_stop: "
                    f"{proof_stop!r} != {contract.premium_stop!r}"
                )

    return tuple(errors)


def check_portfolio_risk_intake(
    payload: Any,
    *,
    proof_packet: ProofPacket | None,
    contract: ContractQualityInput | None,
    max_trade_risk_dollars: float = DEFAULT_MAX_TRADE_RISK_DOLLARS,
    max_aggregate_open_risk_dollars: float | None = None,
) -> PortfolioRiskResult:
    """Canonical portfolio snapshot intake.

    The caller supplies current open-position risk and an optional correlation
    label for the new candidate. The candidate's own risk and debit are derived
    from the validated contract, so they cannot be overridden independently.
    """
    if not isinstance(payload, Mapping):
        return _blocked("missing or malformed portfolio_risk snapshot")
    if proof_packet is None or contract is None:
        return _blocked("cannot evaluate portfolio risk without valid proof and contract facts")

    consistency_errors = _proof_contract_consistency_errors(proof_packet, contract)
    if consistency_errors:
        return _blocked(*consistency_errors)

    raw_open_positions = payload.get("open_positions")
    if not isinstance(raw_open_positions, list):
        return _blocked(
            "portfolio_risk.open_positions must be supplied as a list (use [] when flat)"
        )

    open_positions: list[RiskExposure] = []
    errors: list[str] = []
    for index, raw in enumerate(raw_open_positions):
        exposure, exposure_errors = _coerce_exposure(raw, label=f"open_positions[{index}]")
        errors.extend(exposure_errors)
        if exposure is not None:
            open_positions.append(exposure)

    if errors:
        return _blocked(*errors)

    assert contract.premium_stop is not None
    candidate_risk = (
        (contract.premium - contract.premium_stop)
        * CONTRACT_MULTIPLIER
        * contract.max_contracts
    )
    candidate_capital = contract.premium * CONTRACT_MULTIPLIER * contract.max_contracts
    candidate = RiskExposure(
        ticker=proof_packet.ticker,
        direction=proof_packet.direction,
        planned_dollar_risk=max(0.0, candidate_risk),
        capital_deployed=max(0.0, candidate_capital),
        correlation_group=str(payload.get("candidate_correlation_group", "")),
    )

    return evaluate_portfolio_risk(
        open_positions=open_positions,
        candidate=candidate,
        max_trade_risk_dollars=max_trade_risk_dollars,
        max_aggregate_open_risk_dollars=max_aggregate_open_risk_dollars,
    )
