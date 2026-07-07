"""Phase 7 — controlled order ticket preparation.

Pure, deterministic construction of a local, non-executable order ticket
from a Phase 6 HumanConfirmedOrderPrep. No broker calls, no order calls, no
HTTP, no Discord, no file writes, no storage of any kind — this module
performs no I/O of any kind. It only reads a confirmed order prep and a
config object, and returns a result.

This module does NOT place orders, preview orders, or execute anything. The
resulting PreparedOrderTicket is permanently non-executable in this phase:
`executable` is always False, `broker` and `broker_order_id` are always
None. There is no broker-specific schema here, only options_manager's own
internal ticket shape.

This module does NOT store, persist, or look up a ticket. It never marks a
confirmation used and never re-validates a previously built ticket — a
future storage/caller layer owns all of that. There is no
`validate_existing_ticket()` here by design.

`OrderTicketResult.status == "EXPIRED"` is purely a pass-through of an
already-expired HumanConfirmedOrderPrep; this module never re-derives or
re-checks expiry against a stored ticket.

No live-options lock bypass exists here because no order path exists in
this phase — there is nothing for a lock to gate. This module never reads
or mutates LIVE_OPTIONS_TRADING_ENABLED, and never imports live_lock.

Independent of risk/risk_engine.py (futures) and risk/options_risk_engine.py
(reference only, not imported) — this is options_manager's own ticket
builder.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal, Optional

from .config import OptionsManagerConfig
from .human_confirm import HumanConfirmedOrderPrep

ALLOWED_ORDER_ACTION = "BUY_TO_OPEN"


@dataclass(kw_only=True)
class PreparedOrderTicket:
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
    broker: Optional[str]
    broker_order_id: Optional[str]
    created_at: datetime
    expires_at: datetime
    warnings: list[str] = field(default_factory=list)


@dataclass
class OrderTicketResult:
    ticket_created: bool
    status: Literal["TICKET_READY", "REJECTED", "EXPIRED", "DATA_BLOCKED"]
    failed_stage: Optional[str] = None
    reason: str = ""
    ticket: Optional[PreparedOrderTicket] = None
    warnings: list[str] = field(default_factory=list)


def compute_ticket_id(confirmed_prep: HumanConfirmedOrderPrep, now: datetime) -> str:
    """Deterministic id derived from the confirmation and the intent's own
    content-defining fields, plus the caller-supplied `now`.

    A changed confirmation_id or changed order terms always produces a
    different ticket_id. Including `now` means two tickets built at
    different instants for the same confirmation don't collide, while the
    same (confirmed_prep, now) pair always reproduces the same id.
    """
    order_intent = confirmed_prep.order_intent
    raw = "|".join(
        [
            confirmed_prep.confirmation_id or "",
            order_intent.ticker,
            order_intent.direction,
            order_intent.order_action,
            str(order_intent.quantity),
            f"{order_intent.contract_strike:.4f}",
            order_intent.contract_expiry.isoformat(),
            f"{order_intent.estimated_limit_price:.4f}",
            f"{order_intent.estimated_notional:.4f}",
            order_intent.account_tag,
            order_intent.source,
            now.isoformat(),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _ticket_ready(ticket: PreparedOrderTicket) -> OrderTicketResult:
    return OrderTicketResult(
        ticket_created=True,
        status="TICKET_READY",
        failed_stage=None,
        reason="",
        ticket=ticket,
        warnings=[],
    )


def _rejected(failed_stage: str, reason: str) -> OrderTicketResult:
    return OrderTicketResult(
        ticket_created=False,
        status="REJECTED",
        failed_stage=failed_stage,
        reason=reason,
    )


def _data_blocked(failed_stage: str, reason: str) -> OrderTicketResult:
    return OrderTicketResult(
        ticket_created=False,
        status="DATA_BLOCKED",
        failed_stage=failed_stage,
        reason=reason,
    )


def _expired(reason: str) -> OrderTicketResult:
    return OrderTicketResult(
        ticket_created=False,
        status="EXPIRED",
        failed_stage="human_confirm_expired",
        reason=reason,
    )


def build_order_ticket(
    confirmed_prep: HumanConfirmedOrderPrep,
    config: OptionsManagerConfig,
    *,
    now: datetime,
) -> OrderTicketResult:
    """Pure function of (confirmed_prep, config, now) -> OrderTicketResult.

    config is required and must be passed explicitly by the caller — this
    function itself must never read env vars, .env files, or any other
    external mutable state, or it stops being deterministic. `now` is
    caller-supplied, never read from the clock internally. It never calls a
    broker, never places or previews a real order, and never writes,
    stores, or looks up anything — including confirmed_prep itself, which
    it only reads.
    """
    cfg = config

    # 0. Subsystem-level kill switch for order ticket preparation. Checked
    # before inspecting any downstream field, malformed or not.
    if not cfg.order_ticket_enabled:
        return _rejected(
            "order_ticket_disabled",
            "order_ticket_enabled is False; no order ticket created",
        )

    # 1. Human-confirmed order prep must be CONFIRMED.
    if confirmed_prep.status == "REJECTED":
        return _rejected(
            "human_confirm",
            f"human_confirm rejected: failed_stage={confirmed_prep.failed_stage!r}, "
            f"reason={confirmed_prep.reason!r}",
        )
    if confirmed_prep.status == "EXPIRED":
        return _expired(
            f"human_confirm expired: failed_stage={confirmed_prep.failed_stage!r}, "
            f"reason={confirmed_prep.reason!r}"
        )
    if confirmed_prep.status == "USED":
        return _rejected(
            "human_confirm",
            f"human_confirm already used: failed_stage={confirmed_prep.failed_stage!r}, "
            f"reason={confirmed_prep.reason!r}",
        )
    if confirmed_prep.status == "DATA_BLOCKED":
        return _data_blocked(
            "human_confirm",
            f"human_confirm data_blocked: failed_stage={confirmed_prep.failed_stage!r}, "
            f"reason={confirmed_prep.reason!r}",
        )

    # 2. Order intent required.
    order_intent = confirmed_prep.order_intent
    if order_intent is None:
        return _data_blocked("order_intent", "confirmed_prep.order_intent is missing")

    # 3. Confirmation id required.
    confirmation_id = confirmed_prep.confirmation_id
    if not confirmation_id or not confirmation_id.strip():
        return _data_blocked(
            "confirmation_id", "confirmed_prep.confirmation_id is missing or empty"
        )

    # 4. dry_run_only must still be True — defensive re-check, same pattern
    # Phase 5 and Phase 6 used on themselves.
    if order_intent.dry_run_only is not True:
        return _rejected(
            "dry_run_only", "order_intent.dry_run_only is not True; refusing to create ticket"
        )

    # 5. Order action — Phase 7 only supports BUY_TO_OPEN.
    if order_intent.order_action != ALLOWED_ORDER_ACTION:
        return _rejected(
            "order_action",
            f"order_intent.order_action {order_intent.order_action!r} is invalid "
            f"(must be {ALLOWED_ORDER_ACTION!r})",
        )

    # 6. Quantity.
    quantity = order_intent.quantity
    if quantity < 1:
        return _rejected("quantity", f"quantity {quantity} must be at least 1")
    if quantity > cfg.order_ticket_max_contracts:
        return _rejected(
            "quantity",
            f"quantity {quantity} exceeds order_ticket_max_contracts "
            f"{cfg.order_ticket_max_contracts}",
        )

    # 7. Estimated notional.
    estimated_notional = order_intent.estimated_notional
    if estimated_notional > cfg.order_ticket_max_notional:
        return _rejected(
            "notional",
            f"estimated_notional {estimated_notional} exceeds order_ticket_max_notional "
            f"{cfg.order_ticket_max_notional}",
        )

    # 8. Limit price.
    limit_price = order_intent.estimated_limit_price
    if limit_price <= 0:
        return _rejected("limit_price", f"limit_price {limit_price} must be > 0")
    if limit_price > cfg.order_ticket_max_limit_price:
        return _rejected(
            "limit_price",
            f"limit_price {limit_price} exceeds order_ticket_max_limit_price "
            f"{cfg.order_ticket_max_limit_price}",
        )

    # 9. Account tag — sourced only from confirmed_prep.order_intent, no
    # separate account field, no broker account metadata.
    account_tag = order_intent.account_tag
    if account_tag not in cfg.order_ticket_allowed_account_tags:
        return _rejected(
            "account_tag",
            f"account_tag '{account_tag}' not in allowed tags "
            f"{cfg.order_ticket_allowed_account_tags}",
        )

    # 10. `now` must be timezone-aware.
    if now.tzinfo is None:
        return _data_blocked("timestamp", "now has no timezone info")

    # 11. TTL must be positive.
    if cfg.order_ticket_ttl_seconds <= 0:
        return _rejected(
            "ttl",
            f"order_ticket_ttl_seconds {cfg.order_ticket_ttl_seconds} must be > 0",
        )

    # 12. Build the ticket. executable/broker/broker_order_id are always
    # non-executable in Phase 7.
    ticket_id = compute_ticket_id(confirmed_prep, now)
    expires_at = now + timedelta(seconds=cfg.order_ticket_ttl_seconds)
    ticket = PreparedOrderTicket(
        ticket_id=ticket_id,
        confirmation_id=confirmation_id,
        ticker=order_intent.ticker,
        direction=order_intent.direction,
        order_action=ALLOWED_ORDER_ACTION,
        quantity=quantity,
        contract_strike=order_intent.contract_strike,
        contract_expiry=order_intent.contract_expiry,
        limit_price=limit_price,
        estimated_notional=estimated_notional,
        account_tag=account_tag,
        source=order_intent.source,
        dry_run_only=True,
        executable=False,
        broker=None,
        broker_order_id=None,
        created_at=now,
        expires_at=expires_at,
        warnings=[],
    )

    # Defensive re-check: this module must never return a ticket that isn't
    # non-executable, even though the literals above guarantee it today — a
    # ticket builder must not approve on top of a broken invariant.
    if ticket.executable is not False:
        return _rejected("executable", "ticket.executable is not False; refusing to create ticket")
    if ticket.broker is not None:
        return _rejected("broker", "ticket.broker is not None; refusing to create ticket")
    if ticket.broker_order_id is not None:
        return _rejected(
            "broker_order_id", "ticket.broker_order_id is not None; refusing to create ticket"
        )
    if ticket.dry_run_only is not True:
        return _rejected(
            "dry_run_only", "ticket.dry_run_only is not True; refusing to create ticket"
        )

    return _ticket_ready(ticket)
