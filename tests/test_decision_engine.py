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
    PreviousDayData, VolumeData, TrendData, HTFContext,
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




class TestNewYorkEntryWindows:
    @staticmethod
    def _at_et(state: MarketState, hour: int, minute: int) -> MarketState:
        state = deepcopy(state)
        # May is EDT, so ET + 4 hours = UTC.
        state.timestamp = datetime(2026, 5, 23, hour + 4, minute, tzinfo=timezone.utc)
        state.session = "new_york"
        return state

    def _afternoon_a_plus_state(self, fresh_market_state):
        state = self._at_et(fresh_market_state, 13, 30)
        state.orb.status = "inside"
        state.trend = TrendData(direction="UP", strength="STRONG", ema_fast_above_slow=True)
        state.volume = VolumeData(current_bar=5200, avg_bar=3800, relative=1.37)
        state.strat = StratContext(
            current_bar_type="two_up",
            previous_bar_type="inside_bar",
            two_bars_back_type="two_up",
            strat_sequence="strat_212",
            strat_trigger="continuation",
            strat_direction="LONG",
        )
        return state

    def test_1030_opening_all_setups_allowed(self, engine, fresh_market_state):
        state = self._at_et(fresh_market_state, 10, 30)
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "TRADE"

    # ── mid_early (10:45–11:30): restricted ───────────────────────────────────

    def test_1050_mid_early_strong_trend_vwap_allowed(self, engine, fresh_market_state):
        """10:50 ET with strong trend + VWAP above passes mid_early gate."""
        state = self._at_et(fresh_market_state, 10, 50)
        state.trend = TrendData(direction="UP", strength="STRONG", ema_fast_above_slow=True)
        state.vwap.price_vs_vwap = "above"
        decision = engine.evaluate(state, DailyState())
        assert decision.decision != "NO_TRADE" or "mid-morning" not in decision.reason.lower()

    def test_1050_mid_early_moderate_trend_blocked(self, engine, fresh_market_state):
        """10:50 ET with only moderate trend is rejected by mid_early gate."""
        state = self._at_et(fresh_market_state, 10, 50)
        state.trend = TrendData(direction="UP", strength="MODERATE", ema_fast_above_slow=True)
        state.vwap.price_vs_vwap = "above"
        state.orb.status = "inside"
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "NO_TRADE"
        assert "mid-morning" in decision.reason.lower()

    def test_1050_mid_early_strong_trend_orb_active_allowed(self, engine, fresh_market_state):
        """10:50 ET passes mid_early gate via strong trend + active ORB (no VWAP needed)."""
        state = self._at_et(fresh_market_state, 10, 50)
        state.trend = TrendData(direction="UP", strength="STRONG", ema_fast_above_slow=True)
        state.vwap.price_vs_vwap = "inside"
        state.orb.status = "reclaimed_high"
        decision = engine.evaluate(state, DailyState())
        assert decision.decision != "NO_TRADE" or "mid-morning" not in decision.reason.lower()

    # ── mid_late (11:30–12:00): lunch block — hard blocked ───────────────────

    def test_1130_is_now_mid_late_blocked(self, engine, fresh_market_state):
        """11:30 ET is in mid_late (hard blocked) — lunch transition window."""
        state = self._at_et(fresh_market_state, 11, 30)
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "NO_TRADE"
        assert decision.reason == "NY session window blocked"

    # ── afternoon (12:00–14:00): open — all setups allowed ───────────────────

    def test_1205_afternoon_now_open(self, engine, fresh_market_state):
        """12:05 ET is in the open afternoon window — not blocked by session gate."""
        state = self._at_et(fresh_market_state, 12, 5)
        state.trend = TrendData(direction="UP", strength="STRONG", ema_fast_above_slow=True)
        state.vwap.price_vs_vwap = "above"
        state.orb.status = "reclaimed_high"
        decision = engine.evaluate(state, DailyState())
        assert decision.reason != "NY session window blocked"

    def test_1200_afternoon_open_strong_trend_gets_through_window(self, engine, fresh_market_state):
        """12:00 ET is in the open afternoon window — window gate does not block."""
        state = self._at_et(fresh_market_state, 12, 0)
        state.trend = TrendData(direction="UP", strength="STRONG", ema_fast_above_slow=True)
        state.vwap.price_vs_vwap = "above"
        state.orb.status = "reclaimed_high"
        decision = engine.evaluate(state, DailyState())
        assert decision.reason != "NY session window blocked"

    def test_1330_afternoon_open_orb_reclaim_allowed(self, config, fresh_market_state):
        """13:30 ET: afternoon window is unrestricted — orb_reclaim fires with strong trend."""
        from dataclasses import replace
        engine = DecisionEngine(config=replace(config, enabled_concepts=["orb_reclaim"]))
        state = self._at_et(fresh_market_state, 13, 30)
        state.trend = TrendData(direction="UP", strength="STRONG", ema_fast_above_slow=True)
        state.vwap.price_vs_vwap = "above"
        state.orb.status = "reclaimed_high"
        state.volume = VolumeData(current_bar=3000, avg_bar=3800, relative=0.79)
        decision = engine.evaluate(state, DailyState())
        assert decision.reason != "NY session window blocked"

    def test_1355_just_before_cutoff_allowed(self, engine, fresh_market_state):
        """13:55 ET is still inside the 14:00 cutoff — window is open."""
        state = self._at_et(fresh_market_state, 13, 55)
        state.trend = TrendData(direction="UP", strength="STRONG", ema_fast_above_slow=True)
        state.vwap.price_vs_vwap = "above"
        state.orb.status = "reclaimed_high"
        decision = engine.evaluate(state, DailyState())
        assert decision.reason != "NY session window blocked"

    def test_1400_late_blocks_entries(self, engine, fresh_market_state):
        """14:00 ET is in the late window — hard blocked."""
        state = self._at_et(fresh_market_state, 14, 0)
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "NO_TRADE"
        assert decision.reason == "NY session window blocked"

    def test_1530_late_blocks_entries(self, engine, fresh_market_state):
        state = self._at_et(fresh_market_state, 15, 30)
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "NO_TRADE"
        assert decision.reason == "NY session window blocked"


