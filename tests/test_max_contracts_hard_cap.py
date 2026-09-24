"""MAX_CONTRACTS_HARD_CAP fails closed: missing or invalid refuses, never None."""

from __future__ import annotations

import dataclasses

import pytest

from config.settings import ConfigError, load_config
from execution.broker_interface import BracketOrder
from execution.paper_broker import PaperBroker
from execution.tradovate_broker import TradovateBroker, TradovateConfig
from risk.risk_engine import DailyState, RiskEngine, TradeSetup


def _approved_setup(contracts: int) -> TradeSetup:
    return TradeSetup(
        direction="LONG",
        entry=19500.0,
        stop=19480.0,
        target=19540.0,
        rr_ratio=2.0,
        strategy="orb_reclaim",
        instrument="MNQ",
        session="new_york",
        contracts=contracts,
    )


@pytest.mark.parametrize(
    "raw",
    [None, "", "   ", "abc", "1.5", "+1", "0", "-1"],
)
def test_load_config_refuses_missing_or_invalid_cap(monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv("MAX_CONTRACTS_HARD_CAP", raising=False)
    else:
        monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", raw)
    with pytest.raises(ConfigError, match="MAX_CONTRACTS_HARD_CAP"):
        load_config("risk_rules.yaml")


def test_load_config_keeps_the_env_value_and_yaml_ceilings(monkeypatch):
    monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", "1")
    cfg = load_config("risk_rules.yaml")
    assert cfg.max_contracts_hard_cap == 1
    assert cfg.max_contracts_per_instrument["MNQ"] == 6
    assert cfg.max_contracts_per_instrument["MES"] == 6


def test_validate_rejects_missing_zero_and_negative_caps(config):
    state = DailyState()
    setup = _approved_setup(1)
    for cap in (None, 0, -1):
        engine = RiskEngine(config=dataclasses.replace(config, max_contracts_hard_cap=cap))
        result = engine.validate(setup, state)
        assert result.rejected
        assert result.failed_rule == "max_contracts_hard_cap_invalid"


def test_validate_rejects_quantity_above_cap_without_resizing(config):
    engine = RiskEngine(config=dataclasses.replace(config, max_contracts_hard_cap=1))
    result = engine.validate(_approved_setup(2), DailyState())
    assert result.rejected
    assert result.failed_rule == "max_contracts_hard_cap_exceeded"
    assert "2" in result.reason
    assert "1" in result.reason


def test_validate_allows_quantity_equal_to_cap(config):
    engine = RiskEngine(config=dataclasses.replace(config, max_contracts_hard_cap=1))
    assert engine.validate(_approved_setup(1), DailyState()).approved


def _order(contracts: int) -> BracketOrder:
    return BracketOrder(
        instrument="MNQ",
        direction="LONG",
        entry=100.0,
        stop=90.0,
        target=120.0,
        rr_ratio=2.0,
        strategy="orb_reclaim",
        contracts=contracts,
    )


def test_paper_broker_rejects_quantity_above_cap_without_resizing(monkeypatch):
    monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", "1")
    broker = PaperBroker()
    fill = broker.execute_bracket(_order(3))
    assert fill.result == "CANCELLED"
    assert fill.contracts == 3
    assert "exceed MAX_CONTRACTS_HARD_CAP=1" in fill.exit_reason
    assert broker._position is None


@pytest.mark.parametrize("raw", [None, "", "abc", "0", "-1"])
def test_paper_broker_rejects_invalid_cap(monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv("MAX_CONTRACTS_HARD_CAP", raising=False)
    else:
        monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", raw)
    broker = PaperBroker()
    fill = broker.execute_bracket(_order(1))
    assert fill.result == "CANCELLED"
    assert broker._position is None
    assert "MAX_CONTRACTS_HARD_CAP" in fill.exit_reason


def test_tradovate_refuses_over_cap_before_any_http(monkeypatch):
    monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", "1")
    broker = TradovateBroker(config=TradovateConfig(env="demo", expected_account_id=1))

    def _submitted(*_args, **_kwargs):
        raise AssertionError("broker submitted an order")

    broker._session.post = _submitted
    fill = broker.execute_bracket(_order(4))
    assert fill.result == "CANCELLED"
    assert fill.contracts == 4
    assert "exceed MAX_CONTRACTS_HARD_CAP=1" in fill.exit_reason
    assert broker._last_position is None
