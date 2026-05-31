"""
tests/test_risk_engine.py

Tests for every RiskEngine rule.
Each rule is tested: valid pass, and each failure mode.
"""

from __future__ import annotations

import pytest
from risk.risk_engine import RiskEngine, DailyState, TradeSetup, RiskResult


class TestRiskEngineApproval:

    def test_valid_setup_is_approved(self, config, valid_trade_setup, clean_daily_state):
        engine = RiskEngine(config=config)
        result = engine.validate(valid_trade_setup, clean_daily_state)
        assert result.approved
        assert result.result == "APPROVED"
        assert result.failed_rule is None

    def test_approved_result_properties(self, config, valid_trade_setup, clean_daily_state):
        engine = RiskEngine(config=config)
        result = engine.validate(valid_trade_setup, clean_daily_state)
        assert result.approved is True
        assert result.rejected is False


class TestInstrumentCheck:

    def test_invalid_instrument_rejected(self, config, clean_daily_state):
        engine = RiskEngine(config=config)
        setup = TradeSetup(
            direction="LONG", entry=100.0, stop=95.0, target=110.0,
            rr_ratio=2.0, strategy="orb_reclaim",
            instrument="ES",  # Not in allowed list
            session="new_york",
        )
        result = engine.validate(setup, clean_daily_state)
        assert result.rejected
        assert result.failed_rule == "instrument_not_allowed"

    def test_all_allowed_instruments_pass(self, config, clean_daily_state):
        engine = RiskEngine(config=config)
        for instrument in ["MNQ", "MES", "MGC", "MCL"]:
            setup = TradeSetup(
                direction="LONG", entry=100.0, stop=95.0, target=110.0,
                rr_ratio=2.0, strategy="orb_reclaim",
                instrument=instrument, session="new_york",
            )
            result = engine.validate(setup, clean_daily_state)
            assert result.failed_rule != "instrument_not_allowed", \
                f"{instrument} should be allowed"


class TestSessionCheck:

    def test_asian_session_rejected(self, config, clean_daily_state):
        engine = RiskEngine(config=config)
        setup = TradeSetup(
            direction="LONG", entry=19500.0, stop=19480.0, target=19540.0,
            rr_ratio=2.0, strategy="orb_reclaim",
            instrument="MNQ", session="asian",
        )
        result = engine.validate(setup, clean_daily_state)
        assert result.rejected
        assert result.failed_rule == "session_not_allowed"

    def test_allowed_sessions_pass(self, config, clean_daily_state):
        engine = RiskEngine(config=config)
        for session in ["london", "new_york"]:
            setup = TradeSetup(
                direction="LONG", entry=19500.0, stop=19480.0, target=19540.0,
                rr_ratio=2.0, strategy="orb_reclaim",
                instrument="MNQ", session=session,
            )
            result = engine.validate(setup, clean_daily_state)
            assert result.failed_rule != "session_not_allowed", \
                f"{session} should be allowed"

    def test_pre_market_rejected(self, config, clean_daily_state):
        engine = RiskEngine(config=config)
        setup = TradeSetup(
            direction="LONG", entry=19500.0, stop=19480.0, target=19540.0,
            rr_ratio=2.0, strategy="orb_reclaim",
            instrument="MNQ", session="pre_market",
        )
        result = engine.validate(setup, clean_daily_state)
        assert result.rejected
        assert result.failed_rule == "session_not_allowed"