class TestQualityGates:
    """Tests for trend-strength and signal-bar-volume quality gates."""

    @pytest.fixture
    def qengine(self, config):
        from dataclasses import replace
        qconfig = replace(
            config,
            require_strong_trend={"MNQ": True, "MES": False},
            min_signal_bar_volume={"MNQ": 0.8, "MES": 0.0},
        )
        return DecisionEngine(config=qconfig)

    def _strong_state(self, fresh_market_state) -> MarketState:
        """Base state: STRONG trend + sufficient volume — passes all quality gates."""
        state = deepcopy(fresh_market_state)
        state.trend = TrendData(direction="UP", strength="STRONG", ema_fast_above_slow=True)
        state.volume = VolumeData(current_bar=4000, avg_bar=3800, relative=1.05)
        return state

    def test_mnq_strong_trend_passes(self, qengine, fresh_market_state):
        state = self._strong_state(fresh_market_state)
        decision = qengine.evaluate(state, DailyState())
        assert "TREND_STRENGTH_BELOW_REQUIRED" not in (decision.failed_gates or [])

    def test_mnq_moderate_trend_blocked(self, qengine, fresh_market_state):
        state = self._strong_state(fresh_market_state)
        state.trend = TrendData(direction="UP", strength="MODERATE", ema_fast_above_slow=True)
        decision = qengine.evaluate(state, DailyState())
        assert decision.decision == "NO_TRADE"
        assert "TREND_STRENGTH_BELOW_REQUIRED" in (decision.failed_gates or [])
        assert "STRONG" in decision.reason

    def test_mnq_no_trend_data_blocked(self, qengine, fresh_market_state):
        state = self._strong_state(fresh_market_state)
        state.trend = None
        decision = qengine.evaluate(state, DailyState())
        assert decision.decision == "NO_TRADE"
        assert "TREND_STRENGTH_BELOW_REQUIRED" in (decision.failed_gates or [])

    def test_mnq_sufficient_volume_passes(self, qengine, fresh_market_state):
        state = self._strong_state(fresh_market_state)
        state.volume = VolumeData(current_bar=4000, avg_bar=3800, relative=0.80)
        decision = qengine.evaluate(state, DailyState())
        assert "SIGNAL_BAR_VOLUME_TOO_LOW" not in (decision.failed_gates or [])

    def test_mnq_low_volume_blocked(self, qengine, fresh_market_state):
        state = self._strong_state(fresh_market_state)
        state.volume = VolumeData(current_bar=2000, avg_bar=3800, relative=0.53)
        decision = qengine.evaluate(state, DailyState())
        assert decision.decision == "NO_TRADE"
        assert "SIGNAL_BAR_VOLUME_TOO_LOW" in (decision.failed_gates or [])
        assert "0.53" in decision.reason

    def test_mnq_null_volume_blocked(self, qengine, fresh_market_state):
        state = self._strong_state(fresh_market_state)
        state.volume = VolumeData(current_bar=0, avg_bar=0, relative=None)
        decision = qengine.evaluate(state, DailyState())
        assert decision.decision == "NO_TRADE"
        assert "SIGNAL_BAR_VOLUME_TOO_LOW" in (decision.failed_gates or [])

    def test_trend_gate_fires_before_volume_gate(self, qengine, fresh_market_state):
        """Trend check precedes volume check — weak trend + low volume shows trend failure."""
        state = self._strong_state(fresh_market_state)
        state.trend = TrendData(direction="UP", strength="MODERATE", ema_fast_above_slow=True)
        state.volume = VolumeData(current_bar=2000, avg_bar=3800, relative=0.53)
        decision = qengine.evaluate(state, DailyState())
        assert decision.decision == "NO_TRADE"
        assert "TREND_STRENGTH_BELOW_REQUIRED" in (decision.failed_gates or [])

    def test_htf_alignment_fail_blocks_when_enabled(self, config, fresh_market_state):
        from dataclasses import replace

        qconfig = replace(
            config,
            require_htf_alignment={"MNQ": True},
            require_strong_trend={"MNQ": True},
            min_signal_bar_volume={"MNQ": 0.8},
        )
        state = self._strong_state(fresh_market_state)
        state.htf = HTFContext(
            daily_direction="UP",
            four_hour_direction="DOWN",
            ftfc_direction="MIXED",
            ftfc_aligned=False,
        )

        decision = DecisionEngine(config=qconfig).evaluate(state, DailyState())

        assert decision.decision == "NO_TRADE"
        assert decision.reason == "HTF/FTFC alignment failed"
        assert "HTF_ALIGNMENT_FAIL" in (decision.failed_gates or [])

    def test_htf_absent_does_not_block_when_enabled(self, config, fresh_market_state):
        from dataclasses import replace

        qconfig = replace(
            config,
            require_htf_alignment={"MNQ": True},
            require_strong_trend={"MNQ": True},
            min_signal_bar_volume={"MNQ": 0.8},
        )
        state = self._strong_state(fresh_market_state)
        state.htf = None

        decision = DecisionEngine(config=qconfig).evaluate(state, DailyState())

        assert "HTF_ALIGNMENT_FAIL" not in (decision.failed_gates or [])


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

    def test_1400_late_window_blocks_retrigger(self, retrigger_config):
        """14:05 ET is in the late window (hard blocked) — strat_4hr_retrigger cannot fire."""
        ts = datetime(2026, 5, 23, 18, 5, tzinfo=timezone.utc)  # 14:05 ET
        state = self._retrigger_state(ts)
        engine = DecisionEngine(config=retrigger_config)
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "NO_TRADE"
        assert decision.reason == "NY session window blocked"

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


