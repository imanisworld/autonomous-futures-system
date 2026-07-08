"""options_manager/levels/base.py

Advisory-only level/target model — Increment 2. Shared, fail-closed
contract for the target finder (target_finder.py now; future level-aware
modules can extend this). No broker calls, no execution, no order
placement, no side effects of any kind — these are pure functions of
their inputs.

Nothing here imports alert_ranker, options_companion, execution, webhook,
broker systems, or risk/risk_engine.py. Nothing here imports any existing
options_manager pipeline module (risk_gate, contract_quality,
dry_run_review, human_confirm, order_ticket, broker_boundary,
mock_broker_preview, storage, http_api, app) or the strategies package
added in Increment 1 — this module is additive only and is not wired into
strat_212.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

TargetFinderStatus = Literal["VALID", "INVALID"]


@dataclass(frozen=True)
class LevelFinderInputs:
    """Explicit, caller-supplied inputs only — this module never fetches
    market data, never reads env vars, and never looks anything up."""

    direction: Literal["CALL", "PUT"]
    entry: Optional[float]
    underlying_invalidation: Optional[float]
    resistance_levels: tuple[float, ...] = ()
    support_levels: tuple[float, ...] = ()
    gamma_resistance: Optional[float] = None
    gamma_support: Optional[float] = None
    min_rr_threshold: Optional[float] = None
    min_distance_to_target: Optional[float] = None


@dataclass(kw_only=True)
class TargetFinderResult:
    """Advisory-only output. Never a broker call, never an order, never
    an execution side effect — a pure description of where a hypothetical
    trade's targets would sit, for a human or a downstream advisory
    pipeline to independently re-evaluate."""

    status: TargetFinderStatus
    reason_code: str
    reason: str = ""
    target_1: Optional[float] = None
    target_2: Optional[float] = None
    distance_to_target_1: Optional[float] = None
    distance_to_target_2: Optional[float] = None
    risk_amount: Optional[float] = None
    reward_1: Optional[float] = None
    reward_2: Optional[float] = None
    rr_1: Optional[float] = None
    rr_2: Optional[float] = None
    warnings: list[str] = field(default_factory=list)


def _invalid(reason_code: str, reason: str) -> TargetFinderResult:
    return TargetFinderResult(status="INVALID", reason_code=reason_code, reason=reason)
