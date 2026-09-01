"""Pure advisory portfolio-risk accounting for options_manager.

This module deliberately does not impose a position-count cap. It evaluates
planned dollar risk and reports capital deployed and caller-supplied
correlation groups separately. No I/O, market data, broker, scanner, or order
path is used here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal, Sequence

DEFAULT_MAX_TRADE_RISK_DOLLARS = 300.0
DEFAULT_MAX_AGGREGATE_OPEN_RISK_DOLLARS = 1000.0


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
    max_aggregate_open_risk_dollars: float = DEFAULT_MAX_AGGREGATE_OPEN_RISK_DOLLARS,
) -> PortfolioRiskResult:
    """Evaluate a candidate against planned risk dollars, never position count.

    `planned_dollar_risk` should come from the trade's premium-stop /
    invalidation plan. `capital_deployed` is tracked for visibility but is
    intentionally not treated as planned max loss.
    """
    blocking: list[str] = []

    if max_trade_risk_dollars <= 0:
        blocking.append("missing/invalid max_trade_risk_dollars")
    if max_aggregate_open_risk_dollars <= 0:
        blocking.append("missing/invalid max_aggregate_open_risk_dollars")

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

    if candidate.planned_dollar_risk > max_trade_risk_dollars:
        blocking.append(
            f"candidate planned risk ${candidate.planned_dollar_risk:.2f} exceeds "
            f"per-trade cap ${max_trade_risk_dollars:.2f}"
        )

    if projected_open_risk > max_aggregate_open_risk_dollars:
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