class TestMaxContractsCheck:

    def test_mgc_one_contract_rejected(self, config, clean_daily_state):
        engine = RiskEngine(config=config)
        setup = TradeSetup(
            direction="LONG", entry=2000.0, stop=1995.0, target=2010.0,
            rr_ratio=2.0, strategy="orb_reclaim",
            instrument="MGC", session="new_york", contracts=1,
        )
        result = engine.validate(setup, clean_daily_state)
        assert result.rejected
        assert result.failed_rule == "max_contracts_exceeded"

    def test_mnq_two_contracts_approved(self, config, clean_daily_state):
        engine = RiskEngine(config=config)
        setup = TradeSetup(
            direction="LONG", entry=19500.0, stop=19480.0, target=19540.0,
            rr_ratio=2.0, strategy="orb_reclaim",
            instrument="MNQ", session="new_york", contracts=2,
        )
        result = engine.validate(setup, clean_daily_state)
        assert result.approved

    def test_mnq_three_contracts_rejected(self, config, clean_daily_state):
        engine = RiskEngine(config=config)
        setup = TradeSetup(
            direction="LONG", entry=19500.0, stop=19480.0, target=19540.0,
            rr_ratio=2.0, strategy="orb_reclaim",
            instrument="MNQ", session="new_york", contracts=3,
        )
        result = engine.validate(setup, clean_daily_state)
        assert result.rejected
        assert result.failed_rule == "max_contracts_exceeded"

    def test_instrument_not_in_contract_config_defaults_to_one(self, config, clean_daily_state):
        config.allowed_instruments.append("M2K")
        engine = RiskEngine(config=config)
        one_contract = TradeSetup(
            direction="LONG", entry=100.0, stop=95.0, target=110.0,
            rr_ratio=2.0, strategy="orb_reclaim",
            instrument="M2K", session="new_york", contracts=1,
        )
        two_contracts = TradeSetup(
            direction="LONG", entry=100.0, stop=95.0, target=110.0,
            rr_ratio=2.0, strategy="orb_reclaim",
            instrument="M2K", session="new_york", contracts=2,
        )

        assert engine.validate(one_contract, clean_daily_state).approved
        rejected = engine.validate(two_contracts, clean_daily_state)
        assert rejected.rejected
        assert rejected.failed_rule == "max_contracts_exceeded"


class TestDailyTradeLimit:

    def test_at_limit_rejected(self, config, valid_trade_setup):
        engine = RiskEngine(config=config)
        daily = DailyState(trade_count=3)  # At max
        result = engine.validate(valid_trade_setup, daily)
        assert result.rejected
        assert result.failed_rule == "daily_trade_limit"

    def test_over_limit_rejected(self, config, valid_trade_setup):
        engine = RiskEngine(config=config)
        daily = DailyState(trade_count=5)  # Over max
        result = engine.validate(valid_trade_setup, daily)
        assert result.rejected
        assert result.failed_rule == "daily_trade_limit"

    def test_below_limit_passes(self, config, valid_trade_setup):
        engine = RiskEngine(config=config)
        for count in [0, 1, 2]:
            daily = DailyState(trade_count=count)
            result = engine.validate(valid_trade_setup, daily)
            assert result.failed_rule != "daily_trade_limit", \
                f"trade_count={count} should be under limit"


class TestConsecutiveLossLimit:

    def test_at_loss_limit_rejected(self, config, valid_trade_setup):
        engine = RiskEngine(config=config)
        daily = DailyState(consecutive_losses=2)
        result = engine.validate(valid_trade_setup, daily)
        assert result.rejected
        assert result.failed_rule == "consecutive_loss_limit"

    def test_one_loss_still_trades(self, config, valid_trade_setup):
        engine = RiskEngine(config=config)
        daily = DailyState(consecutive_losses=1)
        result = engine.validate(valid_trade_setup, daily)
        assert result.failed_rule != "consecutive_loss_limit"

    def test_zero_losses_trades(self, config, valid_trade_setup):
        engine = RiskEngine(config=config)
        daily = DailyState(consecutive_losses=0)
        result = engine.validate(valid_trade_setup, daily)
        assert result.failed_rule != "consecutive_loss_limit"


class TestOpenPositionCheck:

    def test_open_position_blocks_new_trade(self, config, valid_trade_setup):
        engine = RiskEngine(config=config)
        daily = DailyState(has_open_position=True)
        result = engine.validate(valid_trade_setup, daily)
        assert result.rejected
        assert result.failed_rule == "open_position_exists"

    def test_no_open_position_passes(self, config, valid_trade_setup):
        engine = RiskEngine(config=config)
        daily = DailyState(has_open_position=False)
        result = engine.validate(valid_trade_setup, daily)
        assert result.failed_rule != "open_position_exists"


