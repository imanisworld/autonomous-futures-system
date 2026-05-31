"""Tests for the IBKR paper broker adapter."""

from __future__ import annotations

import os
import socket
from types import SimpleNamespace

import pytest

from execution.broker_interface import BracketOrder
from execution.ibkr_broker import IBKRBroker, IBKRConfig


class FakeOrder:
    def __init__(self, order_id: int, order_type: str = "LMT"):
        self.orderId = order_id
        self.orderType = order_type
        self.lmtPrice = 0.0
        self.account = ""


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def emit(self, *args):
        for handler in list(self.handlers):
            handler(*args)


class FakeIB:
    def __init__(self, connected: bool = True):
        self._connected = connected
        self.connect_calls = []
        self.placed_orders = []
        self.cancelled_orders = []
        self.global_cancel_called = False
        self._positions = []
        self._open_trades = []
        self._account_summary = []
        self.bracket = [FakeOrder(101), FakeOrder(102), FakeOrder(103)]
        self.errorEvent = FakeEvent()
        self.positions_calls = 0
        self.account_summary_calls = 0
        self.open_trades_calls = 0

    def isConnected(self):
        return self._connected

    def connect(self, host, port, clientId, timeout):
        self.connect_calls.append((host, port, clientId, timeout))
        self._connected = True

    def qualifyContracts(self, contract):
        return [contract]

    def bracketOrder(self, action, quantity, limitPrice, takeProfitPrice, stopLossPrice):
        self.bracket_args = (action, quantity, limitPrice, takeProfitPrice, stopLossPrice)
        return self.bracket

    def placeOrder(self, contract, order):
        self.placed_orders.append((contract, order))
        return SimpleNamespace(fills=[])

    def positions(self):
        self.positions_calls += 1
        return self._positions

    def openTrades(self):
        self.open_trades_calls += 1
        return self._open_trades

    def cancelOrder(self, order):
        self.cancelled_orders.append(order)

    def reqGlobalCancel(self):
        self.global_cancel_called = True

    def accountSummary(self):
        self.account_summary_calls += 1
        return self._account_summary


@pytest.fixture
def config():
    return IBKRConfig(max_reconnect_attempts=1, base_backoff_seconds=0, account="DU123456")


@pytest.fixture
def long_order():
    return BracketOrder(
        instrument="MNQ",
        direction="LONG",
        entry=19500.0,
        stop=19480.0,
        target=19540.0,
        rr_ratio=2.0,
        strategy="orb_reclaim",
        contracts=2,
    )


def test_ibkr_broker_is_paper_not_live(config):
    broker = IBKRBroker(config=config, ib=FakeIB(), auto_connect=False)
    assert broker.is_live is False
    assert broker.get_broker_name() == "IBKRBrokerPaper"


def test_capabilities_report_paper_futures_brackets(config):
    fake_ib = FakeIB()
    fake_ib._account_summary = [SimpleNamespace(tag="AvailableFunds", value="999000.50")]
    broker = IBKRBroker(config=config, ib=fake_ib, auto_connect=False)

    caps = broker.get_capabilities()

    assert caps.asset_class == "futures"
    assert caps.account_mode == "paper"
    assert caps.starting_capital == 1_000_000.0
    assert caps.available_cash == 999000.50
    assert caps.supports_brackets is True
    assert caps.supports_options is False


def test_execute_bracket_places_three_linked_orders(config, long_order):
    fake_ib = FakeIB()
    broker = IBKRBroker(config=config, ib=fake_ib, auto_connect=False)

    fill = broker.execute_bracket(long_order)

    assert fill.result == "OPEN"
    assert fill.instrument == "MNQ"
    assert fill.contracts == 2
    assert fake_ib.bracket_args == ("BUY", 2, 19500.0, 19540.0, 19480.0)
    assert len(fake_ib.placed_orders) == 3
    assert fake_ib.bracket[0].orderType == "MKT"
    assert all(order.account == "DU123456" for _, order in fake_ib.placed_orders)


def test_short_bracket_uses_sell_action(config):
    order = BracketOrder(
        instrument="MES",
        direction="SHORT",
        entry=5300.0,
        stop=5304.0,
        target=5292.0,
        rr_ratio=2.0,
        strategy="vwap_reject",
    )
    fake_ib = FakeIB()
    broker = IBKRBroker(config=config, ib=fake_ib, auto_connect=False)

    fill = broker.execute_bracket(order)

    assert fill.result == "OPEN"
    assert fake_ib.bracket_args[0] == "SELL"


