"""
execution/webull_sandbox_futures_mirror.py

Webull SANDBOX futures paper MIRROR lane — UNWIRED.

Mirrors an internal paper-lane bracket (entry fill / exit fill) as plain
1-lot orders on the Webull OpenAPI *sandbox* FUTURES paper account so the
operator can watch the paper lanes play out on a real paper ledger.

Contract (mirror, not execution):
- The internal journal / PaperBroker remains the evidence of record. Nothing
  here feeds back into risk, decisions, or PaperBroker resolution.
- Only the sandbox host is ever contacted, and only when the frozen Webull
  paper config is paper-only-safe AND ``WEBULL_FUTURES_MIRROR_ENABLED`` is
  explicitly true. Default is off → every call returns BLOCKED without
  creating a client.
- Orders are single legs (Webull futures have no OTO/OCO combos), quantity is
  capped by ``WEBULL_FUTURES_MIRROR_MAX_CONTRACTS`` (default 1), and only
  quarterly micro roots with a computable front month are mirrored.
- No runtime module imports this file yet (guarded by tests). Wiring is a
  separate, flag-gated change.

Proven against the sandbox 2026-09-21: place / detail / cancel on MESZ6 and
preview on MNQZ6 all 200; no trading-hours restriction on futures.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Literal, Mapping, Optional, Protocol

from execution.broker_interface import BracketOrder, Fill
from execution.tradovate_broker import _front_month_symbol
from integrations.webull_paper_config import load_webull_paper_config

WEBULL_SANDBOX_FUTURES_BROKER = "webull_sandbox_futures_paper"
_FUTURES_ACCOUNT_CLASS = "FUTURES"
_DEFAULT_ROOTS = ("MNQ", "MES")
TERMINAL_ORDER_STATES = frozenset(
    {"FILLED", "CANCELLED", "CANCELED", "REJECTED", "EXPIRED", "FAILED"}
)

OrderStatus = Literal["SUBMITTED", "BLOCKED", "REJECTED", "ERROR"]
CancelStatus = Literal["CANCELLED", "BLOCKED", "REJECTED", "ERROR"]
DetailStatus = Literal["OK", "BLOCKED", "ERROR"]


class _ResponseLike(Protocol):
    status_code: int

    def json(self) -> Any: ...


class _FuturesClientLike(Protocol):
    def get_account_list(self) -> _ResponseLike: ...

    def place_order(self, account_id: str, orders: list[dict[str, Any]]) -> _ResponseLike: ...

    def cancel_order(self, account_id: str, client_order_id: str) -> _ResponseLike: ...

    def get_order_detail(self, account_id: str, client_order_id: str) -> _ResponseLike: ...


FuturesClientFactory = Callable[[str, str], _FuturesClientLike]


# ─── Config ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WebullFuturesMirrorConfig:
    enabled: bool
    max_contracts: int
    allowed_roots: tuple[str, ...]
    errors: tuple[str, ...] = ()


def load_mirror_config(env: Mapping[str, str] | None = None) -> WebullFuturesMirrorConfig:
    source = os.environ if env is None else env
    errors: list[str] = []

    raw_enabled = str(source.get("WEBULL_FUTURES_MIRROR_ENABLED", "false") or "").strip().lower()
    if raw_enabled in {"1", "true", "yes", "on"}:
        enabled = True
    elif raw_enabled in {"", "0", "false", "no", "off"}:
        enabled = False
    else:
        enabled = False
        errors.append("WEBULL_FUTURES_MIRROR_ENABLED must be true or false")

    raw_max = str(source.get("WEBULL_FUTURES_MIRROR_MAX_CONTRACTS", "1") or "1").strip()
    try:
        max_contracts = int(raw_max)
    except ValueError:
        max_contracts = 0
        errors.append("WEBULL_FUTURES_MIRROR_MAX_CONTRACTS must be an integer")
    if max_contracts < 1:
        errors.append("WEBULL_FUTURES_MIRROR_MAX_CONTRACTS must be >= 1")

    raw_roots = str(source.get("WEBULL_FUTURES_MIRROR_INSTRUMENTS", "") or "").strip()
    roots = tuple(
        r.strip().upper().replace("1!", "") for r in raw_roots.split(",") if r.strip()
    ) or _DEFAULT_ROOTS

    return WebullFuturesMirrorConfig(
        enabled=enabled,
        max_contracts=max_contracts,
        allowed_roots=roots,
        errors=tuple(errors),
    )


def mirror_block_reason(env: Mapping[str, str]) -> str | None:
    """First reason the mirror must not touch the network, else None."""
    paper = load_webull_paper_config(env)
    if not paper.network_calls_allowed:
        return "webull_sandbox_config_invalid"
    mirror = load_mirror_config(env)
    if mirror.errors:
        return "webull_futures_mirror_config_invalid"
    if not mirror.enabled:
        return "webull_futures_mirror_not_enabled"
    return None


# ─── Results ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MirrorOrderResult:
    status: OrderStatus
    broker: str = WEBULL_SANDBOX_FUTURES_BROKER
    leg: str | None = None            # entry | exit
    client_order_id: str | None = None
    broker_order_id: str | None = None
    symbol: str | None = None
    side: str | None = None
    quantity: int | None = None
    order_type: str | None = None
    limit_price: float | None = None
    reason: str | None = None

    @property
    def submitted(self) -> bool:
        return self.status == "SUBMITTED"


@dataclass(frozen=True)
class MirrorCancelResult:
    status: CancelStatus
    client_order_id: str | None = None
    broker_order_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class MirrorOrderDetail:
    status: DetailStatus
    client_order_id: str | None = None
    broker_order_id: str | None = None
    order_state: str | None = None
    terminal: bool = False
    filled_quantity: int | None = None
    average_fill_price: float | None = None
    reason: str | None = None


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _status(response: Any) -> int:
    return int(getattr(response, "status_code", 0) or 0)


def _json(response: Any) -> tuple[Any, str | None]:
    try:
        return response.json(), None
    except Exception as exc:  # pragma: no cover - defensive
        return None, f"invalid_json:{type(exc).__name__}"


def _credentials(env: Mapping[str, str]) -> tuple[str, str]:
    return (
        str(env.get("WEBULL_SANDBOX_APP_KEY", "") or ""),
        str(env.get("WEBULL_SANDBOX_APP_SECRET", "") or ""),
    )


def _account_rows(payload: Any) -> list[dict[str, Any]] | None:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
    return None


def select_futures_account(payload: Any) -> tuple[str | None, str | None]:
    """Exactly one FUTURES-class account, else fail closed. Never logs the id."""
    rows = _account_rows(payload)
    if rows is None:
        return None, "account_schema_unrecognized"
    matches: dict[str, dict[str, Any]] = {}
    for row in rows:
        account_class = str(row.get("account_class", "") or "").upper()
        account_id = str(row.get("account_id", "") or "").strip()
        if account_class == _FUTURES_ACCOUNT_CLASS and account_id:
            matches[account_id] = row
    if not matches:
        return None, "sandbox_futures_account_missing"
    if len(matches) != 1:
        return None, "sandbox_futures_account_ambiguous"
    return next(iter(matches)), None


def _resolve_account(client: _FuturesClientLike) -> tuple[str | None, str | None]:
    try:
        response = client.get_account_list()
    except Exception as exc:
        return None, f"account_request_failed:{type(exc).__name__}"
    if _status(response) != 200:
        return None, "account_http_error"
    payload, err = _json(response)
    if err:
        return None, f"account_{err}"
    return select_futures_account(payload)


def _broker_reject(exc: Exception) -> tuple[str, str] | None:
    """SDK ServerException (HTTP 4xx + error_code) → ('REJECTED', 'broker:<CODE>')."""
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
    if isinstance(payload, dict):
        for key in ("order_id", "broker_order_id", "orderId"):
            value = payload.get(key)
            if value not in (None, ""):
                return str(value)
    return None


def mirror_client_order_id(source_id: str | None, leg: str, fallback: str) -> str:
    """Deterministic 32-hex id per (source order id, leg) so a retry never
    double-submits. ``fallback`` is used when the source has no id."""
    base = source_id or fallback
    return hashlib.sha256(f"webull-futures-mirror:{base}:{leg}".encode()).hexdigest()[:32]


def mirror_symbol(instrument: str, today: date) -> str | None:
    """Front-month Webull symbol (e.g. MNQZ6) or None when not computable."""
    return _front_month_symbol(instrument, today)


def _fmt_price(value: float) -> str:
    return f"{float(value):.2f}"


def _build_order(
    *,
    client_order_id: str,
    symbol: str,
    side: str,
    quantity: int,
    order_type: str,
    limit_price: float | None,
) -> dict[str, Any]:
    order: dict[str, Any] = {
        "client_order_id": client_order_id,
        "combo_type": "NORMAL",
        "order_type": order_type,
        "quantity": str(int(quantity)),
        "side": side,
        "time_in_force": "DAY",
        "entrust_type": "QTY",
        "symbol": symbol,
        "instrument_type": "FUTURES",
        "market": "US",
    }
    if order_type == "LIMIT":
        if limit_price is None:
            raise ValueError("limit order requires limit_price")
        order["limit_price"] = _fmt_price(limit_price)
    return order


# ─── Official client ─────────────────────────────────────────────────────────

class _OfficialFuturesClient:
    """Thin wrapper over the official SDK, sandbox endpoint only."""

    def __init__(self, app_key: str, app_secret: str):
        from webull.core.client import ApiClient
        from webull.trade.trade.v2.account_info_v2 import AccountV2

        from options_manager.adapters.webull_sandbox import (  # sandbox host pin
            WEBULL_REGION,
            WEBULL_SANDBOX_HOST,
        )

        self._api = ApiClient(app_key, app_secret, WEBULL_REGION)
        self._api.add_endpoint(WEBULL_REGION, WEBULL_SANDBOX_HOST)
        self._accounts = AccountV2(self._api)

    def get_account_list(self) -> _ResponseLike:
        return self._accounts.get_account_list()

    def place_order(self, account_id: str, orders: list[dict[str, Any]]) -> _ResponseLike:
        from webull.trade.request.v3.place_order_request import PlaceOrderRequest

        request = PlaceOrderRequest()
        request.set_account_id(account_id)
        request.set_new_orders(orders)
        request.add_custom_headers_from_order(orders)
        return self._api.get_response(request)

    def cancel_order(self, account_id: str, client_order_id: str) -> _ResponseLike:
        from webull.trade.request.v3.cancel_order_request import CancelOrderRequest

        request = CancelOrderRequest()
        request.set_account_id(account_id)
        request.set_client_order_id(client_order_id)
        return self._api.get_response(request)

    def get_order_detail(self, account_id: str, client_order_id: str) -> _ResponseLike:
        from webull.trade.request.v3.get_order_detail_request import OrderDetailRequest

        request = OrderDetailRequest()
        request.set_account_id(account_id)
        request.set_client_order_id(client_order_id)
        return self._api.get_response(request)


def _client(env: Mapping[str, str], factory: FuturesClientFactory | None) -> _FuturesClientLike:
    key, secret = _credentials(env)
    return (factory or _OfficialFuturesClient)(key, secret)


# ─── Public API ──────────────────────────────────────────────────────────────

def _submit(
    *,
    env: Mapping[str, str],
    leg: str,
    source_id: str | None,
    fallback_id: str,
    instrument: str,
    side: str,
    contracts: int,
    order_type: str,
    limit_price: float | None,
    today: date | None,
    client_factory: FuturesClientFactory | None,
) -> MirrorOrderResult:
    reason = mirror_block_reason(env)
    if reason:
        return MirrorOrderResult(status="BLOCKED", leg=leg, reason=reason)
    cfg = load_mirror_config(env)

    root = str(instrument or "").upper().replace("1!", "")
    if root not in cfg.allowed_roots:
        return MirrorOrderResult(status="BLOCKED", leg=leg, reason="instrument_not_mirrored")
    symbol = mirror_symbol(root, today or date.today())
    if not symbol:
        return MirrorOrderResult(status="BLOCKED", leg=leg, reason="front_month_unresolved")
    if side not in ("BUY", "SELL"):
        return MirrorOrderResult(status="BLOCKED", leg=leg, reason="direction_invalid")
    quantity = min(int(contracts or 0), cfg.max_contracts)
    if quantity < 1:
        return MirrorOrderResult(status="BLOCKED", leg=leg, reason="quantity_invalid")

    client_order_id = mirror_client_order_id(source_id, leg, fallback_id)
    try:
        order = _build_order(
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
        )
    except ValueError as exc:
        return MirrorOrderResult(status="BLOCKED", leg=leg, reason=str(exc))

    base = dict(
        leg=leg,
        client_order_id=client_order_id,
        symbol=symbol,
        side=side,
        quantity=quantity,
        order_type=order_type,
        limit_price=float(limit_price) if order_type == "LIMIT" else None,
    )

    client = _client(env, client_factory)
    account_id, err = _resolve_account(client)
    if err or not account_id:
        return MirrorOrderResult(status="ERROR", reason=err or "account_unresolved", **base)

    try:
        response = client.place_order(account_id, [order])
    except Exception as exc:
        mapped = _broker_reject(exc)
        if mapped:
            return MirrorOrderResult(status=mapped[0], reason=mapped[1], **base)  # type: ignore[arg-type]
        return MirrorOrderResult(
            status="ERROR", reason=f"place_request_failed:{type(exc).__name__}", **base
        )
    if _status(response) != 200:
        return MirrorOrderResult(status="REJECTED", reason="place_http_error", **base)
    payload, jerr = _json(response)
    if jerr:
        return MirrorOrderResult(status="ERROR", reason=f"place_{jerr}", **base)
    return MirrorOrderResult(
        status="SUBMITTED", broker_order_id=_broker_order_id(payload), **base
    )


def mirror_entry(
    order: BracketOrder,
    env: Mapping[str, str] | None = None,
    *,
    source_id: str | None = None,
    today: date | None = None,
    client_factory: FuturesClientFactory | None = None,
) -> MirrorOrderResult:
    """Mirror a paper-lane ENTRY as one single-leg order.

    LIMIT at ``order.entry`` unless ``order.force_market_entry``. The stop and
    target are NOT sent (no combos on Webull futures) — exits are mirrored
    separately via :func:`mirror_exit` when the paper lane resolves.
    """
    source = os.environ if env is None else env
    side = "BUY" if str(order.direction).upper() == "LONG" else (
        "SELL" if str(order.direction).upper() == "SHORT" else "INVALID"
    )
    order_type = "MARKET" if order.force_market_entry else "LIMIT"
    return _submit(
        env=source,
        leg="entry",
        source_id=source_id or order.client_order_id,
        fallback_id=f"{order.instrument}:{order.strategy}:{order.entry}:{order.stop}",
        instrument=order.instrument,
        side=side,
        contracts=order.contracts,
        order_type=order_type,
        limit_price=None if order_type == "MARKET" else order.entry,
        today=today,
        client_factory=client_factory,
    )


def mirror_exit(
    fill: Fill,
    env: Mapping[str, str] | None = None,
    *,
    source_id: str | None = None,
    today: date | None = None,
    client_factory: FuturesClientFactory | None = None,
) -> MirrorOrderResult:
    """Mirror a resolved paper-lane EXIT as one MARKET order on the opposite side.

    Only fills that actually exited (``exit_price`` set, result not CANCELLED)
    are mirrored; no-fill outcomes have nothing to flatten.
    """
    source = os.environ if env is None else env
    if fill.exit_price is None or str(fill.result).upper() in ("CANCELLED", "OPEN"):
        return MirrorOrderResult(status="BLOCKED", leg="exit", reason="fill_not_exited")
    side = "SELL" if str(fill.direction).upper() == "LONG" else (
        "BUY" if str(fill.direction).upper() == "SHORT" else "INVALID"
    )
    return _submit(
        env=source,
        leg="exit",
        source_id=source_id or fill.paper_order_id,
        fallback_id=f"{fill.instrument}:{fill.direction}:{fill.entry_price}:{fill.exit_price}",
        instrument=fill.instrument,
        side=side,
        contracts=fill.contracts,
        order_type="MARKET",
        limit_price=None,
        today=today,
        client_factory=client_factory,
    )


def cancel_mirror_order(
    client_order_id: str,
    env: Mapping[str, str] | None = None,
    *,
    client_factory: FuturesClientFactory | None = None,
) -> MirrorCancelResult:
    source = os.environ if env is None else env
    reason = mirror_block_reason(source)
    if reason:
        return MirrorCancelResult(status="BLOCKED", client_order_id=client_order_id, reason=reason)
    client = _client(source, client_factory)
    account_id, err = _resolve_account(client)
    if err or not account_id:
        return MirrorCancelResult(
            status="ERROR", client_order_id=client_order_id, reason=err or "account_unresolved"
        )
    try:
        response = client.cancel_order(account_id, client_order_id)
    except Exception as exc:
        mapped = _broker_reject(exc)
        if mapped:
            return MirrorCancelResult(status=mapped[0], client_order_id=client_order_id, reason=mapped[1])  # type: ignore[arg-type]
        return MirrorCancelResult(
            status="ERROR",
            client_order_id=client_order_id,
            reason=f"cancel_request_failed:{type(exc).__name__}",
        )
    if _status(response) != 200:
        return MirrorCancelResult(status="REJECTED", client_order_id=client_order_id, reason="cancel_http_error")
    payload, _ = _json(response)
    return MirrorCancelResult(
        status="CANCELLED", client_order_id=client_order_id, broker_order_id=_broker_order_id(payload)
    )


def get_mirror_order_detail(
    client_order_id: str,
    env: Mapping[str, str] | None = None,
    *,
    client_factory: FuturesClientFactory | None = None,
) -> MirrorOrderDetail:
    source = os.environ if env is None else env
    reason = mirror_block_reason(source)
    if reason:
        return MirrorOrderDetail(status="BLOCKED", client_order_id=client_order_id, reason=reason)
    client = _client(source, client_factory)
    account_id, err = _resolve_account(client)
    if err or not account_id:
        return MirrorOrderDetail(
            status="ERROR", client_order_id=client_order_id, reason=err or "account_unresolved"
        )
    try:
        response = client.get_order_detail(account_id, client_order_id)
    except Exception as exc:
        mapped = _broker_reject(exc)
        return MirrorOrderDetail(
            status="ERROR",
            client_order_id=client_order_id,
            reason=mapped[1] if mapped else f"detail_request_failed:{type(exc).__name__}",
        )
    if _status(response) != 200:
        return MirrorOrderDetail(status="ERROR", client_order_id=client_order_id, reason="detail_http_error")
    payload, jerr = _json(response)
    if jerr:
        return MirrorOrderDetail(status="ERROR", client_order_id=client_order_id, reason=f"detail_{jerr}")

    leg: dict[str, Any] = {}
    if isinstance(payload, dict):
        orders = payload.get("orders")
        if isinstance(orders, list) and orders and isinstance(orders[0], dict):
            leg = orders[0]
    state = str(leg.get("status", "") or "").upper() or None

    def _f(value: Any) -> float | None:
        try:
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    filled = _f(leg.get("filled_quantity"))
    return MirrorOrderDetail(
        status="OK",
        client_order_id=client_order_id,
        broker_order_id=_broker_order_id(leg) or _broker_order_id(payload)
        or (str(payload.get("combo_order_id")) if isinstance(payload, dict) and payload.get("combo_order_id") else None),
        order_state=state,
        terminal=state in TERMINAL_ORDER_STATES if state else False,
        filled_quantity=int(filled) if filled is not None else None,
        average_fill_price=_f(leg.get("avg_filled_price") or leg.get("average_fill_price")),
    )


__all__ = [
    "WEBULL_SANDBOX_FUTURES_BROKER",
    "TERMINAL_ORDER_STATES",
    "WebullFuturesMirrorConfig",
    "MirrorOrderResult",
    "MirrorCancelResult",
    "MirrorOrderDetail",
    "load_mirror_config",
    "mirror_block_reason",
    "mirror_client_order_id",
    "mirror_symbol",
    "select_futures_account",
    "mirror_entry",
    "mirror_exit",
    "cancel_mirror_order",
    "get_mirror_order_detail",
]
