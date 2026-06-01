"""Alpaca paper options adapter (dormant, isolated from futures execution).

This module intentionally does not plug into webhook.runner._make_broker().
It is a separate options lane for future SPY/QQQ/etc. contracts while the
main futures system stays on PaperBroker/Tradovate.

Alpaca options use simple or multi-leg option order classes. Do not model
them as futures-style atomic brackets unless Alpaca explicitly supports that
for the order type being used.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from dotenv import load_dotenv

from execution.broker_interface import BrokerCapabilities
from risk.options_risk_engine import OptionTradePlan, OptionsDailyState, OptionsRiskEngine, OptionsRiskResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AlpacaOptionsConfig:
    api_key: str = ""
    secret_key: str = ""
    paper: bool = True
    enabled: bool = False
    timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls) -> "AlpacaOptionsConfig":
        load_dotenv()
        return cls(
            api_key=os.getenv("ALPACA_API_KEY", "").strip(),
            secret_key=os.getenv("ALPACA_SECRET_KEY", "").strip(),
            paper=os.getenv("ALPACA_PAPER", "true").strip().lower() not in {
                "0", "false", "no", "off"
            },
            enabled=os.getenv("ALPACA_OPTIONS_ENABLED", "false").strip().lower() in {
                "1", "true", "yes", "on"
            },
            timeout_seconds=float(os.getenv("ALPACA_TIMEOUT_SECONDS", "10")),
        )


@dataclass(frozen=True)
class OptionOrderRequest:
    """Single-leg options order request.

    symbol should be the Alpaca/OCC option contract symbol returned by
    get_option_contracts(), for example SPY260620C00600000.
    """

    symbol: str
    side: str  # BUY | SELL
    quantity: int = 1
    order_type: str = "market"  # market | limit
    limit_price: Optional[float] = None
    time_in_force: str = "day"
    client_order_id: Optional[str] = None


@dataclass(frozen=True)
class OptionOrderFill:
    broker: str
    symbol: str
    side: str
    quantity: int
    order_id: Optional[str]
    status: str
    submitted: bool
    filled_avg_price: Optional[float] = None
    reason: Optional[str] = None


class AlpacaOptionsBroker:
    """Dormant Alpaca paper-options client.

    This is not a BrokerInterface implementation because the current
    BrokerInterface is futures-bracket shaped. Keeping this adapter separate
    prevents accidental routing from the futures webhook path.
    """

    def __init__(
        self,
        config: AlpacaOptionsConfig | None = None,
        *,
        client: Any | None = None,
        client_cls: Any | None = None,
        auto_connect: bool = True,
    ):
        self.config = config or AlpacaOptionsConfig.from_env()
        self._client = client
        self._client_cls = client_cls
        if auto_connect and self._client is None:
            self._client = self._make_client()

    @property
    def is_live(self) -> bool:
        return not self.config.paper

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def connected(self) -> bool:
        return self._client is not None and self.configured

    @property
    def configured(self) -> bool:
        return bool(self.config.api_key and self.config.secret_key)

    def get_broker_name(self) -> str:
        return "AlpacaOptionsPaper" if self.config.paper else "AlpacaOptionsLive"

    def get_capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities(
            broker_name=self.get_broker_name(),
            asset_class="options",
            account_mode="paper" if self.config.paper else "live",
            starting_capital=None,
            available_cash=self.get_account_balance(),
            estimated_margin_required=None,
            max_dollars_risk_per_trade=None,
            supports_brackets=False,
            supports_options=True,
        )

    def health_check(self) -> dict[str, Any]:
        return {
            "broker": self.get_broker_name(),
            "enabled": self.enabled,
            "configured": self.configured,
            "connected": self.connected,
            "paper": self.config.paper,
            "supports_brackets": False,
        }

    def validate_plan(
        self,
        plan: OptionTradePlan,
        daily_state: OptionsDailyState | None = None,
        risk_engine: OptionsRiskEngine | None = None,
    ) -> OptionsRiskResult:
        engine = risk_engine or OptionsRiskEngine()
        return engine.validate(
            plan,
            daily_state or OptionsDailyState(),
            broker_is_live=self.is_live,
        )

    def submit_order(
        self,
        request: OptionOrderRequest,
        *,
        plan: OptionTradePlan | None = None,
        daily_state: OptionsDailyState | None = None,
        risk_engine: OptionsRiskEngine | None = None,
    ) -> OptionOrderFill:
        if plan is not None:
            risk = self.validate_plan(plan, daily_state, risk_engine)
            if risk.rejected:
                return self._rejected(request, risk.failed_rule or "OPTIONS_RISK_REJECTED")
        if not self.enabled:
            return self._rejected(request, "ALPACA_OPTIONS_DISABLED")
        if not self.connected:
            self._client = self._make_client()
        if self._client is None:
            return self._rejected(request, "ALPACA_CLIENT_UNAVAILABLE")

        try:
            order_req = self._to_alpaca_order_request(request)
            order = self._client.submit_order(order_req)
            return OptionOrderFill(
                broker=self.get_broker_name(),
                symbol=request.symbol,
                side=request.side.upper(),
                quantity=max(1, int(request.quantity or 1)),
                order_id=str(getattr(order, "id", "") or "") or None,
                status=str(getattr(order, "status", "submitted") or "submitted"),
                submitted=True,
                filled_avg_price=_maybe_float(getattr(order, "filled_avg_price", None)),
            )
        except Exception as exc:  # pragma: no cover - exact SDK errors vary
            logger.exception("Alpaca option order failed: %s", exc)
            return self._rejected(request, "ALPACA_ORDER_ERROR")

    def get_option_contracts(
        self,
        underlying: str,
        *,
        expiration_date: str | None = None,
        expiration_date_gte: str | None = None,
        expiration_date_lte: str | None = None,
        contract_type: str | None = None,
        strike_price_gte: str | None = None,
        strike_price_lte: str | None = None,
        limit: int = 100,
    ) -> list[Any]:
        if not self.enabled or not self.connected:
            return []
        try:
            from alpaca.trading.enums import AssetStatus, ContractType
            from alpaca.trading.requests import GetOptionContractsRequest

            kwargs: dict[str, Any] = {
                "underlying_symbols": [underlying.upper()],
                "status": AssetStatus.ACTIVE,
                "limit": int(limit),
            }
            if expiration_date:
                kwargs["expiration_date"] = expiration_date
            if expiration_date_gte:
                kwargs["expiration_date_gte"] = expiration_date_gte
            if expiration_date_lte:
                kwargs["expiration_date_lte"] = expiration_date_lte
            if strike_price_gte:
                kwargs["strike_price_gte"] = strike_price_gte
            if strike_price_lte:
                kwargs["strike_price_lte"] = strike_price_lte
            if contract_type:
                normalized = contract_type.lower()
                kwargs["type"] = ContractType.CALL if normalized == "call" else ContractType.PUT

            response = self._client.get_option_contracts(GetOptionContractsRequest(**kwargs))
            return list(getattr(response, "option_contracts", response) or [])
        except Exception as exc:  # pragma: no cover - exact SDK errors vary
            logger.warning("Alpaca option contract lookup failed: %s", exc)
            return []

    def get_account_balance(self) -> Optional[float]:
        if self._client is None:
            return None
        try:
            account = self._client.get_account()
            return _maybe_float(getattr(account, "cash", None))
        except Exception as exc:  # pragma: no cover - exact SDK errors vary
            logger.debug("Alpaca account lookup unavailable: %s", exc)
            return None

    def cancel_all(self) -> None:
        if self._client is None:
            return
        try:
            self._client.cancel_orders()
        except Exception as exc:  # pragma: no cover - exact SDK errors vary
            logger.warning("Alpaca cancel_all failed: %s", exc)

    def _make_client(self) -> Any | None:
        if not self.configured:
            logger.info("Alpaca options client not configured; missing API key/secret")
            return None
        try:
            if self._client_cls is not None:
                return self._client_cls(
                    self.config.api_key,
                    self.config.secret_key,
                    paper=self.config.paper,
                )
            from alpaca.trading.client import TradingClient

            return TradingClient(
                self.config.api_key,
                self.config.secret_key,
                paper=self.config.paper,
            )
        except Exception as exc:  # pragma: no cover - import/env dependent
            logger.warning(
                "alpaca-py is required for AlpacaOptionsBroker. Install with: pip install alpaca-py (%s)",
                exc,
            )
            return None

    @staticmethod
    def _to_alpaca_order_request(request: OptionOrderRequest) -> Any:
        from alpaca.trading.enums import OrderSide, OrderType, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

        side = OrderSide.BUY if request.side.upper() == "BUY" else OrderSide.SELL
        tif = TimeInForce.DAY if request.time_in_force.lower() == "day" else TimeInForce.GTC
        qty = max(1, int(request.quantity or 1))
        common = {
            "symbol": request.symbol,
            "qty": qty,
            "side": side,
            "time_in_force": tif,
        }
        if request.client_order_id:
            common["client_order_id"] = request.client_order_id

        if request.order_type.lower() == "limit":
            if request.limit_price is None:
                raise ValueError("limit_price is required for limit option orders")
            return LimitOrderRequest(limit_price=float(request.limit_price), **common)
        if request.order_type.lower() != "market":
            raise ValueError(f"Unsupported Alpaca option order_type: {request.order_type}")
        return MarketOrderRequest(type=OrderType.MARKET, **common)

    def _rejected(self, request: OptionOrderRequest, reason: str) -> OptionOrderFill:
        return OptionOrderFill(
            broker=self.get_broker_name(),
            symbol=request.symbol,
            side=request.side.upper(),
            quantity=max(1, int(request.quantity or 1)),
            order_id=None,
            status="rejected",
            submitted=False,
            reason=reason,
        )


def _maybe_float(value: Any) -> Optional[float]:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
