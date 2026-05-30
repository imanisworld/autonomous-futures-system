"""IBKR paper-trading broker adapter.

Requires IB Gateway or TWS to be running and logged into the paper account.
The adapter never uses username/password directly; Gateway/TWS owns auth.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Optional

from dotenv import load_dotenv

from execution.broker_interface import (
    BrokerCapabilities,
    BrokerInterface,
    BracketOrder,
    Fill,
    Position,
)

logger = logging.getLogger(__name__)


def _ensure_event_loop() -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


@dataclass(frozen=True)
class IBKRConfig:
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 1
    connect_timeout: float = 8.0
    max_reconnect_attempts: int = 3
    base_backoff_seconds: float = 1.0
    account_mode: str = "paper"
    account: str = ""

    @classmethod
    def from_env(cls) -> "IBKRConfig":
        load_dotenv()
        return cls(
            host=os.getenv("IBKR_HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=int(os.getenv("IBKR_PORT", "7497")),
            client_id=int(os.getenv("IBKR_CLIENT_ID", "1")),
            connect_timeout=float(os.getenv("IBKR_CONNECT_TIMEOUT", "8")),
            max_reconnect_attempts=int(os.getenv("IBKR_MAX_RECONNECT_ATTEMPTS", "3")),
            base_backoff_seconds=float(os.getenv("IBKR_BASE_BACKOFF_SECONDS", "1")),
            account=os.getenv("IBKR_ACCOUNT", "").strip(),
        )


class IBKRBroker(BrokerInterface):
    """BrokerInterface implementation for IBKR paper trading via ib_insync."""

    FUTURE_EXCHANGES = {
        "MNQ": "CME",
        "MES": "CME",
        "MGC": "COMEX",
        "MCL": "NYMEX",
    }

    def __init__(
        self,
        config: IBKRConfig | None = None,
        *,
        ib: Any | None = None,
        ib_cls: Any | None = None,
        auto_connect: bool = True,
    ):
        self.config = config or IBKRConfig.from_env()
        self._ib = ib or self._make_ib(ib_cls)
        self._last_order_ids: list[int] = []
        self._last_position: Position | None = None
        if auto_connect:
            self.connect()

    @staticmethod
    def _make_ib(ib_cls: Any | None) -> Any:
        if ib_cls is not None:
            return ib_cls()
        try:
            _ensure_event_loop()
            from ib_insync import IB
        except Exception:  # pragma: no cover - env dependent
            logger.warning(
                "ib_insync is required for IBKRBroker. Install with: pip install ib_insync"
            )
            return None
        return IB()

    @property
    def is_live(self) -> bool:
        # This adapter is intentionally scoped to paper Gateway/TWS port 7497.
        return False

    @property
    def connected(self) -> bool:
        try:
            if self._ib is None:
                return False
            return bool(self._ib.isConnected())
        except Exception:
            return False

    def connect(self) -> bool:
        if self.connected:
            return True
        if self._ib is None:
            logger.error("IBKR client unavailable; install ib_insync to connect")
            return False
        delay = max(0.0, float(self.config.base_backoff_seconds))
        for attempt in range(1, self.config.max_reconnect_attempts + 1):
            try:
                logger.info(
                    "Connecting to IBKR Gateway/TWS paper endpoint %s:%s client_id=%s",
                    self.config.host,
                    self.config.port,
                    self.config.client_id,
                )
                self._ib.connect(
                    self.config.host,
                    self.config.port,
                    clientId=self.config.client_id,
                    timeout=self.config.connect_timeout,
                )
                if self.connected:
                    logger.info("Connected to IBKR paper trading endpoint")
                    return True
            except Exception as exc:  # pragma: no cover - exact API errors vary
                logger.warning("IBKR connect attempt %s failed: %s", attempt, exc)
            if attempt < self.config.max_reconnect_attempts:
                if delay:
                    time.sleep(delay)
                delay = delay * 2 if delay else 0.0
        logger.error("Unable to connect to IBKR Gateway/TWS after retries")
        return False

    def execute_bracket(self, order: BracketOrder) -> Fill:
        try:
            if not self.connected and not self.connect():
                return self._cancelled_fill(order, "IBKR_NOT_CONNECTED")

            contract = self._contract_for(order.instrument)
            qualified = self._ib.qualifyContracts(contract)
            if qualified:
                contract = qualified[0]

            action = "BUY" if order.direction == "LONG" else "SELL"
            quantity = max(1, int(order.contracts or 1))
            stop_price = float(order.stop)
            target_price = float(order.target)

            bracket = self._ib.bracketOrder(
                action,
                quantity,
                float(order.entry),
                target_price,
                stop_price,
            )
            # Keep the parent as a market order while still using ib.bracketOrder
            # for the linked take-profit and stop-loss order structure.
            parent = bracket[0]
            parent.orderType = "MKT"
            if hasattr(parent, "lmtPrice"):
                parent.lmtPrice = 0

            self._last_order_ids = []
            trades = []
            for ib_order in bracket:
                if self.config.account and hasattr(ib_order, "account"):
                    ib_order.account = self.config.account
                trade = self._ib.placeOrder(contract, ib_order)
                trades.append(trade)
                order_id = getattr(ib_order, "orderId", None)
                if order_id is not None:
                    self._last_order_ids.append(order_id)

            self._last_position = Position(
                instrument=order.instrument,
                direction=order.direction,
                entry_price=order.entry,
                stop=order.stop,
                target=order.target,
                quantity=quantity,
                open=True,
            )
            logger.info("Submitted IBKR paper bracket order: %s", order)
            fill_price = self._avg_fill_price(trades) or order.entry
            return Fill(
                instrument=order.instrument,
                direction=order.direction,
                contracts=quantity,
                entry_price=fill_price,
                exit_price=None,
                exit_reason=None,
                result="OPEN",
                pnl_ticks=None,
                pnl_dollars=None,
            )
        except Exception as exc:  # pragma: no cover - exact API errors vary
            logger.exception("IBKR bracket order failed: %s", exc)
            return self._cancelled_fill(order, "IBKR_ORDER_ERROR")

    def get_position(self) -> Optional[Position]:
        try:
            if not self.connected:
                return self._last_position if self._last_position and self._last_position.open else None
            positions = self._ib.positions()
            for ib_pos in positions:
                pos = self._position_from_ib(ib_pos)
                if pos is not None:
                    self._last_position = pos
                    return pos
        except Exception as exc:  # pragma: no cover - exact API errors vary
            logger.warning("IBKR position lookup failed: %s", exc)
        return self._last_position if self._last_position and self._last_position.open else None

    def cancel_all(self) -> None:
        try:
            if self.connected:
                if hasattr(self._ib, "reqGlobalCancel"):
                    self._ib.reqGlobalCancel()
                for trade in list(self._ib.openTrades()):
                    self._ib.cancelOrder(trade.order)
        except Exception as exc:  # pragma: no cover - exact API errors vary
            logger.warning("IBKR cancel_all failed: %s", exc)
        self._last_position = None
        self._last_order_ids = []

    def get_broker_name(self) -> str:
        return "IBKRBrokerPaper"

    def get_capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities(
            broker_name=self.get_broker_name(),
            asset_class="futures",
            account_mode="paper",
            starting_capital=1_000_000.0,
            available_cash=self._available_cash(),
            estimated_margin_required=None,
            max_dollars_risk_per_trade=None,
            supports_brackets=True,
            supports_options=False,
        )

    def _contract_for(self, instrument: str) -> Any:
        try:
            _ensure_event_loop()
            from ib_insync import Future
        except Exception:  # pragma: no cover - mocked/unit-test path
            Future = None
        exchange = os.getenv(
            f"IBKR_{instrument.upper()}_EXCHANGE",
            self.FUTURE_EXCHANGES.get(instrument.upper(), "CME"),
        ).strip()
        contract_month = (
            os.getenv(f"IBKR_{instrument.upper()}_CONTRACT_MONTH", "").strip()
            or os.getenv("IBKR_CONTRACT_MONTH", "").strip()
        )
        if Future is None:
            return SimpleNamespace(
                symbol=instrument.upper(),
                lastTradeDateOrContractMonth=contract_month,
                exchange=exchange,
                currency="USD",
            )
        return Future(
            symbol=instrument.upper(),
            lastTradeDateOrContractMonth=contract_month,
            exchange=exchange,
            currency="USD",
        )

    @staticmethod
    def _avg_fill_price(trades: list[Any]) -> float | None:
        prices: list[float] = []
        for trade in trades:
            fills = getattr(trade, "fills", []) or []
            for fill in fills:
                execution = getattr(fill, "execution", None)
                price = getattr(execution, "price", None)
                if price is not None:
                    prices.append(float(price))
        if not prices:
            return None
        return round(sum(prices) / len(prices), 4)

    def _position_from_ib(self, ib_pos: Any) -> Position | None:
        quantity = float(getattr(ib_pos, "position", 0) or 0)
        if quantity == 0:
            return None
        contract = getattr(ib_pos, "contract", None)
        instrument = getattr(contract, "symbol", "UNKNOWN")
        direction = "LONG" if quantity > 0 else "SHORT"
        avg_cost = float(getattr(ib_pos, "avgCost", 0) or 0)
        return Position(
            instrument=instrument,
            direction=direction,
            entry_price=avg_cost,
            stop=0.0,
            target=0.0,
            quantity=abs(int(quantity)),
            open=True,
        )

    def _available_cash(self) -> float | None:
        try:
            if not self.connected:
                return None
            for item in self._ib.accountSummary():
                if getattr(item, "tag", "") in {"AvailableFunds", "TotalCashValue"}:
                    return float(item.value)
        except Exception as exc:  # pragma: no cover - exact API errors vary
            logger.debug("IBKR account summary unavailable: %s", exc)
        return None

    @staticmethod
    def _cancelled_fill(order: BracketOrder, reason: str) -> Fill:
        return Fill(
            instrument=order.instrument,
            direction=order.direction,
            contracts=max(1, int(order.contracts or 1)),
            entry_price=order.entry,
            exit_price=None,
            exit_reason=reason,
            result="CANCELLED",
            pnl_ticks=None,
            pnl_dollars=None,
        )
