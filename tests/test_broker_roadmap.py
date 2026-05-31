"""
tests/test_broker_roadmap.py

Ensures broker roadmap and capital guardrails are explicit but inactive.
"""

from __future__ import annotations

import yaml

from config.settings import load_config
from execution.paper_broker import PaperBroker
from execution.tradovate_broker_stub import TradovateBrokerStub


def test_config_loads_small_account_guardrails():
    config = load_config()

    assert config.minimum_starting_capital == 500
    assert config.starting_capital_default == 5000
    assert config.max_account_risk_per_trade_percent == 1.0
    assert config.max_daily_loss_percent == 3.0
    assert config.require_margin_check is True
    assert config.broker_priority == ["paper", "tradovate_sim", "ibkr_paper"]
    assert config.live_trading_enabled is False


def test_risk_rules_contains_inactive_broker_roadmap():
    with open("risk_rules.yaml", encoding="utf-8") as handle:
        rules = yaml.safe_load(handle)

    assert rules["broker_roadmap"]["first_realistic_sim_path"] == "tradovate_sim"
    assert rules["broker_roadmap"]["future_multi_asset_path"] == "ibkr_paper"
    assert rules["capital_guardrails"]["minimum_starting_capital"] == 500
    assert rules["capital_guardrails"]["starting_capital_default"] == 5000
    assert rules["trading_mode"]["live_trading_enabled"] is False


def test_paper_broker_capabilities_are_safe():
    caps = PaperBroker().get_capabilities()

    assert caps.broker_name == "PaperBroker"
    assert caps.asset_class == "futures"
    assert caps.account_mode == "paper"
    assert caps.supports_brackets is True
    assert caps.supports_options is False
    assert caps.starting_capital == 5000.0
    assert caps.max_dollars_risk_per_trade == 10.0


def test_tradovate_stub_capabilities_are_sim_planning_only():
    stub = TradovateBrokerStub()
    caps = stub.get_capabilities()

    assert stub.is_live is True
    assert caps.broker_name.startswith("TradovateStub")
    assert caps.asset_class == "futures"
    assert caps.account_mode == "sim"
    assert caps.supports_brackets is True
    assert caps.supports_options is False
    assert caps.estimated_margin_required is None
    assert caps.max_dollars_risk_per_trade is None
