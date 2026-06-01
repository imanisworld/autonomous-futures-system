"""Tests for the dormant Alpaca options adapter."""

from __future__ import annotations

from types import SimpleNamespace

from execution.alpaca_options_broker import (
    AlpacaOptionsBroker,
    AlpacaOptionsConfig,
    OptionOrderRequest,
)


class FakeAlpacaClient:
    def __init__(self):
        self.orders = []
        self.cancelled = False
        self.account = SimpleNamespace(cash="25000.50")

    def submit_order(self, order_request):
        self.orders.append(order_request)
        return SimpleNamespace(id="order-123", status="accepted", filled_avg_price=None)

    def get_account(self):
        return self.account

    def cancel_orders(self):
        self.cancelled = True


def _config(enabled=True, paper=True):
    return AlpacaOptionsConfig(
        api_key="key",
        secret_key="secret",
        paper=paper,
        enabled=enabled,
    )


def test_alpaca_options_is_dormant_when_disabled():
    broker = AlpacaOptionsBroker(config=_config(enabled=False), client=FakeAlpacaClient())

    fill = broker.submit_order(OptionOrderRequest(symbol="SPY260620C00600000", side="BUY"))

    assert fill.submitted is False
    assert fill.reason == "ALPACA_OPTIONS_DISABLED"


def test_capabilities_are_options_not_brackets():
    broker = AlpacaOptionsBroker(config=_config(), client=FakeAlpacaClient())

    caps = broker.get_capabilities()

    assert broker.is_live is False
    assert caps.asset_class == "options"
    assert caps.account_mode == "paper"
    assert caps.supports_options is True
    assert caps.supports_brackets is False
    assert caps.available_cash == 25000.50


def test_live_flag_reflects_non_paper_config():
    broker = AlpacaOptionsBroker(config=_config(paper=False), client=FakeAlpacaClient())

    assert broker.is_live is True
    assert broker.get_broker_name() == "AlpacaOptionsLive"


def test_health_check_reports_safe_status():
    broker = AlpacaOptionsBroker(config=_config(), client=FakeAlpacaClient())

    health = broker.health_check()

    assert health["enabled"] is True
    assert health["configured"] is True
    assert health["connected"] is True
    assert health["supports_brackets"] is False


def test_missing_credentials_do_not_create_client():
    broker = AlpacaOptionsBroker(
        config=AlpacaOptionsConfig(enabled=True, api_key="", secret_key=""),
        auto_connect=True,
    )

    assert broker.connected is False


def test_cancel_all_delegates_to_client():
    client = FakeAlpacaClient()
    broker = AlpacaOptionsBroker(config=_config(), client=client)

    broker.cancel_all()

    assert client.cancelled is True


def test_submit_order_rejects_when_options_risk_fails():
    from datetime import datetime, timezone
    from risk.options_risk_engine import OptionTradePlan, OptionsDailyState, OptionsRiskConfig, OptionsRiskEngine

    client = FakeAlpacaClient()
    broker = AlpacaOptionsBroker(config=_config(), client=client)
    plan = OptionTradePlan(
        underlying="SPY",
        symbol="SPY260620C00600000",
        contract_type="CALL",
        side="BUY",
        quantity=1,
        entry_premium=1.00,
        stop_premium=None,
        target_premium=2.00,
        strategy="orb_reclaim",
        session="new_york",
        timestamp=datetime(2026, 5, 29, 14, 0, tzinfo=timezone.utc),
        order_type="limit",
        confluence_grade="A",
    )
    risk_engine = OptionsRiskEngine(OptionsRiskConfig(enabled=True))

    fill = broker.submit_order(
        OptionOrderRequest(symbol="SPY260620C00600000", side="BUY", order_type="limit", limit_price=1.0),
        plan=plan,
        daily_state=OptionsDailyState(),
        risk_engine=risk_engine,
    )

    assert fill.submitted is False
    assert fill.reason == "stop_required"
    assert client.orders == []