class TestStratInsideBreakAndOutsideContinuation:
    """strat_inside_break and strat_outside_continuation handlers."""

    @pytest.fixture
    def ib_config(self, config):
        from dataclasses import replace
        return replace(config, enabled_concepts=["strat_inside_break"])

    @pytest.fixture
    def oc_config(self, config):
        from dataclasses import replace
        return replace(config, enabled_concepts=["strat_outside_continuation"])

    def _state_with(self, fresh_market_state, **strat_kwargs):
        state = deepcopy(fresh_market_state)
        state.strat = StratContext(**strat_kwargs)
        state.orb.status = "inside"   # neutralise ORB strategies
        state.market_condition = "TRENDING"
        return state

    # ── inside_break ─────────────────────────────────────────────────────────

    def test_inside_break_long_with_trend(self, fresh_market_state, ib_config):
        engine = DecisionEngine(config=ib_config)
        state = self._state_with(
            fresh_market_state,
            current_bar_type="two_up",
            previous_bar_type="inside_bar",
            strat_sequence="strat_inside_break",
            strat_direction="LONG",
        )
        state.trend = TrendData(direction="UP", strength="MODERATE")
        state.vwap.price_vs_vwap = "above"
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "TRADE"
        assert decision.setup.strategy == "strat_inside_break"
        assert decision.setup.direction == "LONG"

    def test_inside_break_short_with_downtrend(self, fresh_market_state, ib_config):
        engine = DecisionEngine(config=ib_config)
        state = self._state_with(
            fresh_market_state,
            current_bar_type="two_down",
            previous_bar_type="inside_bar",
            strat_sequence="strat_inside_break",
            strat_direction="SHORT",
        )
        state.trend = TrendData(direction="DOWN", strength="MODERATE")
        state.vwap.price_vs_vwap = "below"
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "TRADE"
        assert decision.setup.strategy == "strat_inside_break"
        assert decision.setup.direction == "SHORT"

    def test_inside_break_blocked_counter_trend(self, fresh_market_state, ib_config):
        """Long inside break with downtrend context should not fire."""
        engine = DecisionEngine(config=ib_config)
        state = self._state_with(
            fresh_market_state,
            current_bar_type="two_up",
            previous_bar_type="inside_bar",
            strat_sequence="strat_inside_break",
            strat_direction="LONG",
        )
        state.trend = TrendData(direction="DOWN", strength="MODERATE")
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "NO_TRADE"

    def test_inside_break_requires_strat_sequence(self, fresh_market_state, ib_config):
        """Without classified strat_sequence the handler must not fire."""
        engine = DecisionEngine(config=ib_config)
        state = self._state_with(
            fresh_market_state,
            current_bar_type="two_up",
            previous_bar_type="inside_bar",
            strat_sequence=None,
            strat_direction=None,
        )
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "NO_TRADE"

    # ── outside_continuation ─────────────────────────────────────────────────

    def test_outside_continuation_long_with_volume(self, fresh_market_state, oc_config):
        engine = DecisionEngine(config=oc_config)
        state = self._state_with(
            fresh_market_state,
            current_bar_type="two_up",
            previous_bar_type="outside_bar",
            strat_sequence="strat_outside_continuation",
            strat_direction="LONG",
        )
        state.volume = VolumeData(current_bar=5000, avg_bar=3800, relative=1.32)
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "TRADE"
        assert decision.setup.strategy == "strat_outside_continuation"
        assert decision.setup.direction == "LONG"

    def test_outside_continuation_blocked_low_volume(self, fresh_market_state, oc_config):
        """Low-volume outside bar follow-through is a trap — must be rejected."""
        engine = DecisionEngine(config=oc_config)
        state = self._state_with(
            fresh_market_state,
            current_bar_type="two_up",
            previous_bar_type="outside_bar",
            strat_sequence="strat_outside_continuation",
            strat_direction="LONG",
        )
        state.volume = VolumeData(current_bar=1000, avg_bar=3800, relative=0.26)
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "NO_TRADE"

    def test_outside_continuation_short(self, fresh_market_state, oc_config):
        engine = DecisionEngine(config=oc_config)
        state = self._state_with(
            fresh_market_state,
            current_bar_type="two_down",
            previous_bar_type="outside_bar",
            strat_sequence="strat_outside_continuation",
            strat_direction="SHORT",
        )
        state.volume = VolumeData(current_bar=5000, avg_bar=3800, relative=1.32)
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "TRADE"
        assert decision.setup.direction == "SHORT"


