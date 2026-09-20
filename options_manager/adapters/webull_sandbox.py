"""Webull sandbox-only adapter for the options system.

This adapter is deliberately preview-only. It can read the sandbox Individual
Cash account, discover option-contract metadata, and request a broker preview.
It exposes no order-submission, cancellation, replacement, or live-routing
capability.

Safety is layered:
- the frozen Webull config must point at the sandbox host in paper mode;
- both Webull live flags must remain false;
- options_manager live options trading must remain false;
- broker-boundary validation must pass before a preview request;
- real broker preview must be explicitly enabled in OptionsManagerConfig;
- sandbox account selection must resolve exactly one Individual Cash account.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import os
from typing import Any, Callable, Literal, Mapping, Protocol

from integrations.webull_paper_config import (
    WEBULL_SANDBOX_HOST,
    load_webull_paper_config,
)
from options_manager.broker_boundary import (
    OptionsBrokerPreviewRequest,
    validate_preview_boundary,
)
from options_manager.config import OptionsManagerConfig

WEBULL_SANDBOX_BROKER = "WEBULL_SANDBOX"
WEBULL_REGION = "us"
_INDIVIDUAL_CASH_CLASS = "INDIVIDUAL_CASH"
_CASH_ACCOUNT_TYPE = "CASH"


class _ResponseLike(Protocol):
    status_code: int

    def json(self) -> Any: ...


class _AccountClientLike(Protocol):
    def get_account_list(self) -> _ResponseLike: ...

    def get_account_balance(self, account_id: str) -> _ResponseLike: ...

    def get_account_position(self, account_id: str) -> _ResponseLike: ...


class _InstrumentClientLike(Protocol):
    def get_option_contracts(self, ticker: str) -> _ResponseLike: ...
class _PreviewClientLike(Protocol):
    def get_account_list(self) -> _ResponseLike: ...

    def preview_option(self, account_id: str, orders: list[dict[str, Any]]) -> _ResponseLike: ...


AccountClientFactory = Callable[[str, str], _AccountClientLike]
InstrumentClientFactory = Callable[[str, str], _InstrumentClientLike]
PreviewClientFactory = Callable[[str, str], _PreviewClientLike]


@dataclass(frozen=True)
class WebullSandboxAccountSnapshot:
    status: Literal["OK", "BLOCKED", "WAIT", "ERROR"]
    endpoint: str = WEBULL_SANDBOX_HOST
    mode: str = "paper"
    account_class: str | None = None
    account_type: str | None = None
    currency: str | None = None
    net_liquidation: float | None = None
    cash_balance: float | None = None
    position_count: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class WebullSandboxOptionContract:
    option_symbol: str
    underlying_symbol: str
    expiration: date
    dte: int
    strike: float
    option_type: Literal["CALL", "PUT"]


@dataclass(frozen=True)
class WebullSandboxContractDiscovery:
    status: Literal["OK", "BLOCKED", "WAIT", "ERROR"]
    endpoint: str = WEBULL_SANDBOX_HOST
    mode: str = "paper"
    ticker: str = ""
    contracts: tuple[WebullSandboxOptionContract, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class WebullSandboxPreviewResult:
    preview_ready: bool
    status: Literal["PREVIEW_READY", "BLOCKED", "REJECTED", "ERROR"]
    ticket_id: str | None = None
    broker: str | None = None
    executable: bool = False
    submitted: bool = False
    broker_order_id: str | None = None
    currency: str | None = None
    estimated_cost: float | None = None
    estimated_transaction_fee: float | None = None
    reason: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
def _config_block_reason(
    env: Mapping[str, str],
    options_config: OptionsManagerConfig,
    *,
    require_real_preview: bool,
) -> str | None:
    webull = load_webull_paper_config(env)
    if not webull.paper_only_safe:
        return "webull_sandbox_config_invalid"
    if not webull.network_calls_allowed:
        return "webull_sandbox_api_disabled"
    if options_config.live_options_trading_enabled:
        return "live_options_trading_enabled"
    if require_real_preview and not options_config.broker_boundary_allow_real_preview:
        return "real_broker_preview_not_enabled"
    return None


def _credentials(env: Mapping[str, str]) -> tuple[str, str]:
    return (
        str(env.get("WEBULL_SANDBOX_APP_KEY", "") or "").strip(),
        str(env.get("WEBULL_SANDBOX_APP_SECRET", "") or "").strip(),
    )


def _account_rows(payload: Any) -> list[dict[str, Any]] | None:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return None
    for key in ("accounts", "account_list", "accountList"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]

    data = payload.get("data")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("accounts", "account_list", "accountList", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return None


def _select_individual_cash_account(payload: Any) -> tuple[str | None, str | None]:
    rows = _account_rows(payload)
    if rows is None:
        return None, "account_schema_unrecognized"

    matches: dict[str, dict[str, Any]] = {}
    for row in rows:
        account_class = str(row.get("account_class", "") or "").upper()
        account_type = str(row.get("account_type", "") or "").upper()
        account_id = str(row.get("account_id", "") or "").strip()
        if (
            account_class == _INDIVIDUAL_CASH_CLASS
            and account_type == _CASH_ACCOUNT_TYPE
            and account_id
        ):
            matches[account_id] = row

    if not matches:
        return None, "sandbox_individual_cash_account_missing"
    if len(matches) != 1:
        return None, "sandbox_individual_cash_account_ambiguous"
    return next(iter(matches)), None


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk_dicts(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_dicts(nested)


def _first_scalar(payload: Any, keys: tuple[str, ...]) -> Any:
    for row in _walk_dicts(payload):
        for key in keys:
            value = row.get(key)
            if value is not None and not isinstance(value, (dict, list)):
                return value
    return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
def _position_count(payload: Any) -> int | None:
    if isinstance(payload, list):
        return len(payload)
    if not isinstance(payload, dict):
        return None
    for key in ("positions", "position_list", "positionList", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
    data = payload.get("data")
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("positions", "position_list", "positionList", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return len(value)
    return None


def _official_api_client(app_key: str, app_secret: str):
    from webull.core.client import ApiClient

    client = ApiClient(app_key, app_secret, WEBULL_REGION)
    client.add_endpoint(WEBULL_REGION, WEBULL_SANDBOX_HOST)
    return client


def _official_account_client(app_key: str, app_secret: str) -> _AccountClientLike:
    from webull.trade.trade.v2.account_info_v2 import AccountV2

    return AccountV2(_official_api_client(app_key, app_secret))
class _OfficialInstrumentClient:
    def __init__(self, app_key: str, app_secret: str):
        from webull.data.quotes.instrument import Instrument

        self._instrument = Instrument(_official_api_client(app_key, app_secret))

    def get_option_contracts(self, ticker: str) -> _ResponseLike:
        return self._instrument.get_option_contracts(
            "US_OPTION",
            ticker,
            page_size=1000,
        )


class _OfficialPreviewClient:
    def __init__(self, app_key: str, app_secret: str):
        from webull.trade.trade.v2.account_info_v2 import AccountV2

        self._api = _official_api_client(app_key, app_secret)
        self._accounts = AccountV2(self._api)

    def get_account_list(self) -> _ResponseLike:
        return self._accounts.get_account_list()

    def preview_option(
        self, account_id: str, orders: list[dict[str, Any]]
    ) -> _ResponseLike:
        from webull.trade.request.v2.preview_option_request import PreviewOptionRequest

        request = PreviewOptionRequest()
        request.set_account_id(account_id)
        request.set_new_orders(orders)
        return self._api.get_response(request)


def read_sandbox_individual_cash_account(
    options_config: OptionsManagerConfig,
    env: Mapping[str, str] | None = None,
    *,
    account_client_factory: AccountClientFactory | None = None,
) -> WebullSandboxAccountSnapshot:
    source = os.environ if env is None else env
    reason = _config_block_reason(source, options_config, require_real_preview=False)
    if reason:
        return WebullSandboxAccountSnapshot(status="BLOCKED", reason=reason)

    key, secret = _credentials(source)
    factory = account_client_factory or _official_account_client
    try:
        client = factory(key, secret)
        account_response = client.get_account_list()
    except ImportError:
        return WebullSandboxAccountSnapshot(status="BLOCKED", reason="sdk_unavailable")
    except Exception as exc:
        return WebullSandboxAccountSnapshot(
            status="ERROR", reason=f"account_request_failed:{type(exc).__name__}"
        )

    if int(getattr(account_response, "status_code", 0) or 0) != 200:
        return WebullSandboxAccountSnapshot(
            status="ERROR",
            reason=f"account_http_{getattr(account_response, 'status_code', 'unknown')}",
        )
    try:
        account_payload = account_response.json()
    except Exception as exc:
        return WebullSandboxAccountSnapshot(
            status="ERROR", reason=f"account_invalid_json:{type(exc).__name__}"
        )

    account_id, select_reason = _select_individual_cash_account(account_payload)
    if select_reason:
        status = "WAIT" if select_reason.endswith("_missing") else "ERROR"
        return WebullSandboxAccountSnapshot(status=status, reason=select_reason)

    try:
        balance_response = client.get_account_balance(account_id)
        position_response = client.get_account_position(account_id)
    except Exception as exc:
        return WebullSandboxAccountSnapshot(
            status="ERROR", reason=f"account_detail_request_failed:{type(exc).__name__}"
        )

    if int(getattr(balance_response, "status_code", 0) or 0) != 200:
        return WebullSandboxAccountSnapshot(status="ERROR", reason="balance_http_error")
    if int(getattr(position_response, "status_code", 0) or 0) != 200:
        return WebullSandboxAccountSnapshot(status="ERROR", reason="positions_http_error")

    try:
        balance_payload = balance_response.json()
        position_payload = position_response.json()
    except Exception as exc:
        return WebullSandboxAccountSnapshot(
            status="ERROR", reason=f"account_detail_invalid_json:{type(exc).__name__}"
        )
    count = _position_count(position_payload)
    if count is None:
        return WebullSandboxAccountSnapshot(
            status="ERROR", reason="positions_schema_unrecognized"
        )

    currency = _first_scalar(balance_payload, ("currency", "currency_code"))
    net_liquidation = _as_float(
        _first_scalar(
            balance_payload,
            ("net_liquidation", "net_liquidation_value", "net_asset_value"),
        )
    )
    cash_balance = _as_float(
        _first_scalar(
            balance_payload,
            ("cash_balance", "cash", "settled_cash", "total_cash"),
        )
    )
    return WebullSandboxAccountSnapshot(
        status="OK",
        account_class=_INDIVIDUAL_CASH_CLASS,
        account_type=_CASH_ACCOUNT_TYPE,
        currency=str(currency) if currency is not None else None,
        net_liquidation=net_liquidation,
        cash_balance=cash_balance,
        position_count=count,
    )


def discover_sandbox_option_contracts(
    ticker: str,
    options_config: OptionsManagerConfig,
    env: Mapping[str, str] | None = None,
    *,
    observed_on: date | None = None,
    min_dte: int = 0,
    option_type: Literal["CALL", "PUT"] | None = None,
    instrument_client_factory: InstrumentClientFactory | None = None,
) -> WebullSandboxContractDiscovery:
    source = os.environ if env is None else env
    reason = _config_block_reason(source, options_config, require_real_preview=False)
    symbol = ticker.strip().upper()
    if reason:
        return WebullSandboxContractDiscovery(
            status="BLOCKED", ticker=symbol, reason=reason
        )
    if not symbol:
        return WebullSandboxContractDiscovery(
            status="ERROR", ticker=symbol, reason="ticker_missing"
        )

    key, secret = _credentials(source)
    factory = instrument_client_factory or _OfficialInstrumentClient
    try:
        response = factory(key, secret).get_option_contracts(symbol)
    except ImportError:
        return WebullSandboxContractDiscovery(
            status="BLOCKED", ticker=symbol, reason="sdk_unavailable"
        )
    except Exception as exc:
        return WebullSandboxContractDiscovery(
            status="ERROR",
            ticker=symbol,
            reason=f"contract_request_failed:{type(exc).__name__}",
        )
    if int(getattr(response, "status_code", 0) or 0) != 200:
        return WebullSandboxContractDiscovery(
            status="ERROR",
            ticker=symbol,
            reason=f"contract_http_{getattr(response, 'status_code', 'unknown')}",
        )

    try:
        payload = response.json()
    except Exception as exc:
        return WebullSandboxContractDiscovery(
            status="ERROR",
            ticker=symbol,
            reason=f"contract_invalid_json:{type(exc).__name__}",
        )

    as_of = observed_on or datetime.now(timezone.utc).date()
    contracts: dict[tuple[str, date, float, str], WebullSandboxOptionContract] = {}
    for row in _walk_dicts(payload):
        raw_exp = (
            row.get("option_expire_date")
            or row.get("expire_date")
            or row.get("expiration_date")
        )
        raw_strike = row.get("strike_price")
        raw_type = str(row.get("option_type", "") or "").upper()
        raw_symbol = str(row.get("symbol", "") or "").strip().upper()
        if not raw_exp or raw_strike is None or raw_type not in {"CALL", "PUT"}:
            continue
        try:
            expiration = date.fromisoformat(str(raw_exp)[:10])
            strike = float(raw_strike)
        except (TypeError, ValueError):
            continue
        dte = (expiration - as_of).days
        if dte < min_dte:
            continue
        if option_type is not None and raw_type != option_type:
            continue
        contract = WebullSandboxOptionContract(
            option_symbol=raw_symbol,
            underlying_symbol=symbol,
            expiration=expiration,
            dte=dte,
            strike=strike,
            option_type=raw_type,
        )
        contracts[(raw_symbol, expiration, strike, raw_type)] = contract

    ordered = tuple(
        sorted(
            contracts.values(),
            key=lambda item: (item.expiration, item.strike, item.option_type),
        )
    )
    if not ordered:
        return WebullSandboxContractDiscovery(
            status="WAIT", ticker=symbol, reason="no_matching_contracts"
        )
    return WebullSandboxContractDiscovery(
        status="OK", ticker=symbol, contracts=ordered
    )
def _preview_orders(request: OptionsBrokerPreviewRequest) -> list[dict[str, Any]]:
    return [
        {
            "client_order_id": request.ticket_id,
            "combo_type": "NORMAL",
            "order_type": "LIMIT",
            "quantity": str(request.quantity),
            "limit_price": f"{request.limit_price:.4f}",
            "option_strategy": "SINGLE",
            "side": "BUY",
            "time_in_force": "DAY",
            "entrust_type": "QTY",
            "legs": [
                {
                    "side": "BUY",
                    "quantity": str(request.quantity),
                    "symbol": request.ticker,
                    "strike_price": f"{request.contract_strike:g}",
                    "option_expire_date": request.contract_expiry.isoformat(),
                    "instrument_type": "OPTION",
                    "option_type": request.direction,
                    "market": "US",
                }
            ],
        }
    ]


def preview_sandbox_option_order(
    preview_request: OptionsBrokerPreviewRequest,
    options_config: OptionsManagerConfig,
    env: Mapping[str, str] | None = None,
    *,
    preview_client_factory: PreviewClientFactory | None = None,
) -> WebullSandboxPreviewResult:
    source = os.environ if env is None else env
    reason = _config_block_reason(source, options_config, require_real_preview=True)
    if reason:
        return WebullSandboxPreviewResult(
            preview_ready=False, status="BLOCKED", reason=reason
        )

    boundary = validate_preview_boundary(preview_request, options_config)
    if not boundary.preview_ready:
        return WebullSandboxPreviewResult(
            preview_ready=False,
            status="REJECTED",
            ticket_id=preview_request.ticket_id or None,
            reason=f"broker_boundary:{boundary.failed_stage}:{boundary.reason}",
        )

    key, secret = _credentials(source)
    factory = preview_client_factory or _OfficialPreviewClient
    try:
        client = factory(key, secret)
        account_response = client.get_account_list()
    except ImportError:
        return WebullSandboxPreviewResult(
            preview_ready=False, status="BLOCKED", reason="sdk_unavailable"
        )
    except Exception as exc:
        return WebullSandboxPreviewResult(
            preview_ready=False,
            status="ERROR",
            reason=f"account_request_failed:{type(exc).__name__}",
        )
    if int(getattr(account_response, "status_code", 0) or 0) != 200:
        return WebullSandboxPreviewResult(
            preview_ready=False, status="ERROR", reason="account_http_error"
        )
    try:
        account_payload = account_response.json()
    except Exception as exc:
        return WebullSandboxPreviewResult(
            preview_ready=False,
            status="ERROR",
            reason=f"account_invalid_json:{type(exc).__name__}",
        )

    account_id, select_reason = _select_individual_cash_account(account_payload)
    if select_reason:
        return WebullSandboxPreviewResult(
            preview_ready=False, status="ERROR", reason=select_reason
        )

    try:
        response = client.preview_option(account_id, _preview_orders(preview_request))
    except Exception as exc:
        return WebullSandboxPreviewResult(
            preview_ready=False,
            status="ERROR",
            ticket_id=preview_request.ticket_id,
            reason=f"preview_request_failed:{type(exc).__name__}",
        )

    if int(getattr(response, "status_code", 0) or 0) != 200:
        return WebullSandboxPreviewResult(
            preview_ready=False,
            status="REJECTED",
            ticket_id=preview_request.ticket_id,
            broker=WEBULL_SANDBOX_BROKER,
            reason=f"preview_http_{getattr(response, 'status_code', 'unknown')}",
        )

    try:
        payload = response.json()
    except Exception as exc:
        return WebullSandboxPreviewResult(
            preview_ready=False,
            status="ERROR",
            ticket_id=preview_request.ticket_id,
            broker=WEBULL_SANDBOX_BROKER,
            reason=f"preview_invalid_json:{type(exc).__name__}",
        )

    currency = _first_scalar(payload, ("currency", "currency_code"))
    estimated_cost = _as_float(
        _first_scalar(payload, ("estimated_cost", "estimated_notional", "order_value"))
    )
    estimated_fee = _as_float(
        _first_scalar(
            payload,
            ("estimated_transaction_fee", "estimated_fee", "transaction_fee"),
        )
    )
    return WebullSandboxPreviewResult(
        preview_ready=True,
        status="PREVIEW_READY",
        ticket_id=preview_request.ticket_id,
        broker=WEBULL_SANDBOX_BROKER,
        executable=False,
        submitted=False,
        broker_order_id=None,
        currency=str(currency) if currency is not None else None,
        estimated_cost=estimated_cost,
        estimated_transaction_fee=estimated_fee,
        warnings=(
            "sandbox_preview_only",
            "no_submission_capability_exposed",
            "submitted_false",
        ),
    )
