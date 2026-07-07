"""Phase 9 — inert broker boundary schema.

Pure, deterministic conversion of a Phase 7 PreparedOrderTicket into a local
OptionsBrokerPreviewRequest, and independent re-validation of that request
into an OptionsBrokerPreviewResult. No broker calls, no real preview, no
order placement, no HTTP, no Discord, no file writes, no storage of any
kind — this module performs no I/O of any kind. It only reads a ticket (or
a request) and a config object, and returns a result.

This module does NOT place orders, preview orders with a broker, or execute
anything. There is no broker-specific schema here, no credentials, no
account number, no routing destination — only options_manager's own local
preview-request/preview-result shapes.

Per the Phase 8 audit decision, this module does NOT blindly trust Phase
7's own gates: `validate_preview_boundary` independently re-checks
executable, dry_run_only, order_action, quantity, limit_price,
estimated_notional, and account_tag against its own config caps.

`OptionsBrokerPreviewResult.submitted` is always False here, even when
`broker_boundary_allow_real_preview` is True — that flag is only a
future-phase design placeholder; flipping it never causes this module to
call anything. `broker` and `broker_order_id` are always None — no broker
has ever seen this request.

No live-options lock bypass exists here because no order path exists in
this phase — there is nothing for a lock to gate. This module never reads
or mutates LIVE_OPTIONS_TRADING_ENABLED, and never imports live_lock.

Independent of risk/risk_engine.py (futures) and risk/options_risk_engine.py
(reference only, not imported) — this is options_manager's own boundary
schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Optional

from .config import OptionsManagerConfig
from .order_ticket import PreparedOrderTicket

ALLOWED_ORDER_ACTION = "BUY_TO_OPEN"

REAL_PREVIEW_BLOCKED_WARNING = "real_preview_requested_but_blocked_in_phase_9"


@dataclass(kw_only=True)
class OptionsBrokerPreviewRequest:
    ticket_id: str
    confirmation_id: str
    ticker: str
    direction: Literal["CALL", "PUT"]
    order_action: Literal["BUY_TO_OPEN"]
    quantity: int
    contract_strike: float
    contract_expiry: date
    limit_price: float
    estimated_notional: float
    account_tag: str
    source: str
    dry_run_only: bool
    executable: bool


@dataclass
class OptionsBrokerPreviewResult:
    preview_ready: bool
    status: Literal["PREVIEW_READY", "REJECTED", "DATA_BLOCKED"]
    failed_stage: Optional[str] = None
    reason: str = ""
    ticket_id: Optional[str] = None
    broker: Optional[str] = None
    broker_order_id: Optional[str] = None
    executable: bool = False
    submitted: bool = False
    warnings: list[str] = field(default_factory=list)


def build_preview_request(
    ticket: PreparedOrderTicket, config: OptionsManagerConfig
) -> OptionsBrokerPreviewRequest:
    """Pure field mapping from a PreparedOrderTicket -> OptionsBrokerPreviewRequest.

    config is required and must be passed explicitly by the caller, even
    though this function does not read from it today — every function in
    this module takes an explicit config so no function silently becomes
    less pure later. Refuses (raises ValueError) if the ticket does not
    already satisfy the non-executable/dry-run-only/no-broker invariants —
    this is a programming-error guard, not a reviewable REJECTED state; a
    caller should never hand this function a ticket that violates them.
    """
    if ticket.executable is not False:
        raise ValueError(
            "build_preview_request requires ticket.executable is False; "
            f"got {ticket.executable!r}"
        )
    if ticket.dry_run_only is not True:
        raise ValueError(
            "build_preview_request requires ticket.dry_run_only is True; "
            f"got {ticket.dry_run_only!r}"
        )
    if ticket.broker is not None:
        raise ValueError(
            "build_preview_request requires ticket.broker is None; "
            f"got {ticket.broker!r}"
        )
    if ticket.broker_order_id is not None:
        raise ValueError(
            "build_preview_request requires ticket.broker_order_id is None; "
            f"got {ticket.broker_order_id!r}"
        )

    return OptionsBrokerPreviewRequest(
        ticket_id=ticket.ticket_id,
        confirmation_id=ticket.confirmation_id,
        ticker=ticket.ticker,
        direction=ticket.direction,
        order_action=ticket.order_action,
        quantity=ticket.quantity,
        contract_strike=ticket.contract_strike,
        contract_expiry=ticket.contract_expiry,
        limit_price=ticket.limit_price,
        estimated_notional=ticket.estimated_notional,
        account_tag=ticket.account_tag,
        source=ticket.source,
        dry_run_only=ticket.dry_run_only,
        executable=ticket.executable,
    )


def _preview_ready(ticket_id: str, warnings: list[str]) -> OptionsBrokerPreviewResult:
    return OptionsBrokerPreviewResult(
        preview_ready=True,
        status="PREVIEW_READY",
        failed_stage=None,
        reason="",
        ticket_id=ticket_id,
        broker=None,
        broker_order_id=None,
        executable=False,
        submitted=False,
        warnings=warnings,
    )


def _rejected(failed_stage: str, reason: str) -> OptionsBrokerPreviewResult:
    return OptionsBrokerPreviewResult(
        preview_ready=False,
        status="REJECTED",
        failed_stage=failed_stage,
        reason=reason,
        ticket_id=None,
        broker=None,
        broker_order_id=None,
        executable=False,
        submitted=False,
    )


def _data_blocked(failed_stage: str, reason: str) -> OptionsBrokerPreviewResult:
    return OptionsBrokerPreviewResult(
        preview_ready=False,
        status="DATA_BLOCKED",
        failed_stage=failed_stage,
        reason=reason,
        ticket_id=None,
        broker=None,
        broker_order_id=None,
        executable=False,
        submitted=False,
    )


def validate_preview_boundary(
    preview_request: OptionsBrokerPreviewRequest, config: OptionsManagerConfig
) -> OptionsBrokerPreviewResult:
    """Pure function of (preview_request, config) -> OptionsBrokerPreviewResult.

    config is required and must be passed explicitly by the caller — this
    function itself must never read env vars, .env files, or any other
    external mutable state, or it stops being deterministic. It never calls
    a broker, never places or previews a real order, and never writes,
    stores, or looks up anything — including preview_request itself, which
    it only reads.

    Per the Phase 8 audit decision, this does NOT trust that preview_request
    already passed Phase 7's gates — every safety-relevant field is
    independently re-checked against this module's own config caps.
    """
    cfg = config
    warnings: list[str] = []

    # 0. Subsystem-level kill switch for broker boundary validation. Checked
    # before inspecting any downstream field, malformed or not.
    if not cfg.broker_boundary_enabled:
        return _rejected(
            "broker_boundary_disabled",
            "broker_boundary_enabled is False; no preview result created",
        )

    # 1. ticket_id required.
    ticket_id = preview_request.ticket_id
    if not ticket_id or not ticket_id.strip():
        return _data_blocked("ticket_id", "preview_request.ticket_id is missing or empty")

    # 2. confirmation_id required.
    confirmation_id = preview_request.confirmation_id
    if not confirmation_id or not confirmation_id.strip():
        return _data_blocked(
            "confirmation_id", "preview_request.confirmation_id is missing or empty"
        )

    # 3. executable must be False — unconditional, no config bypass.
    if preview_request.executable is not False:
        return _rejected(
            "executable", "preview_request.executable is not False; refusing to preview"
        )

    # 4. dry_run_only must be True — unconditional, no config bypass.
    if preview_request.dry_run_only is not True:
        return _rejected(
            "dry_run_only", "preview_request.dry_run_only is not True; refusing to preview"
        )

    # 5. Order action — Phase 9 only supports BUY_TO_OPEN.
    if preview_request.order_action != ALLOWED_ORDER_ACTION:
        return _rejected(
            "order_action",
            f"preview_request.order_action {preview_request.order_action!r} is invalid "
            f"(must be {ALLOWED_ORDER_ACTION!r})",
        )

    # 6. Quantity — independent safety cap, not trusting Phase 7's own cap.
    quantity = preview_request.quantity
    if quantity < 1:
        return _rejected("quantity", f"quantity {quantity} must be at least 1")
    if quantity > cfg.broker_boundary_max_contracts:
        return _rejected(
            "quantity",
            f"quantity {quantity} exceeds broker_boundary_max_contracts "
            f"{cfg.broker_boundary_max_contracts}",
        )

    # 7. Limit price — independent safety cap.
    limit_price = preview_request.limit_price
    if limit_price <= 0:
        return _rejected("limit_price", f"limit_price {limit_price} must be > 0")
    if limit_price > cfg.broker_boundary_max_limit_price:
        return _rejected(
            "limit_price",
            f"limit_price {limit_price} exceeds broker_boundary_max_limit_price "
            f"{cfg.broker_boundary_max_limit_price}",
        )

    # 8. Estimated notional — independent safety cap.
    estimated_notional = preview_request.estimated_notional
    if estimated_notional <= 0:
        return _rejected(
            "notional", f"estimated_notional {estimated_notional} must be > 0"
        )
    if estimated_notional > cfg.broker_boundary_max_notional:
        return _rejected(
            "notional",
            f"estimated_notional {estimated_notional} exceeds broker_boundary_max_notional "
            f"{cfg.broker_boundary_max_notional}",
        )

    # 9. Account tag — independent safety cap, no broker account metadata.
    account_tag = preview_request.account_tag
    if account_tag not in cfg.broker_boundary_allowed_account_tags:
        return _rejected(
            "account_tag",
            f"account_tag '{account_tag}' not in allowed tags "
            f"{cfg.broker_boundary_allowed_account_tags}",
        )

    # 10. broker_boundary_allow_real_preview is a future-phase design
    # placeholder only — flipping it never causes a real call here.
    if cfg.broker_boundary_allow_real_preview:
        warnings.append(REAL_PREVIEW_BLOCKED_WARNING)

    result = _preview_ready(ticket_id, warnings)

    # Defensive re-check: this module must never return a result that isn't
    # non-executable/non-submitted/broker-free, even though the literals
    # above guarantee it today — a boundary validator must not approve on
    # top of a broken invariant.
    if result.executable is not False:
        return _rejected("executable", "result.executable is not False; refusing to preview")
    if result.submitted is not False:
        return _rejected("submitted", "result.submitted is not False; refusing to preview")
    if result.broker is not None:
        return _rejected("broker", "result.broker is not None; refusing to preview")
    if result.broker_order_id is not None:
        return _rejected(
            "broker_order_id", "result.broker_order_id is not None; refusing to preview"
        )

    return result
