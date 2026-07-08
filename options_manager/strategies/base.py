"""options_manager/strategies/base.py

Advisory-only strategy layer — Increment 1. Every strategy validator in
this package (strat_212 now; future strat_312, break_retest, etc.) returns
a StrategySignal built from this shared, fail-closed contract. No broker
calls, no execution, no order placement, no side effects of any kind —
these are pure functions of their inputs.

This package is the first place in this buildout that imports outside
options_manager/ — strat_212.py reuses strategy.strat_classifier for
candle/sequence classification. That module is pure, read-only, has no
I/O, no execution path, no broker path, and is already tested; nothing
under options_manager/strategies/ imports alert_ranker, options_companion,
execution, webhook, or risk/risk_engine.py.

Nothing here imports options_manager's own risk_gate, contract_quality,
dry_run_review, human_confirm, order_ticket, broker_boundary,
mock_broker_preview, storage, http_api, or app modules either — a
StrategySignal has no wiring into that pipeline yet, so it cannot bypass
any of its gates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

StrategyStatus = Literal["VALID", "WATCH", "INVALID"]


@dataclass(frozen=True)
class StrategyMarketContext:
    """Placeholder input for the future (Increment 3) market-context
    validator. `confirmed=None` means context has not yet been evaluated
    and must fail closed to INVALID, exactly like omitting context
    entirely — a strategy must never assume favorable context by
    default."""

    confirmed: Optional[bool] = None
    notes: str = ""


@dataclass(frozen=True)
class StrategyContractConstraints:
    """Placeholder input for the future (Increment 4) contract selector.
    `constraints_met=None` means contract constraints have not yet been
    evaluated and must fail closed to INVALID, exactly like omitting
    constraints entirely."""

    constraints_met: Optional[bool] = None
    notes: str = ""


@dataclass(kw_only=True)
class StrategySignal:
    """Advisory-only output of every strategy validator. Never a broker
    call, never an order, never an execution side effect — a pure
    description of a possible setup for a human or a downstream advisory
    pipeline (options_manager's existing risk_gate/contract_quality/
    dry_run_review chain) to independently re-evaluate."""

    strategy_name: str
    direction: Literal["CALL", "PUT"]
    status: StrategyStatus
    reason_code: str
    reason: str = ""
    candle_sequence: Optional[str] = None
    entry_trigger: Optional[float] = None
    underlying_invalidation: Optional[float] = None
    target_1: Optional[float] = None
    target_2: Optional[float] = None
    warnings: list[str] = field(default_factory=list)


def _invalid(
    strategy_name: str,
    direction: Literal["CALL", "PUT"],
    reason_code: str,
    reason: str,
) -> StrategySignal:
    return StrategySignal(
        strategy_name=strategy_name,
        direction=direction,
        status="INVALID",
        reason_code=reason_code,
        reason=reason,
    )


def _watch(
    strategy_name: str,
    direction: Literal["CALL", "PUT"],
    reason_code: str,
    reason: str,
    *,
    candle_sequence: Optional[str] = None,
) -> StrategySignal:
    return StrategySignal(
        strategy_name=strategy_name,
        direction=direction,
        status="WATCH",
        reason_code=reason_code,
        reason=reason,
        candle_sequence=candle_sequence,
    )