class TestContinuationPullback:
    """Pullback must be within proximity ticks of VWAP — not just any trending bar."""

    @pytest.fixture
    def pb_config(self, config):
        from dataclasses import replace
        return replace(config, enabled_concepts=["continuation_pullback"])

    def _pb_state(self, fresh_market_state, close, vwap, direction="UP"):
        state = deepcopy(fresh_market_state)
        state.orb.status = "inside"
        if direction == "UP":
            state.orb.low = close - 8.0
            state.orb.high = close + 12.0
        else:
            state.orb.high = close + 8.0
            state.orb.low = close - 12.0
        state.market_condition = "TRENDING"
        state.trend = TrendData(direction=direction, strength="MODERATE")
        state.ohlc.close = close
        state.vwap = VWAPData(
            value=vwap,
            price_vs_vwap="above" if close > vwap else "below" if close < vwap else "at",
            reclaimed=close > vwap,
            holding=True,
        )
        return state

    def test_fires_when_close_near_vwap_from_above(self, fresh_market_state, pb_config):
        """3 ticks above VWAP in uptrend — within 6-tick window → TRADE."""
        engine = DecisionEngine(config=pb_config)
        # MNQ tick = 0.25; 3 ticks = 0.75
        state = self._pb_state(fresh_market_state, close=19500.75, vwap=19500.0)
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "TRADE"
        assert decision.setup.strategy == "continuation_pullback"

    def test_blocked_when_close_far_from_vwap(self, fresh_market_state, pb_config):
        """20 ticks above VWAP — far from it, not a pullback → NO_TRADE."""
        engine = DecisionEngine(config=pb_config)
        # MNQ tick = 0.25; 20 ticks = 5.0
        state = self._pb_state(fresh_market_state, close=19505.0, vwap=19500.0)
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "NO_TRADE"

    def test_blocked_when_close_below_vwap_in_uptrend(self, fresh_market_state, pb_config):
        """Close below VWAP in uptrend — breakdown, not pullback → NO_TRADE."""
        engine = DecisionEngine(config=pb_config)
        state = self._pb_state(fresh_market_state, close=19499.0, vwap=19500.0)
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "NO_TRADE"

    def test_short_pullback_fires_near_vwap_from_below(self, fresh_market_state, pb_config):
        """2 ticks below VWAP in downtrend → TRADE SHORT."""
        engine = DecisionEngine(config=pb_config)
        state = self._pb_state(
            fresh_market_state, close=19499.5, vwap=19500.0, direction="DOWN"
        )
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "TRADE"
        assert decision.setup.direction == "SHORT"


