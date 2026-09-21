"""Webull SANDBOX paper-order lane for the options system.

This module is the only place that can submit, cancel, or read back an
options order at Webull, and it can only do so against the sandbox host in
paper mode. It is a plumbing-proof / mirror lane: the internal shadow journal
remains the evidence of record. Nothing in the running scanner imports this
module yet — wiring it is a separate, flag-gated change.

Safety is layered on top of the preview adapter's checks:
- frozen Webull config must be sandbox + paper with both live flags false;
- ``OptionsManagerConfig.live_options_trading_enabled`` must be False;
- ``broker_boundary_allow_real_preview`` AND
  ``broker_boundary_allow_sandbox_paper_submit`` must both be True;
- the existing broker-boundary validation must pass (single-leg BUY_TO_OPEN,
  quantity/notional/limit caps, allowed account tag);
- a sandbox preview must return PREVIEW_READY immediately before placement;
- exactly one sandbox INDIVIDUAL_CASH account must resolve;
- ``client_order_id`` is the caller's ticket id, so a retry can never place a
  second order for the same ticket at the broker.

Stocks, multi-leg strategies, SELL/short, market orders, replace, and the live
host are not supported and not reachable from here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import math
import os
from typing import Any, Callable, Literal, Mapping, Protocol

from integrations.webull_paper_config import WEBULL_SANDBOX_HOST
from options_manager.broker_boundary import (
    OptionsBrokerPreviewRequest,
    validate_preview_boundary,
)
from options_manager.config import OptionsManagerConfig
from options_manager.adapters.webull_sandbox import (
    WEBULL_REGION,
    WEBULL_SANDBOX_BROKER,
    WebullSandboxPreviewResult,
    _as_float,
    _config_block_reason,
    _credentials,
    _first_scalar,
    _official_api_client,
    _preview_orders,
    _select_individual_cash_account,
)

_ResponseLike = Any  # duck-typed: .status_code, .json()

OrderStatus = Literal["SUBMITTED", "BLOCKED", "REJECTED", "ERROR"]
CancelStatus = Literal["CANCEL_REQUESTED", "BLOCKED", "REJECTED", "ERROR"]
DetailStatus = Literal["OK", "BLOCKED", "NOT_FOUND", "ERROR"]

# Broker-side order states we treat as terminal for paper bookkeeping.
TERMINAL_ORDER_STATES = frozenset({"FILLED", "CANCELLED", "CANCELED", "REJECTED", "EXPIRED", "FAILED"})


class _PaperOrderClientLike(Protocol):
    def get_account_list(self) -> _ResponseLike: ...

    def preview_option(self, account_id: str, orders: list[dict[str, Any]]) -> _ResponseLike: ...

    def place_option(self, account_id: str, orders: list[dict[str, Any]]) -> _ResponseLike: ...

    def cancel_option(self, account_id: str, client_order_id: str) -> _ResponseLike: ...

    def get_order_detail(self, account_id: str, client_order_id: str) -> _ResponseLike: ...


PaperOrderClientFactory = Callable[[str, str], _PaperOrderClientLike]


@dataclass(frozen=True)
class WebullSandboxOrderResult:
    status: OrderStatus
    submitted: bool = False
    broker: str = WEBULL_SANDBOX_BROKER
    endpoint: str = WEBULL_SANDBOX_HOST
    mode: str = "paper"
    ticket_id: str | None = None
    client_order_id: str | None = None
    broker_order_id: str | None = None
    estimated_cost: float | None = None
    estimated_transaction_fee: float | None = None
    reason: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class WebullSandboxCancelResult:
    status: CancelStatus
    ticket_id: str | None = None
    client_order_id: str | None = None
    broker_order_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class WebullSandboxOrderDetail:
    status: DetailStatus
    ticket_id: str | None = None
    client_order_id: str | None = None
    broker_order_id: str | None = None
    order_state: str | None = None
    terminal: bool = False
    filled_quantity: int | None = None
    average_fill_price: float | None = None
    limit_price: float | None = None
    reason: str | None = None


def _submit_block_reason(
    env: Mapping[str, str], options_config: OptionsManagerConfig
) -> str | None:
    reason = _config_block_reason(env, options_config, require_real_preview=True)
    if reason:
        return reason
    if not options_config.broker_boundary_allow_sandbox_paper_submit:
        return "sandbox_paper_submit_not_enabled"
    return None


def _status(response: Any) -> int:
    return int(getattr(response, "status_code", 0) or 0)


def _json(response: Any) -> tuple[Any, str | None]:
    try:
        return response.json(), None
    except Exception as exc:  # pragma: no cover - defensive
        return None, f"invalid_json:{type(exc).__name__}"


def _resolve_account(client: _PaperOrderClientLike) -> tuple[str | None, str | None]:
    try:
        response = client.get_account_list()
    except Exception as exc:
        return None, f"account_request_failed:{type(exc).__name__}"
    if _status(response) != 200:
        return None, "account_http_error"
    payload, err = _json(response)
    if err:
        return None, f"account_{err}"
    return _select_individual_cash_account(payload)


def _broker_reject(exc: Exception) -> tuple[str, str] | None:
    """Map an SDK ServerException (HTTP 4xx with a broker error code) to a
    (status, reason). Returns None for transport/unknown failures."""
    code = getattr(exc, "error_code", None)
    http = getattr(exc, "http_status", None)
    if not code:
        return None
    try:
        http_i = int(http) if http is not None else None
    except (TypeError, ValueError):
        http_i = None
    if http_i is not None and 400 <= http_i < 500:
        return "REJECTED", f"broker:{code}"
    return "ERROR", f"broker:{code}"


def _broker_order_id(payload: Any) -> str | None:
    value = _first_scalar(payload, ("order_id", "broker_order_id", "orderId"))
    return str(value) if value is not None else None


def build_sandbox_close_ticket_id(entry_ticket_id: str) -> str:
    """Deterministic <=32-char close id derived only from the entry ticket."""
    raw = (entry_ticket_id or "").strip()
    if not raw:
        raise ValueError("entry_ticket_id is required")
    return "cls-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:28]


def _close_orders(
    entry_request: OptionsBrokerPreviewRequest,
    *,
    close_ticket_id: str,
    limit_price: float,
) -> list[dict[str, Any]]:
    """Current Webull option schema for one SELL_TO_CLOSE DAY limit order."""
    return [
        {
            "client_order_id": close_ticket_id,
            "combo_type": "NORMAL",
            "order_type": "LIMIT",
            "quantity": str(entry_request.quantity),
            "limit_price": f"{limit_price:.4f}",
            "option_strategy": "SINGLE",
            "instrument_type": "OPTION",
            "market": "US",
            "symbol": entry_request.ticker,
            "side": "SELL",
            "position_intent": "SELL_TO_CLOSE",
            "time_in_force": "DAY",
            "entrust_type": "QTY",
            "legs": [
                {
                    "side": "SELL",
                    "quantity": str(entry_request.quantity),
                    "symbol": entry_request.ticker,
                    "strike_price": f"{entry_request.contract_strike:g}",
                    "option_expire_date": entry_request.contract_expiry.isoformat(),
                    "instrument_type": "OPTION",
                    "option_type": entry_request.direction,
                    "market": "US",
                }
            ],
        }
    ]


class _OfficialPaperOrderClient:
    """Thin wrapper over the official SDK, sandbox endpoint only."""

    def __init__(self, app_key: str, app_secret: str):
        from webull.trade.trade.v2.account_info_v2 import AccountV2

        self._api = _official_api_client(app_key, app_secret)
        self._accounts = AccountV2(self._api)

    def get_account_list(self) -> _ResponseLike:
        return self._accounts.get_account_list()

    def preview_option(self, account_id: str, orders: list[dict[str, Any]]) -> _ResponseLike:
        from webull.trade.request.v2.preview_option_request import PreviewOptionRequest

        request = PreviewOptionRequest()
        request.set_account_id(account_id)
        request.set_new_orders(orders)
        return self._api.get_response(request)

    def place_option(self, account_id: str, orders: list[dict[str, Any]]) -> _ResponseLike:
        from webull.trade.request.v2.place_option_request import PlaceOptionRequest

        request = PlaceOptionRequest()
        request.set_account_id(account_id)
        request.set_new_orders(orders)
        request.add_custom_headers_from_order(orders)
        return self._api.get_response(request)

    def cancel_option(self, account_id: str, client_order_id: str) -> _ResponseLike:
        from webull.trade.request.v2.cancel_option_request import CancelOptionRequest

        request = CancelOptionRequest()
        request.set_account_id(account_id)
        request.set_client_order_id(client_order_id)
        return self._api.get_response(request)

    def get_order_detail(self, account_id: str, client_order_id: str) -> _ResponseLike:
        from webull.trade.request.v2.get_order_detail_request import OrderDetailRequest

        request = OrderDetailRequest()
        request.set_account_id(account_id)
        request.set_client_order_id(client_order_id)
        return self._api.get_response(request)


def submit_sandbox_paper_option_order(
    preview_request: OptionsBrokerPreviewRequest,
    options_config: OptionsManagerConfig,
    env: Mapping[str, str] | None = None,
    *,
    client_factory: PaperOrderClientFactory | None = None,
) -> WebullSandboxOrderResult:
    """Preview then place ONE single-leg BUY_TO_OPEN limit order at the sandbox.

    ``client_order_id`` == ``preview_request.ticket_id`` (idempotent per ticket).
    """
    source = os.environ if env is None else env
    ticket = preview_request.ticket_id or None
    reason = _submit_block_reason(source, options_config)
    if reason:
        return WebullSandboxOrderResult(status="BLOCKED", ticket_id=ticket, reason=reason)

    boundary = validate_preview_boundary(preview_request, options_config)
    if not boundary.preview_ready:
        return WebullSandboxOrderResult(
            status="REJECTED",
            ticket_id=ticket,
            reason=f"broker_boundary:{boundary.failed_stage}:{boundary.reason}",
        )

    key, secret = _credentials(source)
    factory = client_factory or _OfficialPaperOrderClient
    try:
        client = factory(key, secret)
    except ImportError:
        return WebullSandboxOrderResult(status="BLOCKED", ticket_id=ticket, reason="sdk_unavailable")
    except Exception as exc:
        return WebullSandboxOrderResult(
            status="ERROR", ticket_id=ticket, reason=f"client_init_failed:{type(exc).__name__}"
        )

    account_id, select_reason = _resolve_account(client)
    if select_reason or not account_id:
        return WebullSandboxOrderResult(status="ERROR", ticket_id=ticket, reason=select_reason)

    orders = _preview_orders(preview_request)
    client_order_id = orders[0]["client_order_id"]

    # Broker preview must pass immediately before placement.
    try:
        preview = client.preview_option(account_id, orders)
    except Exception as exc:
        mapped = _broker_reject(exc)
        if mapped:
            return WebullSandboxOrderResult(
                status=mapped[0], ticket_id=ticket, client_order_id=client_order_id, reason=f"preview_{mapped[1]}",
            )
        return WebullSandboxOrderResult(
            status="ERROR", ticket_id=ticket, client_order_id=client_order_id,
            reason=f"preview_request_failed:{type(exc).__name__}",
        )
    if _status(preview) != 200:
        return WebullSandboxOrderResult(
            status="REJECTED", ticket_id=ticket, client_order_id=client_order_id,
            reason=f"preview_http_{_status(preview)}",
        )
    preview_payload, err = _json(preview)
    if err:
        return WebullSandboxOrderResult(
            status="ERROR", ticket_id=ticket, client_order_id=client_order_id, reason=f"preview_{err}"
        )
    estimated_cost = _as_float(
        _first_scalar(preview_payload, ("estimated_cost", "estimated_notional", "order_value"))
    )
    estimated_fee = _as_float(
        _first_scalar(preview_payload, ("estimated_transaction_fee", "estimated_fee", "transaction_fee"))
    )

    try:
        placed = client.place_option(account_id, orders)
    except Exception as exc:
        mapped = _broker_reject(exc)
        if mapped and mapped[0] == "REJECTED":
            return WebullSandboxOrderResult(
                status="REJECTED", ticket_id=ticket, client_order_id=client_order_id,
                estimated_cost=estimated_cost, estimated_transaction_fee=estimated_fee,
                reason=f"place_{mapped[1]}",
            )
        return WebullSandboxOrderResult(
            status="ERROR", ticket_id=ticket, client_order_id=client_order_id,
            estimated_cost=estimated_cost, estimated_transaction_fee=estimated_fee,
            reason=f"place_request_failed:{type(exc).__name__}",
            warnings=("placement_outcome_unknown_check_order_detail",),
        )
    if _status(placed) != 200:
        return WebullSandboxOrderResult(
            status="REJECTED", ticket_id=ticket, client_order_id=client_order_id,
            estimated_cost=estimated_cost, estimated_transaction_fee=estimated_fee,
            reason=f"place_http_{_status(placed)}",
        )
    placed_payload, err = _json(placed)
    if err:
        return WebullSandboxOrderResult(
            status="ERROR", ticket_id=ticket, client_order_id=client_order_id,
            reason=f"place_{err}", warnings=("placement_outcome_unknown_check_order_detail",),
        )

    return WebullSandboxOrderResult(
        status="SUBMITTED",
        submitted=True,
        ticket_id=ticket,
        client_order_id=client_order_id,
        broker_order_id=_broker_order_id(placed_payload),
        estimated_cost=estimated_cost,
        estimated_transaction_fee=estimated_fee,
        warnings=("sandbox_paper_only", "mirror_lane_not_evidence_of_record"),
    )


def preview_sandbox_paper_option_close(
    entry_request: OptionsBrokerPreviewRequest,
    entry_detail: WebullSandboxOrderDetail,
    limit_price: float,
    options_config: OptionsManagerConfig,
    env: Mapping[str, str] | None = None,
    *,
    preview_client_factory: PaperOrderClientFactory | None = None,
) -> WebullSandboxPreviewResult:
    """Preview a single-leg SELL_TO_CLOSE for a proven filled sandbox entry.

    Preview only: no placement call exists in this function. It is the next
    proof gate before any close-submit capability is considered.
    """
    source = os.environ if env is None else env
    reason = _config_block_reason(source, options_config, require_real_preview=True)
    entry_ticket = (entry_request.ticket_id or "").strip()
    if reason:
        return WebullSandboxPreviewResult(
            preview_ready=False, status="BLOCKED", ticket_id=entry_ticket or None, reason=reason
        )

    boundary = validate_preview_boundary(entry_request, options_config)
    if not boundary.preview_ready:
        return WebullSandboxPreviewResult(
            preview_ready=False,
            status="REJECTED",
            ticket_id=entry_ticket or None,
            reason=f"entry_boundary:{boundary.failed_stage}:{boundary.reason}",
        )

    if (
        entry_detail.status != "OK"
        or entry_detail.order_state != "FILLED"
        or not entry_detail.terminal
    ):
        return WebullSandboxPreviewResult(
            preview_ready=False,
            status="REJECTED",
            ticket_id=entry_ticket or None,
            reason="entry_not_proven_filled",
        )
    if (
        entry_detail.ticket_id != entry_ticket
        or entry_detail.client_order_id != entry_ticket
    ):
        return WebullSandboxPreviewResult(
            preview_ready=False,
            status="REJECTED",
            ticket_id=entry_ticket or None,
            reason="entry_detail_ticket_mismatch",
        )
    if entry_detail.filled_quantity != entry_request.quantity:
        return WebullSandboxPreviewResult(
            preview_ready=False,
            status="REJECTED",
            ticket_id=entry_ticket or None,
            reason="entry_filled_quantity_mismatch",
        )

    try:
        close_price = float(limit_price)
    except (TypeError, ValueError):
        close_price = float("nan")
    if not math.isfinite(close_price) or close_price <= 0:
        return WebullSandboxPreviewResult(
            preview_ready=False,
            status="REJECTED",
            ticket_id=entry_ticket or None,
            reason="close_limit_price_invalid",
        )

    close_ticket = build_sandbox_close_ticket_id(entry_ticket)
    orders = _close_orders(
        entry_request,
        close_ticket_id=close_ticket,
        limit_price=close_price,
    )

    key, secret = _credentials(source)
    factory = preview_client_factory or _OfficialPaperOrderClient
    try:
        client = factory(key, secret)
    except ImportError:
        return WebullSandboxPreviewResult(
            preview_ready=False, status="BLOCKED", ticket_id=close_ticket, reason="sdk_unavailable"
        )
    except Exception as exc:
        return WebullSandboxPreviewResult(
            preview_ready=False,
            status="ERROR",
            ticket_id=close_ticket,
            reason=f"client_init_failed:{type(exc).__name__}",
        )

    account_id, select_reason = _resolve_account(client)
    if select_reason or not account_id:
        return WebullSandboxPreviewResult(
            preview_ready=False,
            status="ERROR",
            ticket_id=close_ticket,
            reason=select_reason or "sandbox_individual_cash_account_missing",
        )

    try:
        preview = client.preview_option(account_id, orders)
    except Exception as exc:
        mapped = _broker_reject(exc)
        if mapped:
            return WebullSandboxPreviewResult(
                preview_ready=False,
                status="REJECTED" if mapped[0] == "REJECTED" else "ERROR",
                ticket_id=close_ticket,
                reason=f"close_preview_{mapped[1]}",
            )
        return WebullSandboxPreviewResult(
            preview_ready=False,
            status="ERROR",
            ticket_id=close_ticket,
            reason=f"close_preview_request_failed:{type(exc).__name__}",
        )
    if _status(preview) != 200:
        return WebullSandboxPreviewResult(
            preview_ready=False,
            status="REJECTED",
            ticket_id=close_ticket,
            reason=f"close_preview_http_{_status(preview)}",
        )
    payload, err = _json(preview)
    if err:
        return WebullSandboxPreviewResult(
            preview_ready=False,
            status="ERROR",
            ticket_id=close_ticket,
            reason=f"close_preview_{err}",
        )

    return WebullSandboxPreviewResult(
        preview_ready=True,
        status="PREVIEW_READY",
        ticket_id=close_ticket,
        broker=WEBULL_SANDBOX_BROKER,
        executable=False,
        submitted=False,
        broker_order_id=None,
        currency=(
            str(_first_scalar(payload, ("currency", "currency_code")))
            if _first_scalar(payload, ("currency", "currency_code")) is not None
            else None
        ),
        estimated_cost=_as_float(
            _first_scalar(payload, ("estimated_cost", "estimated_notional", "order_value"))
        ),
        estimated_transaction_fee=_as_float(
            _first_scalar(payload, ("estimated_transaction_fee", "estimated_fee", "transaction_fee"))
        ),
        warnings=(
            "sandbox_paper_only",
            "sell_to_close_preview_only_no_submission",
        ),
    )


def cancel_sandbox_paper_option_order(
    ticket_id: str,
    options_config: OptionsManagerConfig,
    env: Mapping[str, str] | None = None,
    *,
    client_factory: PaperOrderClientFactory | None = None,
) -> WebullSandboxCancelResult:
    source = os.environ if env is None else env
    client_order_id = (ticket_id or "").strip()
    if not client_order_id:
        return WebullSandboxCancelResult(status="REJECTED", reason="ticket_id_missing")
    reason = _submit_block_reason(source, options_config)
    if reason:
        return WebullSandboxCancelResult(status="BLOCKED", ticket_id=client_order_id, reason=reason)

    key, secret = _credentials(source)
    factory = client_factory or _OfficialPaperOrderClient
    try:
        client = factory(key, secret)
    except ImportError:
        return WebullSandboxCancelResult(status="BLOCKED", ticket_id=client_order_id, reason="sdk_unavailable")
    except Exception as exc:
        return WebullSandboxCancelResult(
            status="ERROR", ticket_id=client_order_id, reason=f"client_init_failed:{type(exc).__name__}"
        )
    account_id, select_reason = _resolve_account(client)
    if select_reason or not account_id:
        return WebullSandboxCancelResult(status="ERROR", ticket_id=client_order_id, reason=select_reason)
    try:
        response = client.cancel_option(account_id, client_order_id)
    except Exception as exc:
        mapped = _broker_reject(exc)
        if mapped:
            return WebullSandboxCancelResult(
                status="REJECTED" if mapped[0] == "REJECTED" else "ERROR",
                ticket_id=client_order_id, client_order_id=client_order_id, reason=f"cancel_{mapped[1]}",
            )
        return WebullSandboxCancelResult(
            status="ERROR", ticket_id=client_order_id, client_order_id=client_order_id,
            reason=f"cancel_request_failed:{type(exc).__name__}",
        )
    if _status(response) != 200:
        return WebullSandboxCancelResult(
            status="REJECTED", ticket_id=client_order_id, client_order_id=client_order_id,
            reason=f"cancel_http_{_status(response)}",
        )
    payload, err = _json(response)
    return WebullSandboxCancelResult(
        status="CANCEL_REQUESTED",
        ticket_id=client_order_id,
        client_order_id=client_order_id,
        broker_order_id=_broker_order_id(payload) if not err else None,
    )


def get_sandbox_paper_order_detail(
    ticket_id: str,
    options_config: OptionsManagerConfig,
    env: Mapping[str, str] | None = None,
    *,
    client_factory: PaperOrderClientFactory | None = None,
) -> WebullSandboxOrderDetail:
    """Read-only order lookup by client_order_id (== ticket_id)."""
    source = os.environ if env is None else env
    client_order_id = (ticket_id or "").strip()
    if not client_order_id:
        return WebullSandboxOrderDetail(status="ERROR", reason="ticket_id_missing")
    # Reads require the sandbox config to be safe but NOT the submit opt-in.
    reason = _config_block_reason(source, options_config, require_real_preview=False)
    if reason:
        return WebullSandboxOrderDetail(status="BLOCKED", ticket_id=client_order_id, reason=reason)

    key, secret = _credentials(source)
    factory = client_factory or _OfficialPaperOrderClient
    try:
        client = factory(key, secret)
    except ImportError:
        return WebullSandboxOrderDetail(status="BLOCKED", ticket_id=client_order_id, reason="sdk_unavailable")
    except Exception as exc:
        return WebullSandboxOrderDetail(
            status="ERROR", ticket_id=client_order_id, reason=f"client_init_failed:{type(exc).__name__}"
        )
    account_id, select_reason = _resolve_account(client)
    if select_reason or not account_id:
        return WebullSandboxOrderDetail(status="ERROR", ticket_id=client_order_id, reason=select_reason)
    try:
        response = client.get_order_detail(account_id, client_order_id)
    except Exception as exc:
        mapped = _broker_reject(exc)
        if mapped:
            code = mapped[1]
            if "NOT_FOUND" in code.upper() or "NOT_EXIST" in code.upper():
                return WebullSandboxOrderDetail(status="NOT_FOUND", ticket_id=client_order_id, client_order_id=client_order_id, reason=code)
            return WebullSandboxOrderDetail(status="ERROR", ticket_id=client_order_id, client_order_id=client_order_id, reason=f"detail_{code}")
        return WebullSandboxOrderDetail(
            status="ERROR", ticket_id=client_order_id, reason=f"detail_request_failed:{type(exc).__name__}"
        )
    code = _status(response)
    if code == 404:
        return WebullSandboxOrderDetail(status="NOT_FOUND", ticket_id=client_order_id, client_order_id=client_order_id)
    if code != 200:
        return WebullSandboxOrderDetail(
            status="ERROR", ticket_id=client_order_id, client_order_id=client_order_id, reason=f"detail_http_{code}"
        )
    payload, err = _json(response)
    if err:
        return WebullSandboxOrderDetail(status="ERROR", ticket_id=client_order_id, reason=f"detail_{err}")

    state_raw = _first_scalar(payload, ("order_status", "status", "order_state"))
    state = str(state_raw).upper() if state_raw is not None else None
    filled = _first_scalar(payload, ("filled_quantity", "filled_qty", "filledQuantity"))
    try:
        filled_qty = int(float(filled)) if filled is not None else None
    except (TypeError, ValueError):
        filled_qty = None
    return WebullSandboxOrderDetail(
        status="OK",
        ticket_id=client_order_id,
        client_order_id=client_order_id,
        broker_order_id=_broker_order_id(payload),
        order_state=state,
        terminal=bool(state and state in TERMINAL_ORDER_STATES),
        filled_quantity=filled_qty,
        average_fill_price=_as_float(_first_scalar(payload, ("avg_fill_price", "average_fill_price", "filled_price", "avg_price"))),
        limit_price=_as_float(_first_scalar(payload, ("limit_price", "price"))),
    )


__all__ = [
    "TERMINAL_ORDER_STATES",
    "WebullSandboxCancelResult",
    "WebullSandboxOrderDetail",
    "WebullSandboxOrderResult",
    "build_sandbox_close_ticket_id",
    "cancel_sandbox_paper_option_order",
    "get_sandbox_paper_order_detail",
    "preview_sandbox_paper_option_close",
    "submit_sandbox_paper_option_order",
]