def test_execute_bracket_returns_cancelled_when_not_connected(config, long_order):
    fake_ib = FakeIB(connected=False)
    fake_ib.connect = lambda *args, **kwargs: None
    broker = IBKRBroker(config=config, ib=fake_ib, auto_connect=False)

    fill = broker.execute_bracket(long_order)

    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "IBKR_NOT_CONNECTED"


def test_missing_ib_insync_does_not_crash_main_loop(monkeypatch, long_order):
    monkeypatch.setattr(IBKRBroker, "_make_ib", staticmethod(lambda ib_cls: None))
    config = IBKRConfig(max_reconnect_attempts=1, base_backoff_seconds=0)
    broker = IBKRBroker(config=config, auto_connect=False)

    fill = broker.execute_bracket(long_order)

    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "IBKR_NOT_CONNECTED"


def test_get_position_converts_ib_position(config, long_order):
    fake_ib = FakeIB()
    fake_ib._positions = [
        SimpleNamespace(
            contract=SimpleNamespace(symbol="MNQ"),
            position=2,
            avgCost=19501.25,
        )
    ]
    broker = IBKRBroker(config=config, ib=fake_ib, auto_connect=False)

    pos = broker.get_position()

    assert pos is not None
    assert pos.instrument == "MNQ"
    assert pos.direction == "LONG"
    assert pos.entry_price == 19501.25
    assert pos.quantity == 2


def test_cancel_all_calls_global_cancel_and_clears_position(config, long_order):
    fake_ib = FakeIB()
    fake_order = FakeOrder(201)
    fake_ib._open_trades = [SimpleNamespace(order=fake_order)]
    broker = IBKRBroker(config=config, ib=fake_ib, auto_connect=False)
    broker.execute_bracket(long_order)

    broker.cancel_all()

    assert fake_ib.global_cancel_called is True
    assert fake_ib.cancelled_orders == [fake_order]
    assert broker.get_position() is None



def test_ibkr_error_1100_records_disconnect_and_notifies(config):
    fake_ib = FakeIB()
    messages = []
    broker = IBKRBroker(config=config, ib=fake_ib, auto_connect=False, status_callback=messages.append)

    fake_ib.errorEvent.emit(-1, 1100, "Connectivity between IB and TWS has been lost")

    health = broker.health_check()
    assert health["last_error_code"] == 1100
    assert "lost" in health["last_error_message"]
    assert messages == ["IBKR disconnected (1100): Connectivity between IB and TWS has been lost"]
    assert fake_ib.positions_calls == 0


def test_ibkr_error_1102_resubscribes_and_notifies(config):
    fake_ib = FakeIB()
    messages = []
    broker = IBKRBroker(config=config, ib=fake_ib, auto_connect=False, status_callback=messages.append)

    fake_ib.errorEvent.emit(-1, 1102, "Connectivity restored - data maintained")

    health = broker.health_check()
    assert health["last_error_code"] == 1102
    assert health["resubscribe_count"] == 1
    assert fake_ib.positions_calls == 1
    assert fake_ib.account_summary_calls >= 1
    assert fake_ib.open_trades_calls == 1
    assert messages == ["IBKR reconnected (1102): Connectivity restored - data maintained"]


def test_ibkr_error_1101_resubscribes_when_data_lost(config):
    fake_ib = FakeIB()
    broker = IBKRBroker(config=config, ib=fake_ib, auto_connect=False)

    fake_ib.errorEvent.emit(-1, 1101, "Connectivity restored - data lost")

    assert broker.health_check()["resubscribe_count"] == 1

def test_connect_to_gateway_and_place_paper_bracket_when_opted_in(long_order):
    if os.getenv("IBKR_RUN_INTEGRATION_TESTS", "false").lower() not in {"1", "true", "yes"}:
        pytest.skip("Set IBKR_RUN_INTEGRATION_TESTS=true to run against local IB Gateway/TWS")

    host = os.getenv("IBKR_HOST", "127.0.0.1")
    port = int(os.getenv("IBKR_PORT", "7497"))
    with socket.socket() as sock:
        sock.settimeout(0.5)
        if sock.connect_ex((host, port)) != 0:
            pytest.skip("IB Gateway/TWS is not listening on the configured paper port")

    pytest.importorskip("ib_insync")
    broker = IBKRBroker(config=IBKRConfig.from_env())
    fill = broker.execute_bracket(long_order)
    try:
        assert fill.result in {"OPEN", "CANCELLED"}
    finally:
        broker.cancel_all()
