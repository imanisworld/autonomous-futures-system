"""Webull SANDBOX options round-trip close plumbing.

Unwired, sandbox/paper-only support for closing a position that this repository
already proved was opened by a filled sandbox BUY_TO_OPEN ticket.

The Webull Trading API added the option position_intent field with
SELL_TO_CLOSE support. This module uses that explicit intent and refuses to
send a generic SELL that could be interpreted as opening a short option.

Nothing imports this from scanner/runtime code. It is for controlled sandbox
lifecycle proof only until a separate mirror/reconciliation lane is approved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import math
import os
from typing import Any, Callable, Literal, Mapping

from integrations.webull_paper_config import WEBULL_SANDBOX_HOST
from options_manager.config import OptionsManagerConfig
from options_manager.adapters.webull_sandbox import WEBULL_SANDBOX_BROKER
from options_manager.adapters.webull_sandbox_paper_orders import (
    WebullSandboxOrderDetail,
    _OfficialPaperOrderClient,
    _broker_order_id,
    _broker_reject,
    _json,
    _resolve_account,
    _status,
    _submit_block_reason,
)

_CloseClientFactory = Callable[[str, str], Any]

CloseStatus = Literal["SUBMITTED", "BLOCKED", "REJECTED", "ERROR"]
SELL_TO_CLOSE = "SELL_TO_CLOSE"


@dataclass(frozen=True)
class WebullSandboxCloseRequest:
    entry_ticket_id: str
    exit_ticket_id: str
    ticker: str
    direction: Literal["CALL", "PUT"]
    quantity: int
    contract_strike: float
    contract_expiry: date
    limit_price: float


@dataclass(frozen=True)
class WebullSandboxCloseResult:
    status: CloseStatus
    submitted: bool = False
    broker: str = WEBULL_SANDBOX_BROKER
    endpoint: str = WEBULL_SANDBOX_HOST
    mode: str = "paper"
    entry_ticket_id: str | None = None
    exit_ticket_id: str | None = None
    client_order_id: str | None = None
    broker_order_id: str | None = None
    reason: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _credentials(env: Mapping[str, str]) -> tuple[str, str]:
    return (
        str(env.get("WEBULL_SANDBOX_APP_KEY") or ""),
        str(env.get("WEBULL_SANDBOX_APP_SECRET") or ""),
    )


def validate_close_request(
    request: WebullSandboxCloseRequest,
    entry_detail: WebullSandboxOrderDetail,
    options_config: OptionsManagerConfig,
) -> str | None:
    """Return a fail-closed reason, or None when the close request is eligible."""

    if not request.entry_ticket_id.strip():
        return "entry_ticket_id_missing"
    if not request.exit_ticket_id.strip():
        return "exit_ticket_id_missing"
    if request.entry_ticket_id == request.exit_ticket_id:
        return "exit_ticket_id_must_differ_from_entry"
    if len(request.exit_ticket_id) > 32:
        return "exit_ticket_id_too_long"
    if not request.ticker.strip():
        return "ticker_missing"
    if request.direction not in {"CALL", "PUT"}:
        return "option_direction_invalid"
    if request.quantity < 1:
        return "quantity_invalid"
    if request.quantity > options_config.broker_boundary_max_contracts:
        return "quantity_exceeds_boundary_cap"
    if not math.isfinite(float(request.contract_strike)) or request.contract_strike <= 0:
        return "contract_strike_invalid"
    if not math.isfinite(float(request.limit_price)) or request.limit_price <= 0:
        return "limit_price_invalid"

    # A close may legitimately be worth more than the entry-side $3/$300 cap,
    # so do not use entry notional/limit caps to trap a profitable position.
    if entry_detail.status != "OK":
        return "entry_order_detail_not_ok"
    if entry_detail.client_order_id != request.entry_ticket_id:
        return "entry_ticket_detail_mismatch"
    if entry_detail.order_state != "FILLED" or not entry_detail.terminal:
        return "entry_order_not_filled"
    if entry_detail.filled_quantity is None:
        return "entry_filled_quantity_missing"
    if entry_detail.filled_quantity < request.quantity:
        return "close_quantity_exceeds_filled_quantity"
    return None


def build_sell_to_close_orders(
    request: WebullSandboxCloseRequest,
) -> list[dict[str, Any]]:
    """Build one explicit single-leg SELL_TO_CLOSE DAY limit order."""

    return [
        {
            "client_order_id": request.exit_ticket_id,
            "combo_type": "NORMAL",
            "order_type": "LIMIT",
            "quantity": str(request.quantity),
            "limit_price": f"{request.limit_price:.4f}",
            "option_strategy": "SINGLE",
            "side": "SELL",
            "position_intent": SELL_TO_CLOSE,
            "time_in_force": "DAY",
            "entrust_type": "QTY",
            "instrument_type": "OPTION",
            "market": "US",
            "symbol": request.ticker.strip().upper(),
            "legs": [
                {
                    "side": "SELL",
                    "quantity": str(request.quantity),
                    "symbol": request.ticker.strip().upper(),
                    "strike_price": f"{request.contract_strike:g}",
                    "option_expire_date": request.contract_expiry.isoformat(),
                    "instrument_type": "OPTION",
                    "option_type": request.direction,
                    "market": "US",
                }
            ],
        }
    ]


def close_sandbox_paper_option_position(
    request: WebullSandboxCloseRequest,
    entry_detail: WebullSandboxOrderDetail,
    options_config: OptionsManagerConfig,
    env: Mapping[str, str] | None = None,
    *,
    client_factory: _CloseClientFactory | None = None,
) -> WebullSandboxCloseResult:
    """Broker-preview then place one explicit SELL_TO_CLOSE sandbox order."""

    source = os.environ if env is None else env
    config_reason = _submit_block_reason(source, options_config)
    if config_reason:
        return WebullSandboxCloseResult(
            status="BLOCKED",
            entry_ticket_id=request.entry_ticket_id or None,
            exit_ticket_id=request.exit_ticket_id or None,
            reason=config_reason,
        )

    reason = validate_close_request(request, entry_detail, options_config)
    if reason:
        return WebullSandboxCloseResult(
            status="REJECTED",
            entry_ticket_id=request.entry_ticket_id or None,
            exit_ticket_id=request.exit_ticket_id or None,
            reason=reason,
        )

    key, secret = _credentials(source)
    factory = client_factory or _OfficialPaperOrderClient
    try:
        client = factory(key, secret)
    except ImportError:
        return WebullSandboxCloseResult(
            status="BLOCKED",
            entry_ticket_id=request.entry_ticket_id,
            exit_ticket_id=request.exit_ticket_id,
            reason="sdk_unavailable",
        )
    except Exception as exc:
        return WebullSandboxCloseResult(
            status="ERROR",
            entry_ticket_id=request.entry_ticket_id,
            exit_ticket_id=request.exit_ticket_id,
            reason=f"client_init_failed:{type(exc).__name__}",
        )

    account_id, select_reason = _resolve_account(client)
    if select_reason or not account_id:
        return WebullSandboxCloseResult(
            status="ERROR",
            entry_ticket_id=request.entry_ticket_id,
            exit_ticket_id=request.exit_ticket_id,
            reason=select_reason,
        )

    orders = build_sell_to_close_orders(request)

    try:
        preview = client.preview_option(account_id, orders)
    except Exception as exc:
        mapped = _broker_reject(exc)
        if mapped:
            return WebullSandboxCloseResult(
                status="REJECTED" if mapped[0] == "REJECTED" else "ERROR",
                entry_ticket_id=request.entry_ticket_id,
                exit_ticket_id=request.exit_ticket_id,
                client_order_id=request.exit_ticket_id,
                reason=f"preview_{mapped[1]}",
            )
        return WebullSandboxCloseResult(
            status="ERROR",
            entry_ticket_id=request.entry_ticket_id,
            exit_ticket_id=request.exit_ticket_id,
            client_order_id=request.exit_ticket_id,
            reason=f"preview_request_failed:{type(exc).__name__}",
        )
    if _status(preview) != 200:
        return WebullSandboxCloseResult(
            status="REJECTED",
            entry_ticket_id=request.entry_ticket_id,
            exit_ticket_id=request.exit_ticket_id,
            client_order_id=request.exit_ticket_id,
            reason=f"preview_http_{_status(preview)}",
        )
    _, err = _json(preview)
    if err:
        return WebullSandboxCloseResult(
            status="ERROR",
            entry_ticket_id=request.entry_ticket_id,
            exit_ticket_id=request.exit_ticket_id,
            client_order_id=request.exit_ticket_id,
            reason=f"preview_{err}",
        )

    try:
        placed = client.place_option(account_id, orders)
    except Exception as exc:
        mapped = _broker_reject(exc)
        if mapped and mapped[0] == "REJECTED":
            return WebullSandboxCloseResult(
                status="REJECTED",
                entry_ticket_id=request.entry_ticket_id,
                exit_ticket_id=request.exit_ticket_id,
                client_order_id=request.exit_ticket_id,
                reason=f"place_{mapped[1]}",
            )
        return WebullSandboxCloseResult(
            status="ERROR",
            entry_ticket_id=request.entry_ticket_id,
            exit_ticket_id=request.exit_ticket_id,
            client_order_id=request.exit_ticket_id,
            reason=f"place_request_failed:{type(exc).__name__}",
            warnings=("close_outcome_unknown_check_order_detail",),
        )

    if _status(placed) != 200:
        return WebullSandboxCloseResult(
            status="REJECTED",
            entry_ticket_id=request.entry_ticket_id,
            exit_ticket_id=request.exit_ticket_id,
            client_order_id=request.exit_ticket_id,
            reason=f"place_http_{_status(placed)}",
        )
    payload, err = _json(placed)
    if err:
        return WebullSandboxCloseResult(
            status="ERROR",
            entry_ticket_id=request.entry_ticket_id,
            exit_ticket_id=request.exit_ticket_id,
            client_order_id=request.exit_ticket_id,
            reason=f"place_{err}",
            warnings=("close_outcome_unknown_check_order_detail",),
        )

    return WebullSandboxCloseResult(
        status="SUBMITTED",
        submitted=True,
        entry_ticket_id=request.entry_ticket_id,
        exit_ticket_id=request.exit_ticket_id,
        client_order_id=request.exit_ticket_id,
        broker_order_id=_broker_order_id(payload),
        warnings=("sandbox_paper_only", "sell_to_close_only"),
    )


__all__ = [
    "SELL_TO_CLOSE",
    "WebullSandboxCloseRequest",
    "WebullSandboxCloseResult",
    "build_sell_to_close_orders",
    "close_sandbox_paper_option_position",
    "validate_close_request",
]
