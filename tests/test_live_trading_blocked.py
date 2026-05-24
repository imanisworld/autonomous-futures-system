"""
tests/test_live_trading_blocked.py

Proves that live trading cannot run in Phase 1 under any circumstance.

These tests are the safety net. If any of them fail, something has
gone wrong with the architecture and the system must not be deployed.
"""

from __future__ import annotations

import os
import pytest

from config.settings import load_config, LiveTradingBlockedError, SystemConfig
from execution.paper_broker import PaperBroker
from execution.tradovate_broker_stub import TradovateBrokerStub
from execution.broker_interface import BracketOrder
from risk.risk_engine import RiskEngine, DailyState, TradeSetup


# ─── Config-level blocks ──────────────────────────────────────────────────────

class TestLiveTradingConfigBlocked:
    """Live trading is blocked at the config layer."""

    def test_load_config_raises_if_env_live_true(self, tmp_path, monkeypatch):
        """Setting LIVE_TRADING_ENABLED=true in env raises LiveTradingBlockedError."""
        monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")

        # Create a minimal valid risk_rules.yaml
        rules = tmp_path / "risk_rules.yaml"
        rules.write_text(_minimal_rules_yaml(live=False))

        with pytest.raises(LiveTradingBlockedError):
            load_config(str(rules))

    def test_load_config_raises_if_yaml_live_true(self, tmp_path, monkeypatch):
        """Setting live_trading_enabled: true in YAML raises LiveTradingBlockedError."""
        monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")

        rules = tmp_path / "risk_rules.yaml"
        rules.write_text(_minimal_rules_yaml(live=True))

        with pytest.raises(LiveTradingBlockedError):
            load_config(str(rules))

    def test_load_config_raises_if_env_is_1(self, tmp_path, monkeypatch):
        """LIVE_TRADING_ENABLED=1 (truthy) also raises."""
        monkeypatch.setenv("LIVE_TRADING_ENABLED", "1")
        rules = tmp_path / "risk_rules.yaml"
        rules.write_text(_minimal_rules_yaml(live=False))

        with pytest.raises(LiveTradingBlockedError):
            load_config(str(rules))

    def test_config_live_flag_always_false_after_load(self, tmp_path, monkeypatch):
        """Config object always has live_trading_enabled=False after successful load."""
        monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
        rules = tmp_path / "risk_rules.yaml"
        rules.write_text(_minimal_rules_yaml(live=False))

        config = load_config(str(rules))
        assert config.live_trading_enabled is False

    def test_paper_mode_is_true_by_default(self, tmp_path, monkeypatch):
        """paper_mode is always True after successful load."""
        monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
        rules = tmp_path / "risk_rules.yaml"
        rules.write_text(_minimal_rules_yaml(live=False))

        config = load_config(str(rules))
        assert config.paper_mode is True


# ─── Broker-level blocks ──────────────────────────────────────────────────────

class TestPaperBrokerIsNotLive:
    """PaperBroker correctly identifies as non-live."""

    def test_paper_broker_is_not_live(self):
        broker = PaperBroker()
        assert broker.is_live is False

    def test_paper_broker_works_without_credentials(self):
        """Paper broker runs with no env variables set — no crash."""
        for var in ["TRADOVATE_USERNAME", "TRADOVATE_PASSWORD", "LIVE_TRADING_ENABLED"]:
            if var in os.environ:
                del os.environ[var]

        broker = PaperBroker()
        assert broker.is_live is False
        assert broker.get_position() is None

    def test_paper_broker_executes_bracket_without_credentials(self):
        """Paper broker executes bracket orders without any broker credentials."""
        broker = PaperBroker()
        order = BracketOrder(
            instrument="MNQ",
            direction="LONG",
            entry=19500.0,
            stop=19480.0,
            target=19540.0,
            rr_ratio=2.0,
            strategy="orb_reclaim",
        )
        fill = broker.execute_bracket(order)
        assert fill.result in ("OPEN", "WIN", "LOSS")
        assert fill.entry_price == 19500.0


