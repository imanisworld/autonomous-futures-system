"""options_manager/contracts/base.py

Advisory-only options contract-constraints model — Increment 4. Shared,
fail-closed contract for the contract validator (contract_validator.py).
Every input here is caller-supplied; this module never fetches option
chains, never selects a contract, never reads config, never holds
credentials, and performs no I/O of any kind.

This is a separate, additive model from options_manager/strategies/base.py's
placeholder StrategyContractConstraints (Increment 1) — it is not wired
into strat_212.py. Nothing here imports options_manager's own risk_gate,
contract_quality, dry_run_review, human_confirm, order_ticket,
broker_boundary, mock_broker_preview, storage, http_api, or app modules,
the strategies, levels, or context packages, alert_ranker,
options_companion (credentialed, different trust boundary), execution,
webhook, or risk/risk_engine.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

ContractConstraintsStatus = Literal["VALID", "CAUTION", "INVALID"]

RiskLevel = Literal["NONE", "LOW", "HIGH"]


@dataclass(frozen=True)
class ContractConstraintsInputs:
    """Explicit, caller-supplied contract data and risk limits only. This
    module never fetches an option chain or selects a contract — every
    field here is whatever the caller already has in hand. Every field
    other than `direction`/`ticker` defaults to None (not yet resolved)
    and must fail closed to INVALID, exactly like omitting it entirely —
    a contract validator must never assume favorable liquidity/pricing
    by default. `max_theta_abs` is the one threshold that is genuinely
    optional: the theta-too-high check is only enforced when it is
    explicitly supplied (mirrors the min_distance_to_target /
    min_distance_to_gamma_level convention used elsewhere in this
    buildout)."""

    direction: Literal["CALL", "PUT"]
    ticker: Optional[str]
    expiration: Optional[str] = None
    dte: Optional[int] = None
    strike: Optional[float] = None
    premium: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    spread_percent: Optional[float] = None
    volume: Optional[int] = None
    open_interest: Optional[int] = None
    delta: Optional[float] = None
    theta: Optional[float] = None
    iv: Optional[float] = None
    max_premium: Optional[float] = None
    max_spread_percent: Optional[float] = None
    min_volume: Optional[int] = None
    min_open_interest: Optional[int] = None
    min_dte: Optional[int] = None
    max_theta_abs: Optional[float] = None
    earnings_risk: Optional[RiskLevel] = None
    event_risk: Optional[RiskLevel] = None


@dataclass(kw_only=True)
class ContractConstraintsResult:
    """Advisory-only output. Never a broker call, never an order, never
    an execution side effect — a pure description of whether a
    caller-supplied contract's own data (liquidity, spread, greeks, DTE,
    risk flags) meets the caller-supplied risk limits, for a human or a
    downstream advisory pipeline to independently re-evaluate."""

    status: ContractConstraintsStatus
    confirmed: bool
    reason_code: str
    reason: str = ""
    warnings: list[str] = field(default_factory=list)
    contract_score: Optional[float] = None


def _invalid(reason_code: str, reason: str) -> ContractConstraintsResult:
    return ContractConstraintsResult(
        status="INVALID", confirmed=False, reason_code=reason_code, reason=reason
    )
