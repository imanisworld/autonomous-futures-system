"""Phase 5 — dry-run order review.

Pure, deterministic construction of a local order review object from an
already-approved packet, its Phase 2 risk gate result, Phase 3 contract
quality gate result, and Phase 4 paper simulation result. No broker calls, no
order calls, no HTTP, no Discord, no file writes — this module performs no
I/O of any kind. It only reads a packet, three upstream results, a supplied
snapshot, and a config object, and returns a result.

This module does NOT place orders, preview orders with a broker, or execute
anything. It only builds a local `OptionOrderIntent` review object — an
internal schema, not a broker-specific preview. Real order preview/placement
and any live/micro-live behavior are later phases.

Independent of risk/risk_engine.py (futures) and risk/options_risk_engine.py
(reference only, not imported) — this is options_manager's own reviewer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal, Optional

from .config import OptionsManagerConfig
from .contract_quality import ContractMarketSnapshot, ContractQualityResult
from .models import OptionTradePacket
from .paper_sim import PaperSimResult
from .risk_gate import RiskGateResult

ALLOWED_ORDER_ACTION = "BUY_TO_OPEN"


@dataclass(kw_only=True)
class OptionOrderIntent:
    ticker: str
    direction: Literal["CALL", "PUT"]
    order_action: Literal["BUY_TO_OPEN"]
    quantity: int
    contract_strike: float
    contract_expiry: date
    max_premium: float
    estimated_limit_price: float
    estimated_notional: float
    account_tag: str
    source: str
    dry_run_only: bool
    created_at: datetime


@dataclass
class DryRunReviewResult:
    approved_for_review: bool
    status: Literal["REVIEW_READY", "REJECTED", "DATA_BLOCKED"]
    failed_stage: Optional[str] = None
    reason: str = ""
    order_intent: Optional[OptionOrderIntent] = None
    estimated_notional: Optional[float] = None
    warnings: list[str] = field(default_factory=list)


def _review_ready(order_intent: OptionOrderIntent, warnings: list[str]) -> DryRunReviewResult:
    return DryRunReviewResult(
        approved_for_review=True,
        status="REVIEW_READY",
        failed_stage=None,
        reason="",
        order_intent=order_intent,
        estimated_notional=order_intent.estimated_notional,
        warnings=warnings,
    )


def _rejected(failed_stage: str, reason: str) -> DryRunReviewResult:
    return DryRunReviewResult(
        approved_for_review=False,
        status="REJECTED",
        failed_stage=failed_stage,
        reason=reason,
    )


def _data_blocked(failed_stage: str, reason: str) -> DryRunReviewResult:
    return DryRunReviewResult(
        approved_for_review=False,
        status="DATA_BLOCKED",
        failed_stage=failed_stage,
        reason=reason,
    )


def build_dry_run_review(
    packet: OptionTradePacket,
    risk_result: RiskGateResult,
    quality_result: ContractQualityResult,
    paper_sim_result: PaperSimResult,
    snapshot: ContractMarketSnapshot,
    config: OptionsManagerConfig,
) -> DryRunReviewResult:
    """Pure function of (packet, gate/sim results, snapshot, config) -> DryRunReviewResult.

    config is required and must be passed explicitly by the caller — this
    function itself must never read env vars, .env files, or any other
    external mutable state, or it stops being deterministic. It never calls a
    broker, never previews or places a real order, and never writes a
    journal entry; it only builds a local review object from data the caller
    already supplied.
    """
    cfg = config
    warnings: list[str] = []

    # 0. Subsystem-level kill switch for dry-run review generation.
    if not cfg.dry_run_enabled:
        return _rejected(
            "dry_run_disabled", "dry_run_enabled is False; no order intent created"
        )

    # 1. Only PENDING packets may be reviewed — same defensive re-check
    # pattern used by risk_gate.py, contract_quality.py, and paper_sim.py.
    if packet.status != "PENDING":
        return _rejected(
            "packet_status",
            f"packet status is '{packet.status}' (must be PENDING); "
            f"original rejection_reason={packet.rejection_reason!r}",
        )

    # 2. Risk gate precondition — unconditional.
    if risk_result.status == "REJECTED":
        return _rejected(
            "risk_gate",
            f"risk_gate rejected: failed_rule={risk_result.failed_rule!r}, "
            f"reason={risk_result.reason!r}",
        )
    if risk_result.status == "DATA_BLOCKED":
        return _data_blocked(
            "risk_gate",
            f"risk_gate data_blocked: failed_rule={risk_result.failed_rule!r}, "
            f"reason={risk_result.reason!r}",
        )

    # 3. Contract quality gate precondition — unconditional.
    if quality_result.status == "REJECTED":
        return _rejected(
            "contract_quality",
            f"contract_quality rejected: failed_rule={quality_result.failed_rule!r}, "
            f"reason={quality_result.reason!r}",
        )
    if quality_result.status == "DATA_BLOCKED":
        return _data_blocked(
            "contract_quality",
            f"contract_quality data_blocked: failed_rule={quality_result.failed_rule!r}, "
            f"reason={quality_result.reason!r}",
        )

    # 4. Paper simulation precondition — skippable via config.
    if cfg.dry_run_require_paper_simulated:
        if paper_sim_result.status == "REJECTED":
            return _rejected(
                "paper_sim",
                f"paper_sim rejected: failed_stage={paper_sim_result.failed_stage!r}, "
                f"reason={paper_sim_result.reason!r}",
            )
        if paper_sim_result.status == "DATA_BLOCKED":
            return _data_blocked(
                "paper_sim",
                f"paper_sim data_blocked: failed_stage={paper_sim_result.failed_stage!r}, "
                f"reason={paper_sim_result.reason!r}",
            )

    # 5. Snapshot must have an ask to build a review.
    if snapshot.ask is None:
        return _data_blocked("snapshot", "snapshot.ask is missing")

    # 6. Quantity.
    quantity = packet.max_contracts
    if quantity < 1:
        return _rejected(
            "quantity", f"quantity {quantity} must be at least 1"
        )
    if quantity > cfg.dry_run_max_contracts:
        return _rejected(
            "quantity",
            f"quantity {quantity} exceeds dry_run_max_contracts {cfg.dry_run_max_contracts}",
        )

    # 7. Order action — Phase 5 only supports BUY_TO_OPEN.
    if cfg.dry_run_order_action != ALLOWED_ORDER_ACTION:
        return _rejected(
            "order_action",
            f"dry_run_order_action {cfg.dry_run_order_action!r} is invalid "
            f"(must be {ALLOWED_ORDER_ACTION!r})",
        )

    # 8. Limit price.
    estimated_limit_price = snapshot.ask
    if estimated_limit_price <= 0:
        return _rejected(
            "limit_price", f"estimated_limit_price {estimated_limit_price} must be > 0"
        )
    if estimated_limit_price > packet.max_premium:
        return _rejected(
            "limit_price",
            f"estimated_limit_price {estimated_limit_price} exceeds packet "
            f"max_premium {packet.max_premium}",
        )

    # 9. Estimated notional.
    estimated_notional = estimated_limit_price * 100 * quantity
    if estimated_notional > cfg.dry_run_max_notional:
        return _rejected(
            "notional",
            f"estimated_notional {estimated_notional} exceeds dry_run_max_notional "
            f"{cfg.dry_run_max_notional}",
        )

    # 10. Account tag.
    if packet.account_tag not in cfg.dry_run_allowed_account_tags:
        return _rejected(
            "account_tag",
            f"account_tag '{packet.account_tag}' not in allowed tags "
            f"{cfg.dry_run_allowed_account_tags}",
        )

    # 11. Build the order intent. dry_run_only is always True in Phase 5.
    order_intent = OptionOrderIntent(
        ticker=packet.ticker,
        direction=packet.direction,
        order_action=ALLOWED_ORDER_ACTION,
        quantity=quantity,
        contract_strike=packet.contract_strike,
        contract_expiry=packet.contract_expiry,
        max_premium=packet.max_premium,
        estimated_limit_price=estimated_limit_price,
        estimated_notional=estimated_notional,
        account_tag=packet.account_tag,
        source=packet.source,
        dry_run_only=True,
        created_at=packet.created_at,
    )
    # Defensive re-check: this module must never return a review object that
    # isn't marked dry-run-only, even though the literal above guarantees it
    # today — a quality gate must not approve on top of a broken invariant.
    if not order_intent.dry_run_only:
        return _rejected(
            "dry_run_only", "order_intent.dry_run_only is False; refusing to review"
        )

    return _review_ready(order_intent, warnings)