class TestTradovateStubIsLiveAndBlocked:
    """TradovateBrokerStub correctly identifies as live and is blocked."""

    def test_tradovate_stub_is_live(self):
        """Stub correctly reports is_live=True."""
        stub = TradovateBrokerStub()
        assert stub.is_live is True

    def test_tradovate_stub_execute_raises_not_implemented(self):
        """execute_bracket raises NotImplementedError in Phase 1."""
        stub = TradovateBrokerStub()
        order = BracketOrder(
            instrument="MNQ",
            direction="LONG",
            entry=19500.0,
            stop=19480.0,
            target=19540.0,
            rr_ratio=2.0,
            strategy="orb_reclaim",
        )
        with pytest.raises(NotImplementedError):
            stub.execute_bracket(order)

    def test_tradovate_stub_get_position_raises(self):
        stub = TradovateBrokerStub()
        with pytest.raises(NotImplementedError):
            stub.get_position()

    def test_tradovate_stub_cancel_raises(self):
        stub = TradovateBrokerStub()
        with pytest.raises(NotImplementedError):
            stub.cancel_all()

    def test_tradovate_stub_authenticate_raises(self):
        stub = TradovateBrokerStub()
        with pytest.raises(NotImplementedError):
            stub.authenticate()

    def test_tradovate_stub_missing_credentials_does_not_crash(self, monkeypatch):
        """Missing Tradovate credentials don't crash the stub — they're not used."""
        for var in ["TRADOVATE_USERNAME", "TRADOVATE_PASSWORD", "TRADOVATE_APP_ID"]:
            monkeypatch.delenv(var, raising=False)
        stub = TradovateBrokerStub()
        assert stub.is_live is True  # Still correctly identified as live
        assert stub._username == ""


# ─── RiskEngine blocks live broker ────────────────────────────────────────────

class TestRiskEngineBlocksLiveTrading:
    """RiskEngine raises LiveTradingBlockedError if config has live=True."""

    def test_risk_engine_raises_if_config_live_true(self, valid_trade_setup):
        """RiskEngine with live_trading_enabled=True raises, not just rejects."""
        bad_config = SystemConfig(
            live_trading_enabled=True,  # Artificially set for this test
            paper_mode=False,
            allowed_instruments=["MNQ", "MES", "MGC", "MCL"],
            allowed_sessions=["london", "new_york"],
            disabled_sessions=["asian"],
            session_hours={},
            max_trades_per_day=3,
            max_consecutive_losses=2,
            max_open_positions=1,
            averaging_down_allowed=False,
            require_entry=True,
            require_stop=True,
            require_target=True,
            min_rr_ratio=2.0,
            max_staleness_seconds=300,
            reject_null_required_fields=True,
            reject_contradictory_data=True,
            tradable_states=["TRENDING", "RANGE_BOUND"],
            non_tradable_states=["CHOPPY", "DEAD"],
            enabled_concepts=[],
            log_dir="logs_test",
            log_level="WARNING",
            risk_rules_path="risk_rules.yaml",
        )
        engine = RiskEngine(config=bad_config)
        daily = DailyState()

        with pytest.raises(LiveTradingBlockedError):
            engine.validate(valid_trade_setup, daily)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _minimal_rules_yaml(live: bool) -> str:
    live_str = "true" if live else "false"
    return f"""
version: "1.0.0"
trading_mode:
  live_trading_enabled: {live_str}
  paper_mode: true
instruments:
  allowed: [MNQ, MES, MGC, MCL]
sessions:
  allowed: [london, new_york]
  disabled: [asian]
session_hours_et:
  london:
    start: "03:00"
    end: "08:30"
  new_york:
    start: "09:30"
    end: "12:00"
daily_limits:
  max_trades_per_day: 3
  max_consecutive_losses: 2
position_rules:
  max_open_positions: 1
  averaging_down: false
order_rules:
  order_type: bracket
  require_entry: true
  require_stop: true
  require_target: true
risk_reward:
  min_rr_ratio: 2.0
data_quality:
  max_staleness_seconds: 300
  reject_null_required_fields: true
  reject_contradictory_data: true
market_condition:
  tradable_states: [TRENDING, RANGE_BOUND]
  non_tradable_states: [CHOPPY, DEAD]
strategy:
  enabled_concepts: [orb_reclaim]
  frequency: low
  no_trade_is_valid: true
"""