class TestBracketCompleteness:

    def test_zero_entry_rejected(self, config, clean_daily_state):
        engine = RiskEngine(config=config)
        setup = TradeSetup(
            direction="LONG", entry=0.0, stop=19480.0, target=19540.0,
            rr_ratio=2.0, strategy="orb_reclaim",
            instrument="MNQ", session="new_york",
        )
        result = engine.validate(setup, clean_daily_state)
        assert result.rejected
        assert result.failed_rule == "incomplete_bracket"

    def test_zero_stop_rejected(self, config, clean_daily_state):
        engine = RiskEngine(config=config)
        setup = TradeSetup(
            direction="LONG", entry=19500.0, stop=0.0, target=19540.0,
            rr_ratio=2.0, strategy="orb_reclaim",
            instrument="MNQ", session="new_york",
        )
        result = engine.validate(setup, clean_daily_state)
        assert result.rejected
        assert result.failed_rule == "incomplete_bracket"

    def test_zero_target_rejected(self, config, clean_daily_state):
        engine = RiskEngine(config=config)
        setup = TradeSetup(
            direction="LONG", entry=19500.0, stop=19480.0, target=0.0,
            rr_ratio=2.0, strategy="orb_reclaim",
            instrument="MNQ", session="new_york",
        )
        result = engine.validate(setup, clean_daily_state)
        assert result.rejected
        assert result.failed_rule == "incomplete_bracket"


class TestRRRatio:

    def test_rr_below_minimum_rejected(self, config, clean_daily_state):
        engine = RiskEngine(config=config)
        setup = TradeSetup(
            direction="LONG", entry=19500.0, stop=19480.0, target=19530.0,
            rr_ratio=1.5,  # Below 2.0
            strategy="orb_reclaim",
            instrument="MNQ", session="new_york",
        )
        result = engine.validate(setup, clean_daily_state)
        assert result.rejected
        assert result.failed_rule == "rr_below_minimum"

    def test_rr_exactly_minimum_passes(self, config, clean_daily_state):
        engine = RiskEngine(config=config)
        setup = TradeSetup(
            direction="LONG", entry=19500.0, stop=19480.0, target=19540.0,
            rr_ratio=2.0, strategy="orb_reclaim",
            instrument="MNQ", session="new_york",
        )
        result = engine.validate(setup, clean_daily_state)
        assert result.failed_rule != "rr_below_minimum"

    def test_rr_above_minimum_passes(self, config, clean_daily_state):
        engine = RiskEngine(config=config)
        setup = TradeSetup(
            direction="LONG", entry=19500.0, stop=19480.0, target=19560.0,
            rr_ratio=3.0, strategy="orb_reclaim",
            instrument="MNQ", session="new_york",
        )
        result = engine.validate(setup, clean_daily_state)
        assert result.failed_rule != "rr_below_minimum"


class TestDirectionCheck:

    def test_invalid_direction_rejected(self, config, clean_daily_state):
        engine = RiskEngine(config=config)
        setup = TradeSetup(
            direction="BUY",  # Invalid
            entry=19500.0, stop=19480.0, target=19540.0,
            rr_ratio=2.0, strategy="orb_reclaim",
            instrument="MNQ", session="new_york",
        )
        result = engine.validate(setup, clean_daily_state)
        assert result.rejected
        assert result.failed_rule == "invalid_direction"

    def test_long_direction_valid(self, config, clean_daily_state):
        engine = RiskEngine(config=config)
        setup = TradeSetup(
            direction="LONG", entry=19500.0, stop=19480.0, target=19540.0,
            rr_ratio=2.0, strategy="orb_reclaim",
            instrument="MNQ", session="new_york",
        )
        result = engine.validate(setup, clean_daily_state)
        assert result.failed_rule != "invalid_direction"

    def test_short_direction_valid(self, config, clean_daily_state):
        engine = RiskEngine(config=config)
        setup = TradeSetup(
            direction="SHORT", entry=19500.0, stop=19520.0, target=19460.0,
            rr_ratio=2.0, strategy="orb_rejection",
            instrument="MNQ", session="new_york",
        )
        result = engine.validate(setup, clean_daily_state)
        assert result.failed_rule != "invalid_direction"


