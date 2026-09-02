"""Pure, measurement-only risk telemetry for advisory options theses.

The canonical portfolio gate remains the risk authority. This module does not
choose limits or re-size positions; it only reconciles already-persisted plan,
contract, and risk facts into a calibration-friendly snapshot.

Important distinctions are kept explicit:

* planned stop risk is not full premium debit;
* current aggregate risk is not projected aggregate risk;
* position count is telemetry only and never a rejection gate here; and
* recorded policy caps are evidence about the decision that was made, not a
  recommendation that those caps are optimal.

No I/O, broker, execution, provider fetch, config read, or policy mutation is
performed here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from options_manager.plans import TradePlanSnapshot
from options_manager.validation.portfolio_risk_gate import CONTRACT_MULTIPLIER

_EPSILON = 1e-6


class RiskTelemetryStatus(str, Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    INVALID = "invalid"


@dataclass(frozen=True)
class RiskTelemetrySnapshot:
    """One immutable measurement of the risk facts attached to a thesis."""

    ticker: str
    direction: str
    setup_type: str
    timeframe: str
    observed_at: str
    plan_status: str
    actionable: bool

    max_contracts: int
    planned_stop_risk_per_contract: float
    planned_total_trade_risk: float
    full_debit_per_contract: float
    full_debit_total: float

    aggregate_planned_open_risk: float
    projected_aggregate_planned_open_risk: float
    aggregate_full_debit: float
    projected_aggregate_full_debit: float
    open_position_count: int
    correlation_risk: tuple[tuple[str, float], ...]

    stated_max_dollar_risk: float
    max_trade_risk_dollars: float
    max_aggregate_open_risk_dollars: float

    entry_trigger: Optional[float]
    underlying_invalidation: Optional[float]
    distance_to_invalidation: Optional[float]
    target_1: Optional[float]
    target_2: Optional[float]
    rr_1: Optional[float]
    rr_2: Optional[float]

    expiration: str
    dte: int
    strike: float
    premium: float
    premium_stop: float
    bid: float
    ask: float
    spread_percent: float
    volume: int
    open_interest: int
    iv_event_risk: str
    theta_risk: str
    trade_style: str


@dataclass(frozen=True)
class RiskTelemetryResult:
    status: RiskTelemetryStatus
    snapshot: Optional[RiskTelemetrySnapshot]
    reason_codes: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return self.status == RiskTelemetryStatus.COMPLETE and self.snapshot is not None


def _finite(value: object, label: str, reasons: list[str]) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        reasons.append(f"{label}_not_numeric")
        return None
    if not math.isfinite(parsed):
        reasons.append(f"{label}_not_finite")
        return None
    return parsed


def _finite_optional(
    value: object,
    label: str,
    reasons: list[str],
) -> Optional[float]:
    if value is None:
        return None
    return _finite(value, label, reasons)


def _same_number(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=0.0, abs_tol=_EPSILON)


def measure_risk_telemetry(plan: TradePlanSnapshot) -> RiskTelemetryResult:
    """Reconcile a thesis into risk telemetry without choosing any policy.

    Missing contract/risk snapshots yield INCOMPLETE rather than inventing
    values. Present-but-malformed or internally contradictory facts yield
    INVALID. A COMPLETE result means only that the measurement reconciles; it
    does not approve a trade or certify that the recorded limits are optimal.
    """

    missing: list[str] = []
    if plan.contract_plan is None:
        missing.append("contract_plan_missing")
    if plan.risk_plan is None:
        missing.append("risk_plan_missing")
    if missing:
        return RiskTelemetryResult(
            status=RiskTelemetryStatus.INCOMPLETE,
            snapshot=None,
            reason_codes=tuple(missing),
        )

    contract = plan.contract_plan
    risk = plan.risk_plan
    assert contract is not None
    assert risk is not None

    reasons: list[str] = []

    premium = _finite(contract.premium, "premium", reasons)
    premium_stop = _finite(contract.premium_stop, "premium_stop", reasons)
    strike = _finite(contract.strike, "strike", reasons)
    bid = _finite(contract.bid, "bid", reasons)
    ask = _finite(contract.ask, "ask", reasons)
    spread_percent = _finite(contract.spread_percent, "spread_percent", reasons)
    planned_total = _finite(risk.planned_dollar_risk, "planned_dollar_risk", reasons)
    full_debit_total = _finite(risk.capital_deployed, "capital_deployed", reasons)
    aggregate_risk = _finite(risk.aggregate_open_risk, "aggregate_open_risk", reasons)
    projected_risk = _finite(risk.projected_open_risk, "projected_open_risk", reasons)
    aggregate_debit = _finite(
        risk.aggregate_capital_deployed, "aggregate_capital_deployed", reasons
    )
    projected_debit = _finite(
        risk.projected_capital_deployed, "projected_capital_deployed", reasons
    )
    stated_max = _finite(risk.stated_max_dollar_risk, "stated_max_dollar_risk", reasons)
    max_trade = _finite(risk.max_trade_risk_dollars, "max_trade_risk_dollars", reasons)
    max_aggregate = _finite(
        risk.max_aggregate_open_risk_dollars,
        "max_aggregate_open_risk_dollars",
        reasons,
    )
    entry = _finite_optional(plan.entry_trigger, "entry_trigger", reasons)
    invalidation = _finite_optional(
        plan.underlying_invalidation, "underlying_invalidation", reasons
    )
    target_1 = _finite_optional(plan.target_1, "target_1", reasons)
    target_2 = _finite_optional(plan.target_2, "target_2", reasons)
    rr_1 = _finite_optional(plan.rr_1, "rr_1", reasons)
    rr_2 = _finite_optional(plan.rr_2, "rr_2", reasons)

    if contract.max_contracts <= 0:
        reasons.append("max_contracts_not_positive")
    if contract.dte < 0:
        reasons.append("dte_negative")
    if contract.volume < 0:
        reasons.append("volume_negative")
    if contract.open_interest < 0:
        reasons.append("open_interest_negative")
    if risk.open_position_count < 0:
        reasons.append("open_position_count_negative")

    for label, value in (
        ("premium", premium),
        ("premium_stop", premium_stop),
        ("strike", strike),
        ("bid", bid),
        ("ask", ask),
        ("spread_percent", spread_percent),
        ("planned_dollar_risk", planned_total),
        ("capital_deployed", full_debit_total),
        ("aggregate_open_risk", aggregate_risk),
        ("projected_open_risk", projected_risk),
        ("aggregate_capital_deployed", aggregate_debit),
        ("projected_capital_deployed", projected_debit),
        ("stated_max_dollar_risk", stated_max),
        ("max_trade_risk_dollars", max_trade),
        ("max_aggregate_open_risk_dollars", max_aggregate),
    ):
        if value is not None and value < 0:
            reasons.append(f"{label}_negative")

    if premium is not None and premium <= 0:
        reasons.append("premium_not_positive")
    if premium_stop is not None and premium_stop < 0:
        reasons.append("premium_stop_negative")
    if premium is not None and premium_stop is not None and premium_stop > premium:
        reasons.append("premium_stop_above_premium")
    if strike is not None and strike <= 0:
        reasons.append("strike_not_positive")
    if bid is not None and bid <= 0:
        reasons.append("bid_not_positive")
    if bid is not None and ask is not None and ask <= bid:
        reasons.append("ask_not_above_bid")
    if max_trade is not None and max_trade <= 0:
        reasons.append("max_trade_risk_not_positive")
    if max_aggregate is not None and max_aggregate <= 0:
        reasons.append("max_aggregate_risk_not_positive")

    correlation: list[tuple[str, float]] = []
    for index, pair in enumerate(risk.correlation_risk):
        if len(pair) != 2:
            reasons.append(f"correlation_risk_{index}_malformed")
            continue
        group, raw_value = pair
        group_name = str(group).strip()
        if not group_name:
            reasons.append(f"correlation_risk_{index}_group_missing")
        value = _finite(raw_value, f"correlation_risk_{index}", reasons)
        if value is not None:
            if value < 0:
                reasons.append(f"correlation_risk_{index}_negative")
            correlation.append((group_name, value))

    required_numbers = (
        premium,
        premium_stop,
        strike,
        bid,
        ask,
        spread_percent,
        planned_total,
        full_debit_total,
        aggregate_risk,
        projected_risk,
        aggregate_debit,
        projected_debit,
        stated_max,
        max_trade,
        max_aggregate,
    )
    if any(value is None for value in required_numbers):
        return RiskTelemetryResult(
            status=RiskTelemetryStatus.INVALID,
            snapshot=None,
            reason_codes=tuple(dict.fromkeys(reasons)),
        )

    assert premium is not None
    assert premium_stop is not None
    assert strike is not None
    assert bid is not None
    assert ask is not None
    assert spread_percent is not None
    assert planned_total is not None
    assert full_debit_total is not None
    assert aggregate_risk is not None
    assert projected_risk is not None
    assert aggregate_debit is not None
    assert projected_debit is not None
    assert stated_max is not None
    assert max_trade is not None
    assert max_aggregate is not None

    planned_per_contract = (premium - premium_stop) * CONTRACT_MULTIPLIER
    expected_planned_total = planned_per_contract * contract.max_contracts
    debit_per_contract = premium * CONTRACT_MULTIPLIER
    expected_full_debit_total = debit_per_contract * contract.max_contracts

    if not _same_number(planned_total, expected_planned_total):
        reasons.append("planned_risk_contract_math_mismatch")
    if not _same_number(full_debit_total, expected_full_debit_total):
        reasons.append("full_debit_contract_math_mismatch")
    if not _same_number(projected_risk - aggregate_risk, planned_total):
        reasons.append("projected_risk_reconciliation_mismatch")
    if not _same_number(projected_debit - aggregate_debit, full_debit_total):
        reasons.append("projected_debit_reconciliation_mismatch")
    if planned_total > max_trade + _EPSILON:
        reasons.append("recorded_trade_risk_exceeds_recorded_cap")
    if projected_risk > max_aggregate + _EPSILON:
        reasons.append("recorded_projected_risk_exceeds_recorded_aggregate_cap")

    if reasons:
        return RiskTelemetryResult(
            status=RiskTelemetryStatus.INVALID,
            snapshot=None,
            reason_codes=tuple(dict.fromkeys(reasons)),
        )

    distance_to_invalidation = None
    if entry is not None and invalidation is not None:
        distance_to_invalidation = abs(entry - invalidation)

    return RiskTelemetryResult(
        status=RiskTelemetryStatus.COMPLETE,
        snapshot=RiskTelemetrySnapshot(
            ticker=plan.ticker,
            direction=plan.direction,
            setup_type=plan.setup_type,
            timeframe=plan.timeframe,
            observed_at=plan.observed_at,
            plan_status=plan.status.value,
            actionable=plan.actionable,
            max_contracts=contract.max_contracts,
            planned_stop_risk_per_contract=planned_per_contract,
            planned_total_trade_risk=planned_total,
            full_debit_per_contract=debit_per_contract,
            full_debit_total=full_debit_total,
            aggregate_planned_open_risk=aggregate_risk,
            projected_aggregate_planned_open_risk=projected_risk,
            aggregate_full_debit=aggregate_debit,
            projected_aggregate_full_debit=projected_debit,
            open_position_count=risk.open_position_count,
            correlation_risk=tuple(correlation),
            stated_max_dollar_risk=stated_max,
            max_trade_risk_dollars=max_trade,
            max_aggregate_open_risk_dollars=max_aggregate,
            entry_trigger=entry,
            underlying_invalidation=invalidation,
            distance_to_invalidation=distance_to_invalidation,
            target_1=target_1,
            target_2=target_2,
            rr_1=rr_1,
            rr_2=rr_2,
            expiration=contract.expiration,
            dte=contract.dte,
            strike=strike,
            premium=premium,
            premium_stop=premium_stop,
            bid=bid,
            ask=ask,
            spread_percent=spread_percent,
            volume=contract.volume,
            open_interest=contract.open_interest,
            iv_event_risk=contract.iv_event_risk,
            theta_risk=contract.theta_risk,
            trade_style=contract.trade_style,
        ),
    )