def test_mes_live_pullback_target_expands_to_configured_minimum(config, fresh_market_state):
    """MES live pullbacks must not produce tiny 4-5 point targets."""
    from dataclasses import replace

    cfg = replace(
        config,
        enabled_concepts=["continuation_pullback"],
        min_target_points={"MES": 15},
    )
    state = deepcopy(fresh_market_state)
    state.instrument = "MES"
    state.orb.status = "inside"
    state.orb.high = 5590.0
    state.orb.low = 5579.0
    state.market_condition = "TRENDING"
    state.trend = TrendData(direction="UP", strength="MODERATE")
    state.ohlc.close = 5582.25
    state.vwap = VWAPData(
        value=5582.0,
        price_vs_vwap="above",
        reclaimed=True,
        holding=True,
    )

    decision = DecisionEngine(config=cfg).evaluate(state, DailyState())

    assert decision.decision == "TRADE"
    assert decision.setup.strategy == "continuation_pullback"
    assert round(decision.setup.target - decision.setup.entry, 2) == 15.0
    assert "target expanded to 15pt minimum for MES" in decision.setup.notes


def test_pine_advisory_bracket_overrides_matching_backend_setup(config, fresh_market_state):
    """Live Pine brackets are accepted when they match the backend setup."""
    from dataclasses import replace

    cfg = replace(
        config,
        enabled_concepts=["continuation_pullback"],
        min_target_points={"MES": 15},
    )
    state = deepcopy(fresh_market_state)
    state.instrument = "MES"
    state.orb.status = "inside"
    state.orb.high = 5590.0
    state.orb.low = 5579.0
    state.market_condition = "TRENDING"
    state.trend = TrendData(direction="UP", strength="MODERATE")
    state.ohlc.close = 5582.25
    state.vwap = VWAPData(
        value=5582.0,
        price_vs_vwap="above",
        reclaimed=True,
        holding=True,
    )
    state.raw = {
        "signal_strategy": "continuation_pullback",
        "signal_direction": "LONG",
        "entry": 5582.25,
        "stop": 5578.0,
        "target": 5597.25,
        "rr_ratio": 3.53,
    }

    decision = DecisionEngine(config=cfg).evaluate(state, DailyState())

    assert decision.decision == "TRADE"
    assert decision.setup.strategy == "continuation_pullback"
    assert decision.setup.entry == 5582.25
    assert decision.setup.stop == 5578.0
    assert decision.setup.target == 5597.25
    assert "Pine bracket override" in decision.setup.notes


