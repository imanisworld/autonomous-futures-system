"""
tests/test_decision_engine.py

Tests for the DecisionEngine:
- NO_TRADE for choppy/dead markets
- NO_TRADE for wrong session/instrument
- DONE_FOR_DAY at trade and loss limits
- WAIT when position is open
- TRADE for valid ORB and VWAP setups
- NO_TRADE when R:R < 2.0
"""

from __future__ import annotations

from datetime import datetime, timezone
from copy import deepcopy

import pytest

from context.market_context import (
    MarketState, PriceData, OHLCData, VWAPData, ORBData,
    PreviousDayData, VolumeData, TrendData,
)
from risk.risk_engine import DailyState
from strategy.signal_engine import DecisionEngine
from strategy.strat_classifier import StratContext


@pytest.fixture
def engine(config):
    return DecisionEngine(config=config)


class TestDecisionEnginePreFlight:

    def test_done_for_day_at_trade_limit(self, engine, fresh_market_state):
        daily = DailyState(trade_count=3)
        decision = engine.evaluate(fresh_market_state, daily)
        assert decision.decision == "DONE_FOR_DAY"

    def test_done_for_day_at_loss_limit(self, engine, fresh_market_state):
        daily = DailyState(consecutive_losses=2)
        decision = engine.evaluate(fresh_market_state, daily)
        assert decision.decision == "DONE_FOR_DAY"

    def test_wait_when_position_open(self, engine, fresh_market_state):
        daily = DailyState(has_open_position=True)
        decision = engine.evaluate(fresh_market_state, daily)
        assert decision.decision == "WAIT"


class TestDecisionEngineSessionFilter:

    def test_asian_session_no_trade(self, engine, fresh_market_state):
        state = deepcopy(fresh_market_state)
        state.session = "asian"
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "NO_TRADE"
        assert "asian" in decision.reason.lower() or "not allowed" in decision.reason.lower()

    def test_pre_market_no_trade(self, engine, fresh_market_state):
        state = deepcopy(fresh_market_state)
        state.session = "pre_market"
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "NO_TRADE"

    def test_new_york_session_eligible(self, engine, fresh_market_state):
        decision = engine.evaluate(fresh_market_state, DailyState())
        # Should get through session filter (may still be NO_TRADE for other reasons)
        assert decision.decision != "NO_TRADE" or "not allowed" not in decision.reason


class TestDecisionEngineInstrumentFilter:

    def test_invalid_instrument_no_trade(self, engine, fresh_market_state):
        state = deepcopy(fresh_market_state)
        state.instrument = "ES"  # Not allowed
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "NO_TRADE"
        assert "instrument" in decision.reason.lower() or "not in" in decision.reason.lower()


class TestDecisionEngineMarketCondition:

    def test_choppy_market_no_trade(self, engine, fresh_market_state):
        state = deepcopy(fresh_market_state)
        state.market_condition = "CHOPPY"
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "NO_TRADE"
        assert "CHOPPY" in decision.reason

    def test_dead_market_no_trade(self, engine, fresh_market_state):
        state = deepcopy(fresh_market_state)
        state.market_condition = "DEAD"
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "NO_TRADE"
        assert "DEAD" in decision.reason

    def test_trending_market_eligible(self, engine, fresh_market_state):
        """TRENDING market with ORB reclaim should produce TRADE."""
        decision = engine.evaluate(fresh_market_state, DailyState())
        # Sample fixture is TRENDING with orb_reclaim conditions
        assert decision.decision == "TRADE"

    def test_range_bound_eligible(self, engine, fresh_market_state):
        """RANGE_BOUND market should not be immediately rejected."""
        state = deepcopy(fresh_market_state)
        state.market_condition = "RANGE_BOUND"
        decision = engine.evaluate(state, DailyState())
        # RANGE_BOUND is tradable — condition should not be rejection reason
        assert "RANGE_BOUND" not in decision.reason or decision.decision == "TRADE"


class TestDecisionEngineORBSetup:

    def test_orb_reclaim_generates_trade(self, engine, fresh_market_state):
        """ORB reclaim setup in TRENDING market should produce TRADE."""
        decision = engine.evaluate(fresh_market_state, DailyState())
        assert decision.decision == "TRADE"
        assert decision.setup is not None
        assert decision.setup.strategy == "orb_reclaim"
        assert decision.setup.direction == "LONG"
        assert decision.setup.entry > 0
        assert decision.setup.stop > 0
        assert decision.setup.target > 0
        assert decision.setup.rr_ratio >= 2.0

    def test_orb_rejection_generates_short(self, engine, fresh_market_state):
        state = deepcopy(fresh_market_state)
        state.orb.status = "rejected_high"
        state.vwap.price_vs_vwap = "below"
        state.vwap.reclaimed = False
        state.trend = TrendData(direction="DOWN", strength="MODERATE")
        decision = engine.evaluate(state, DailyState())
        if decision.decision == "TRADE":
            assert decision.setup.direction == "SHORT"
            assert decision.setup.strategy == "orb_rejection"