class TestDistinctPrices:

    def test_entry_equals_stop_rejected(self, config, clean_daily_state):
        engine = RiskEngine(config=config)
        setup = TradeSetup(
            direction="LONG", entry=19500.0, stop=19500.0, target=19540.0,
            rr_ratio=2.0, strategy="orb_reclaim",
            instrument="MNQ", session="new_york",
        )
        result = engine.validate(setup, clean_daily_state)
        assert result.rejected
        assert result.failed_rule == "entry_equals_stop"

    def test_entry_equals_target_rejected(self, config, clean_daily_state):
        engine = RiskEngine(config=config)
        setup = TradeSetup(
            direction="LONG", entry=19500.0, stop=19480.0, target=19500.0,
            rr_ratio=0.0, strategy="orb_reclaim",
            instrument="MNQ", session="new_york",
        )
        result = engine.validate(setup, clean_daily_state)
        assert result.rejected
        assert result.failed_rule == "entry_equals_target"


class TestPerSessionTradeLimit:

    def _setup(self, session="new_york"):
        return TradeSetup(
            direction="LONG", entry=19500.0, stop=19480.0, target=19540.0,
            rr_ratio=2.0, strategy="orb_reclaim",
            instrument="MNQ", session=session,
        )

    def _config_with_limits(self, config, limits):
        from dataclasses import replace
        return replace(config, per_session_limits=limits)

    def test_under_limit_approved(self, config, clean_daily_state):
        cfg = self._config_with_limits(config, {"new_york": 2})
        engine = RiskEngine(config=cfg)
        state = DailyState(session_trade_counts={"new_york": 1})
        result = engine.validate(self._setup("new_york"), state)
        assert result.approved

    def test_at_limit_rejected(self, config, clean_daily_state):
        cfg = self._config_with_limits(config, {"new_york": 2})
        engine = RiskEngine(config=cfg)
        state = DailyState(session_trade_counts={"new_york": 2})
        result = engine.validate(self._setup("new_york"), state)
        assert result.rejected
        assert result.failed_rule == "session_trade_limit"
        assert "new_york" in result.reason

    def test_no_limit_configured_passes(self, config, clean_daily_state):
        cfg = self._config_with_limits(config, {})
        engine = RiskEngine(config=cfg)
        state = DailyState(session_trade_counts={"new_york": 99})
        result = engine.validate(self._setup("new_york"), state)
        assert result.approved

    def test_asian_limit_of_one(self, config, clean_daily_state):
        cfg = self._config_with_limits(
            config,
            {"asian": 1, "london": 2, "new_york": 2},
        )
        cfg = type(cfg)(
            **{
                **cfg.__dict__,
                "allowed_sessions": ["london", "new_york", "asian"],
                "disabled_sessions": [],
            }
        )
        engine = RiskEngine(config=cfg)
        state = DailyState(session_trade_counts={"asian": 1})
        result = engine.validate(self._setup("asian"), state)
        assert result.rejected
        assert result.failed_rule == "session_trade_limit"

    def test_other_session_count_does_not_affect_limit(self, config, clean_daily_state):
        cfg = self._config_with_limits(config, {"new_york": 2})
        engine = RiskEngine(config=cfg)
        state = DailyState(session_trade_counts={"london": 5, "new_york": 1})
        result = engine.validate(self._setup("new_york"), state)
        assert result.approved


