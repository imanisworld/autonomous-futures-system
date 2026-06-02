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
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any, Callable, Optional


# ── Contract auto-roll ────────────────────────────────────────────────────────
# CME micro futures (MES, MNQ, MGC, MCL) expire on the 3rd Friday of each
# quarter month (Mar/Jun/Sep/Dec).  We roll ROLL_DAYS_BEFORE expiry so the
# bot never tries to trade an expired contract.
_QUARTER_MONTHS = (3, 6, 9, 12)
_ROLL_DAYS_BEFORE = 5


def _third_friday(year: int, month: int) -> date:
    """Return the 3rd Friday of the given month."""
    first = date(year, month, 1)
    days_to_fri = (4 - first.weekday()) % 7   # 4 = Friday
    return first + timedelta(days=days_to_fri + 14)


def front_month_contract(today: date | None = None) -> str:
    """
    Return YYYYMM for the active front-month quarterly contract.
    Rolls _ROLL_DAYS_BEFORE days before the 3rd-Friday expiry.
    The IBKR_CONTRACT_MONTH env var overrides this when set.
    """
    override = os.getenv("IBKR_CONTRACT_MONTH", "").strip()
    if override:
        return override

    d = today or date.today()
    for month in _QUARTER_MONTHS:
        if d.month <= month:
            exp = _third_friday(d.year, month)
            if d < exp - timedelta(days=_ROLL_DAYS_BEFORE):
                return f"{d.year}{month:02d}"
            # Within roll window — step to next quarter
            idx = _QUARTER_MONTHS.index(month)
            if idx + 1 < len(_QUARTER_MONTHS):
                nm = _QUARTER_MONTHS[idx + 1]
                return f"{d.year}{nm:02d}"
            return f"{d.year + 1}{_QUARTER_MONTHS[0]:02d}"
    # d.month > 12 is impossible, but handle year-end roll
    exp = _third_friday(d.year, 12)
    if d < exp - timedelta(days=_ROLL_DAYS_BEFORE):
        return f"{d.year}12"
    return f"{d.year + 1}{_QUARTER_MONTHS[0]:02d}"

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
    """Ensure a usable event loop exists; never replaces one that ib_insync is already using."""
    try:
        asyncio.get_running_loop()
        return  # loop is active — leave it alone
    except RuntimeError:
        pass
    try:
        loop = asyncio.get_event_loop()
        if not loop.is_closed():
            return  # a loop is already set and open — reuse it
    except RuntimeError:
        pass
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
    auto_resubscribe_on_reconnect: bool = True

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
            auto_resubscribe_on_reconnect=os.getenv(
                "IBKR_AUTO_RESUBSCRIBE_ON_RECONNECT", "true"
            ).strip().lower() not in {"0", "false", "no", "off"},
        )