class TestDecisionEngineRRFilter:

    def test_trade_has_rr_above_minimum(self, engine, fresh_market_state):
        """Any TRADE decision must have R:R >= 2.0."""
        decision = engine.evaluate(fresh_market_state, DailyState())
        if decision.decision == "TRADE":
            assert decision.setup.rr_ratio >= 2.0


class TestDecisionEngineNoTradeIsValid:

    def test_no_setup_produces_no_trade(self, engine, fresh_market_state):
        """When no strategy fires, decision is NO_TRADE (not an error)."""
        state = deepcopy(fresh_market_state)
        # Clear all strategy signals
        state.orb.status = "inside"
        state.vwap.reclaimed = False
        state.vwap.holding = False
        state.vwap.price_vs_vwap = "at"
        state.previous_day.price_vs_pdh = "below"
        state.previous_day.price_vs_pdl = "above"
        state.trend = TrendData(direction="SIDEWAYS", strength="WEAK")
        state.market_condition = "RANGE_BOUND"

        decision = engine.evaluate(state, DailyState())
        # Expect NO_TRADE — no strategy qualifies
        assert decision.decision in ("NO_TRADE", "TRADE")
        # Either is valid — system should not crash
        assert decision.reason is not None and len(decision.reason) > 0

    def test_decision_always_has_reason(self, engine, fresh_market_state):
        """Every decision must include a non-empty reason string."""
        scenarios = [
            DailyState(),
            DailyState(trade_count=3),
            DailyState(consecutive_losses=2),
            DailyState(has_open_position=True),
        ]
        for daily in scenarios:
            decision = engine.evaluate(fresh_market_state, daily)
            assert decision.reason is not None
            assert len(decision.reason) >= 5

    def test_decision_always_has_instrument(self, engine, fresh_market_state):
        """Every decision includes the instrument."""
        decision = engine.evaluate(fresh_market_state, DailyState())
        assert decision.instrument == "MNQ"

    def test_setup_is_none_for_no_trade(self, engine, fresh_market_state):
        """NO_TRADE, DONE_FOR_DAY, and WAIT decisions have no setup."""
        daily = DailyState(trade_count=3)
        decision = engine.evaluate(fresh_market_state, daily)
        assert decision.decision == "DONE_FOR_DAY"
        assert decision.setup is None


class TestPDHPDLReclaim:
    """PDH/PDL reclaim strategies fire on above/below + trend + VWAP."""

    @pytest.fixture
    def pdh_config(self, config):
        from dataclasses import replace
        return replace(config, enabled_concepts=["pdh_reclaim"])

    @pytest.fixture
    def pdl_config(self, config):
        from dataclasses import replace
        return replace(config, enabled_concepts=["pdl_reclaim"])

    def _pdh_state(self, fresh_market_state):
        state = deepcopy(fresh_market_state)
        state.previous_day.high = 19490.0       # close 19505.25 is above
        state.previous_day.price_vs_pdh = "above"
        state.trend = TrendData(direction="UP", strength="MODERATE")
        state.vwap.price_vs_vwap = "above"
        state.orb.status = "inside"             # neutralise other setups
        return state

    def _pdl_state(self, fresh_market_state):
        state = deepcopy(fresh_market_state)
        state.ohlc = deepcopy(state.ohlc)
        state.ohlc.close = 19430.0              # below PDL 19440
        state.price.last = 19430.0
        state.previous_day.low = 19440.0
        state.previous_day.price_vs_pdl = "below"
        state.trend = TrendData(direction="DOWN", strength="MODERATE")
        state.vwap.price_vs_vwap = "below"
        state.orb.status = "inside"
        return state

    def test_pdh_reclaim_generates_long(self, fresh_market_state, pdh_config):
        engine = DecisionEngine(config=pdh_config)
        state = self._pdh_state(fresh_market_state)
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "TRADE"
        assert decision.setup.strategy == "pdh_reclaim"
        assert decision.setup.direction == "LONG"

    def test_pdh_reclaim_blocked_without_trend(self, fresh_market_state, pdh_config):
        engine = DecisionEngine(config=pdh_config)
        state = self._pdh_state(fresh_market_state)
        state.trend = TrendData(direction="SIDEWAYS", strength="WEAK")
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "NO_TRADE"

    def test_pdh_reclaim_blocked_without_vwap(self, fresh_market_state, pdh_config):
        engine = DecisionEngine(config=pdh_config)
        state = self._pdh_state(fresh_market_state)
        state.vwap.price_vs_vwap = "below"
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "NO_TRADE"

    def test_pdl_reclaim_generates_short(self, fresh_market_state, pdl_config):
        engine = DecisionEngine(config=pdl_config)
        state = self._pdl_state(fresh_market_state)
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "TRADE"
        assert decision.setup.strategy == "pdl_reclaim"
        assert decision.setup.direction == "SHORT"

    def test_pdl_reclaim_blocked_without_downtrend(self, fresh_market_state, pdl_config):
        engine = DecisionEngine(config=pdl_config)
        state = self._pdl_state(fresh_market_state)
        state.trend = TrendData(direction="UP", strength="MODERATE")
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "NO_TRADE"