class TestDynamicPositionSizing:

    def _sizing_config(self, config):
        from dataclasses import replace
        from config.settings import PositionSizingConfig, PositionSizingRule

        return replace(
            config,
            allowed_instruments=["MNQ", "MES", "ES", "NQ", "MGC", "MCL"],
            max_contracts_per_instrument={
                "MNQ": 2, "MES": 3, "ES": 3, "NQ": 2, "MGC": 0, "MCL": 0
            },
            position_sizing=PositionSizingConfig(
                starting_balance=5000,
                enabled=True,
                aggressive_rounding=True,
                rounding_threshold_percent=10,
                sizing_rules=[
                    PositionSizingRule(5000, 9000, "MES", 1),
                    PositionSizingRule(9000, 13500, "MES", 2),
                    PositionSizingRule(13500, 18000, "MES", 3),
                    PositionSizingRule(18000, 25000, "ES", 1),
                    PositionSizingRule(25000, 40000, "ES", 2),
                    PositionSizingRule(40000, 60000, "ES", 3),
                    PositionSizingRule(60000, None, "NQ", 2),
                ],
            ),
        )

    def _setup(self, instrument="MES", contracts=1):
        return TradeSetup(
            direction="LONG",
            entry=5000.0,
            stop=4990.0,
            target=5020.0,
            rr_ratio=2.0,
            strategy="vwap_hold",
            instrument=instrument,
            session="new_york",
            contracts=contracts,
        )

    def test_8500_rounds_up_and_allows_mes_two_contracts(self, config):
        engine = RiskEngine(config=self._sizing_config(config))
        state = DailyState(account_balance=8500)
        result = engine.validate(self._setup("MES", 2), state)
        assert result.approved

    def test_9500_allows_mes_two_contracts_within_tier(self, config):
        engine = RiskEngine(config=self._sizing_config(config))
        state = DailyState(account_balance=9500)
        result = engine.validate(self._setup("MES", 2), state)
        assert result.approved

    def test_17000_rounds_up_and_allows_es_one_contract(self, config):
        engine = RiskEngine(config=self._sizing_config(config))
        state = DailyState(account_balance=17000)
        result = engine.validate(self._setup("ES", 1), state)
        assert result.approved

    def test_12000_on_way_down_stays_mes_two_contracts(self, config):
        engine = RiskEngine(config=self._sizing_config(config))
        state = DailyState(account_balance=12000)
        assert engine.validate(self._setup("MES", 2), state).approved
        rejected = engine.validate(self._setup("MES", 3), state)
        assert rejected.rejected
        assert rejected.failed_rule == "position_sizing_contracts"

    def test_36000_rounds_to_40000_and_allows_es_three_contracts(self, config):
        engine = RiskEngine(config=self._sizing_config(config))
        state = DailyState(account_balance=36000)
        result = engine.validate(self._setup("ES", 3), state)
        assert result.approved

    def test_wrong_instrument_for_tier_is_rejected(self, config):
        engine = RiskEngine(config=self._sizing_config(config))
        state = DailyState(account_balance=17000)
        rejected = engine.validate(self._setup("MES", 3), state)
        assert rejected.rejected
        assert rejected.failed_rule == "position_sizing_instrument"


class TestRRCalculation:

    def test_long_rr_calculation(self):
        rr = RiskEngine.calculate_rr("LONG", entry=19500, stop=19480, target=19540)
        assert rr == pytest.approx(2.0, rel=1e-3)

    def test_short_rr_calculation(self):
        rr = RiskEngine.calculate_rr("SHORT", entry=19500, stop=19520, target=19460)
        assert rr == pytest.approx(2.0, rel=1e-3)

    def test_zero_risk_returns_zero(self):
        rr = RiskEngine.calculate_rr("LONG", entry=19500, stop=19500, target=19540)
        assert rr == 0.0

    def test_invalid_direction_returns_zero(self):
        rr = RiskEngine.calculate_rr("BUY", entry=19500, stop=19480, target=19540)
        assert rr == 0.0