class IBKRBroker(BrokerInterface):
    """BrokerInterface implementation for IBKR paper trading via ib_insync."""

    FUTURE_EXCHANGES = {
        "MNQ": "CME",
        "MES": "CME",
        "ES": "CME",
        "NQ": "CME",
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
        status_callback: Callable[[str], None] | None = None,
    ):
        self.config = config or IBKRConfig.from_env()
        self._ib = ib or self._make_ib(ib_cls)
        self._last_order_ids: list[int] = []
        self._last_position: Position | None = None
        self._last_error_code: int | None = None
        self._last_error_message: str | None = None
        self._resubscribe_count = 0
        self._status_callback = status_callback
        self._subscribe_error_events()
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

    def get_account_balance(self) -> Optional[float]:
        return self._available_cash()

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

    def resolve_position(self) -> Fill | None:
        """Query IBKR for fills on the child stop/target orders of the open bracket.

        Returns a Fill if the position was closed (stop or target hit), or None
        if the position is still open. This is the IBKR equivalent of PaperBroker's
        NextBarOHLC simulation — instead of simulating, we ask IBKR what happened.

        Called once per incoming webhook bar when BROKER=ibkr and a position is open.
        """
        if not self._last_position or not self._last_position.open:
            return None

        try:
            if not self.connected and not self.connect():
                logger.warning("IBKR resolve_position: not connected, cannot check fills")
                return None

            # Check whether any of our tracked child orders have been filled
            closed_trade = self._find_closed_child_trade()
            if closed_trade is None:
                # Also check live positions — if flat, the order closed outside our tracking
                if not self._ibkr_has_open_position():
                    return self._synthesize_fill_from_account()
                return None

            return closed_trade

        except Exception as exc:  # pragma: no cover - API errors vary
            logger.warning("IBKR resolve_position failed: %s", exc)
            return None

    def _find_closed_child_trade(self) -> Fill | None:
        """Scan recent fills for a filled child order (stop or target) from our bracket."""
        if not self._last_order_ids:
            return None
        try:
            trades = self._ib.trades() if hasattr(self._ib, "trades") else []
            for trade in trades:
                order = getattr(trade, "order", None)
                order_id = getattr(order, "orderId", None)
                if order_id not in self._last_order_ids:
                    continue
                order_type = getattr(order, "orderType", "").upper()
                status = getattr(getattr(trade, "orderStatus", None), "status", "")
                if status != "Filled":
                    continue
                fills = getattr(trade, "fills", []) or []
                if not fills:
                    continue
                avg_price = self._avg_fill_price([trade]) or (
                    self._last_position.entry_price if self._last_position else 0.0
                )
                is_stop = order_type in ("STP", "STOP", "TRAIL", "TRAILLMT")
                is_target = order_type in ("LMT", "LIMIT")
                if not is_stop and not is_target:
                    continue
                return self._build_fill_from_close(avg_price, is_stop=is_stop)
        except Exception as exc:  # pragma: no cover
            logger.debug("IBKR _find_closed_child_trade error: %s", exc)
        return None

    def _ibkr_has_open_position(self) -> bool:
        """Return True if IBKR reports a non-zero position for our instrument."""
        if self._last_position is None:
            return False
        try:
            for pos in self._ib.positions():
                contract = getattr(pos, "contract", None)
                symbol = getattr(contract, "symbol", "")
                qty = float(getattr(pos, "position", 0) or 0)
                if symbol.upper() == self._last_position.instrument.upper() and qty != 0:
                    return True
        except Exception as exc:  # pragma: no cover
            logger.debug("IBKR position check error: %s", exc)
        return False

    def _synthesize_fill_from_account(self) -> Fill | None:
        """
        Position closed externally (manual close, margin call, etc.).
        Best-effort fallback when no order fill record is available.
        Conservatively marks as LOSS since we can't determine the outcome.
        """
        if self._last_position is None:
            return None
        pos = self._last_position  # capture before clearing
        logger.warning(
            "IBKR position closed outside bracket tracking for %s — synthesising fill",
            pos.instrument,
        )
        self._last_position = None
        self._last_order_ids = []
        return Fill(
            instrument=pos.instrument,
            direction=pos.direction,
            contracts=pos.quantity,
            entry_price=pos.entry_price,
            exit_price=None,
            exit_reason="EXTERNAL_CLOSE",
            result="LOSS",
            pnl_ticks=None,
            pnl_dollars=None,
        )

    def _build_fill_from_close(self, exit_price: float, *, is_stop: bool) -> Fill | None:
        """Build a Fill from a known exit price and order type."""
        if self._last_position is None:
            return None
        pos = self._last_position
        tick_size = 0.25
        tick_values = {"MES": 1.25, "ES": 12.50, "MNQ": 0.50, "NQ": 5.00, "MGC": 1.00, "MCL": 1.00}
        tick_value = tick_values.get(pos.instrument.upper().rstrip("!1234567890HMUZ"), 1.25)

        raw_ticks = (exit_price - pos.entry_price) / tick_size
        signed_ticks = raw_ticks if pos.direction == "LONG" else -raw_ticks
        pnl_dollars = round(signed_ticks * tick_value * pos.quantity, 2)

        if signed_ticks > 0:
            result = "WIN"
        elif signed_ticks < 0:
            result = "LOSS"
        else:
            result = "BREAKEVEN"

        exit_reason = "STOP_HIT" if is_stop else "TARGET_HIT"

        # Clear tracking state
        self._last_position = None
        self._last_order_ids = []

        return Fill(
            instrument=pos.instrument,
            direction=pos.direction,
            contracts=pos.quantity,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            exit_reason=exit_reason,
            result=result,
            pnl_ticks=round(signed_ticks, 2),
            pnl_dollars=pnl_dollars,
        )

    def sync_open_position(self) -> Position | None:
        """Query IBKR for any open position and update internal state.

        Called at startup to reconcile journal state with what IBKR actually holds.
        Returns the Position if one exists, None if flat.
        """
        try:
            if not self.connected and not self.connect():
                return None
            for ib_pos in self._ib.positions():
                pos = self._position_from_ib(ib_pos)
                if pos is not None:
                    self._last_position = pos
                    logger.info(
                        "IBKR sync: found open position %s %s qty=%s avg=%.2f",
                        pos.direction, pos.instrument, pos.quantity, pos.entry_price,
                    )
                    return pos
            # Flat on IBKR side
            self._last_position = None
            logger.info("IBKR sync: no open positions")
        except Exception as exc:  # pragma: no cover
            logger.warning("IBKR sync_open_position failed: %s", exc)
        return None

    def health_check(self) -> dict[str, Any]:
        """Return a lightweight local status snapshot for overnight monitoring."""
        return {
            "broker": self.get_broker_name(),
            "connected": self.connected,
            "account_balance": self.get_account_balance(),
            "last_error_code": self._last_error_code,
            "last_error_message": self._last_error_message,
            "resubscribe_count": self._resubscribe_count,
        }

    def resubscribe_all(self) -> None:
        """Refresh IB state after connectivity restores."""
        if not self.connected:
            logger.warning("IBKR resubscribe skipped; client is not connected")
            return
        try:
            self._ib.positions()
            self._ib.accountSummary()
            if hasattr(self._ib, "openTrades"):
                self._ib.openTrades()
            self._resubscribe_count += 1
            logger.info("IBKR resubscribe complete")
        except Exception as exc:  # pragma: no cover - exact API errors vary
            logger.warning("IBKR resubscribe failed: %s", exc)

    def _subscribe_error_events(self) -> None:
        if self._ib is None:
            return
        event = getattr(self._ib, "errorEvent", None)
        if event is None:
            return
        try:
            event += self._on_ib_error
        except Exception as exc:  # pragma: no cover - fake/event variants differ
            logger.debug("Unable to subscribe to IBKR errorEvent: %s", exc)

    def _on_ib_error(
        self,
        reqId: int,
        errorCode: int,
        errorString: str,
        contract: Any | None = None,
    ) -> None:
        self._last_error_code = int(errorCode)
        self._last_error_message = str(errorString)
        if errorCode == 1100:
            logger.warning("IBKR disconnected: %s", errorString)
            self._emit_status(f"IBKR disconnected (1100): {errorString}")
            return
        if errorCode == 1102:
            logger.info("IBKR reconnected; resubscribing state: %s", errorString)
            if self.config.auto_resubscribe_on_reconnect:
                self.resubscribe_all()
            self._emit_status(f"IBKR reconnected (1102): {errorString}")
            return
        if errorCode == 1101:
            logger.info("IBKR data connection restored; resubscribing state: %s", errorString)
            if self.config.auto_resubscribe_on_reconnect:
                self.resubscribe_all()
            self._emit_status(f"IBKR data restored (1101): {errorString}")
            return
        logger.debug("IBKR API message %s reqId=%s: %s", errorCode, reqId, errorString)

    def _emit_status(self, message: str) -> None:
        if self._status_callback is None:
            return
        try:
            self._status_callback(message)
        except Exception as exc:  # pragma: no cover - callbacks should not break trading
            logger.warning("IBKR status callback failed: %s", exc)

    def _contract_for(self, instrument: str) -> Any:
        try:
            from ib_insync import Future
        except Exception:  # pragma: no cover - mocked/unit-test path
            Future = None
        exchange = os.getenv(
            f"IBKR_{instrument.upper()}_EXCHANGE",
            self.FUTURE_EXCHANGES.get(instrument.upper(), "CME"),
        ).strip()
        # Per-instrument override → global override → auto-computed front month
        contract_month = (
            os.getenv(f"IBKR_{instrument.upper()}_CONTRACT_MONTH", "").strip()
            or front_month_contract()
        )
        logger.debug("Contract month for %s: %s", instrument, contract_month)
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
        """Return account equity suitable for position sizing.

        Priority: NetLiquidation → TotalCashValue → AvailableFunds.
        NetLiquidation is the correct balance basis for paper Gateway because
        AvailableFunds excludes margin already in use and understates the
        account value when a position is open.
        """
        try:
            if not self.connected:
                return None
            summary = {
                getattr(item, "tag", ""): item
                for item in self._ib.accountSummary()
            }
            for tag in ("NetLiquidation", "TotalCashValue", "AvailableFunds"):
                item = summary.get(tag)
                if item is not None:
                    try:
                        return float(item.value)
                    except (TypeError, ValueError):
                        continue
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