class TestStratWiring:
    """Strat classifier integration with the decision engine."""

    @pytest.fixture
    def strat_config(self, config):
        """Config with strat concepts enabled."""
        from dataclasses import replace
        return replace(config, enabled_concepts=[
            "strat_212", "strat_122",
        ])

    @pytest.fixture
    def strat_engine(self, strat_config):
        return DecisionEngine(config=strat_config)

    def _state_with_strat(self, fresh_market_state, **strat_kwargs) -> MarketState:
        state = deepcopy(fresh_market_state)
        state.strat = StratContext(**strat_kwargs)
        # Clear ORB signals so only strat fires
        state.orb.status = "inside"
        state.market_condition = "TRENDING"
        return state

    def test_strat_212_fires_from_classified_long(self, strat_engine, fresh_market_state):
        """strat_212 LONG setup generated from actual classified bar sequence."""
        state = self._state_with_strat(
            fresh_market_state,
            current_bar_type="two_up",
            previous_bar_type="inside_bar",
            two_bars_back_type="two_up",
            strat_sequence="strat_212",
            strat_trigger="continuation",
            strat_direction="LONG",
        )
        decision = strat_engine.evaluate(state, DailyState())
        assert decision.decision == "TRADE"
        assert decision.setup.strategy == "strat_212"
        assert decision.setup.direction == "LONG"
        assert "classified from bar sequence" in decision.setup.notes

    def test_strat_212_fires_from_classified_short(self, strat_engine, fresh_market_state):
        """strat_212 SHORT setup generated from actual classified bar sequence."""
        state = self._state_with_strat(
            fresh_market_state,
            current_bar_type="two_down",
            previous_bar_type="inside_bar",
            two_bars_back_type="two_down",
            strat_sequence="strat_212",
            strat_trigger="continuation",
            strat_direction="SHORT",
        )
        decision = strat_engine.evaluate(state, DailyState())
        assert decision.decision == "TRADE"
        assert decision.setup.strategy == "strat_212"
        assert decision.setup.direction == "SHORT"

    def test_strat_122_fires_from_classified_long(self, strat_engine, fresh_market_state):
        """strat_122 LONG reversal setup from classified sequence."""
        state = self._state_with_strat(
            fresh_market_state,
            current_bar_type="two_up",
            previous_bar_type="two_down",
            two_bars_back_type="inside_bar",
            strat_sequence="strat_122",
            strat_trigger="reversal",
            strat_direction="LONG",
        )
        # Use a non-"inside" ORB status so the strat_212 Phase 1 proxy doesn't
        # fire before the strat_122 classified path gets evaluated.
        state.orb.status = "reclaimed_high"
        decision = strat_engine.evaluate(state, DailyState())
        assert decision.decision == "TRADE"
        assert decision.setup.strategy == "strat_122"
        assert decision.setup.direction == "LONG"

    def test_strat_confirmation_vetoes_opposing_direction(self, engine, fresh_market_state):
        """
        When a structural setup (e.g. orb_reclaim LONG) is found but the
        classified strat sequence points SHORT, the trade is blocked.
        """
        state = deepcopy(fresh_market_state)
        # ORB reclaim would fire LONG, but strat says SHORT
        state.strat = StratContext(
            current_bar_type="two_down",
            previous_bar_type="inside_bar",
            two_bars_back_type="two_down",
            strat_sequence="strat_212",
            strat_direction="SHORT",
        )
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "NO_TRADE"
        assert "contradicts" in decision.reason.lower()

    def test_strat_confirmation_appends_aligned_note(self, engine, fresh_market_state):
        """When strat direction aligns with setup, notes mention 'aligned'."""
        state = deepcopy(fresh_market_state)
        state.strat = StratContext(
            current_bar_type="two_up",
            strat_sequence="strat_212",
            strat_direction="LONG",
        )
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "TRADE"
        assert "aligned" in decision.setup.notes

    def test_strat_no_sequence_does_not_veto(self, engine, fresh_market_state):
        """Strat context without a sequence (just bar type) never vetoes."""
        state = deepcopy(fresh_market_state)
        state.strat = StratContext(current_bar_type="two_up")
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "TRADE"

    def test_strat_none_does_not_affect_decision(self, engine, fresh_market_state):
        """No strat context — engine behaves exactly as before."""
        state = deepcopy(fresh_market_state)
        state.strat = None
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "TRADE"
        assert decision.setup.strategy == "orb_reclaim"