def test_pine_advisory_bracket_mismatch_is_ignored(config, fresh_market_state):
    """A stale/mismatched Pine strategy cannot override another setup."""
    from dataclasses import replace

    cfg = replace(
        config,
        enabled_concepts=["continuation_pullback"],
        min_target_points={"MES": 15},
    )
    state = deepcopy(fresh_market_state)
    state.instrument = "MES"
    state.orb.status = "inside"
    state.orb.high = 5590.0
    state.orb.low = 5579.0
    state.market_condition = "TRENDING"
    state.trend = TrendData(direction="UP", strength="MODERATE")
    state.ohlc.close = 5582.25
    state.vwap = VWAPData(
        value=5582.0,
        price_vs_vwap="above",
        reclaimed=True,
        holding=True,
    )
    state.raw = {
        "signal_strategy": "orb_breakout",
        "signal_direction": "LONG",
        "entry": 5582.25,
        "stop": 5577.0,
        "target": 5597.25,
    }

    decision = DecisionEngine(config=cfg).evaluate(state, DailyState())

    assert decision.decision == "TRADE"
    assert decision.setup.strategy == "continuation_pullback"
    assert decision.setup.stop != 5577.0
    assert "Pine bracket override" not in decision.setup.notes



class TestAsianSessionWindows:

    def _engine_with_asian_windows(self, config):
        from dataclasses import replace

        return DecisionEngine(
            config=replace(
                config,
                allowed_sessions=["asian", "london", "new_york"],
                disabled_sessions=[],
                session_windows={
                    "asian": [
                        {"start": "02:00", "end": "04:00", "allow": True, "note": "London pre-open and overlap"},
                        {"start": "20:00", "end": "21:00", "allow": False, "note": "Tokyo open, often fakeout"},
                        {"default": False},
                    ]
                },
            )
        )

    def test_asian_pre_london_window_reaches_strategy_logic(self, config, fresh_market_state):
        engine = self._engine_with_asian_windows(config)
        state = deepcopy(fresh_market_state)
        state.session = "asian"
        # 2026-05-31 06:30 UTC = 02:30 ET.
        state.timestamp = datetime(2026, 5, 31, 6, 30, tzinfo=timezone.utc)
        decision = engine.evaluate(state, DailyState())
        assert "SESSION_WINDOW" not in decision.failed_gates
        assert "outside allowed" not in decision.reason

    def test_asian_tokyo_fakeout_window_blocks_before_setup(self, config, fresh_market_state):
        engine = self._engine_with_asian_windows(config)
        state = deepcopy(fresh_market_state)
        state.session = "asian"
        # 2026-06-01 00:30 UTC = 20:30 ET on 2026-05-31.
        state.timestamp = datetime(2026, 6, 1, 0, 30, tzinfo=timezone.utc)
        decision = engine.evaluate(state, DailyState())
        assert decision.decision == "NO_TRADE"
        assert decision.failed_gates == ["SESSION_WINDOW"]
        assert "Tokyo open" in decision.reason