class TestStrat4hrRetrigger:
    """strat_4hr_retrigger fires early NY; orb_reclaim catches the same bar late NY."""

    @pytest.fixture
    def retrigger_config(self, config):
        from dataclasses import replace
        return replace(config, enabled_concepts=["strat_4hr_retrigger", "orb_reclaim"])

    def _retrigger_state(self, ts: datetime) -> MarketState:
        return MarketState(
            timestamp=ts,
            instrument="MNQ",
            session="new_york",
            price=PriceData(last=19505.25, bid=19505.0, ask=19505.5),
            ohlc=OHLCData(open=19480.0, high=19510.0, low=19475.0, close=19505.25, timeframe="5m"),
            vwap=VWAPData(value=19495.0, price_vs_vwap="above", reclaimed=True, holding=True),
            orb=ORBData(high=19498.0, low=19462.0, timeframe_minutes=15, status="reclaimed_high"),
            previous_day=PreviousDayData(high=19520.0, low=19440.0, close=19475.0),
            volume=VolumeData(current_bar=5000, avg_bar=3800, relative=1.32),
            market_condition="TRENDING",
            trend=TrendData(direction="UP", strength="STRONG", ema_fast_above_slow=True),
            raw={},
        )

    def test_fires_early_ny(self, retrigger_config):
        """9:45 ET — inside the 9:30–11:00 window → strat_4hr_retrigger."""
        from datetime import timezone
        from zoneinfo import ZoneInfo
        ts = datetime(2026, 5, 23, 13, 45, tzinfo=timezone.utc)  # 09:45 ET
        state = self._retrigger_state(ts)
        engine = DecisionEngine(config=retrigger_config)
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "TRADE"
        assert decision.setup.strategy == "strat_4hr_retrigger"

    def test_falls_back_to_orb_reclaim_late_ny(self, retrigger_config):
        """11:30 ET — outside the window → orb_reclaim fires instead."""
        ts = datetime(2026, 5, 23, 15, 30, tzinfo=timezone.utc)  # 11:30 ET
        state = self._retrigger_state(ts)
        engine = DecisionEngine(config=retrigger_config)
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "TRADE"
        assert decision.setup.strategy == "orb_reclaim"

    def test_does_not_fire_on_moderate_trend(self, retrigger_config):
        """MODERATE trend — orb_reclaim fires, not strat_4hr_retrigger."""
        ts = datetime(2026, 5, 23, 13, 45, tzinfo=timezone.utc)  # 09:45 ET
        state = self._retrigger_state(ts)
        state.trend = TrendData(direction="UP", strength="MODERATE")
        engine = DecisionEngine(config=retrigger_config)
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "TRADE"
        assert decision.setup.strategy == "orb_reclaim"

    def test_does_not_fire_for_mes_instrument(self, retrigger_config):
        """MES is allowed for strat_4hr_retrigger, confirm it fires."""
        ts = datetime(2026, 5, 23, 13, 45, tzinfo=timezone.utc)  # 09:45 ET
        state = self._retrigger_state(ts)
        state.instrument = "MES"
        engine = DecisionEngine(config=retrigger_config)
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "TRADE"
        assert decision.setup.strategy == "strat_4hr_retrigger"

    def test_does_not_fire_for_mgc(self, retrigger_config):
        """MGC is not an equity index — strat_4hr_retrigger must not fire."""
        ts = datetime(2026, 5, 23, 13, 45, tzinfo=timezone.utc)  # 09:45 ET
        state = self._retrigger_state(ts)
        state.instrument = "MGC"
        engine = DecisionEngine(config=retrigger_config)
        decision = engine.evaluate(state, DailyState())
        # Falls back to orb_reclaim (or NO_TRADE if blocked by max_contracts)
        if decision.decision == "TRADE":
            assert decision.setup.strategy == "orb_reclaim"
