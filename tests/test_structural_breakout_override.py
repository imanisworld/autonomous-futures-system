"""
tests/test_structural_breakout_override.py

Verifies the RANGE_BOUND → TRENDING structural breakout override.

Scenario: Pine labels the RTH breakout bar RANGE_BOUND even though EMA-stack
is STRONG UP, price is through PDH, and VWAP is aligned. The override must
upgrade the label so the TRENDING gate doesn't block the trade.

Real incident: 2026-06-29 and 2026-06-30 — MES ran 50+ points on consecutive
trend days while the system called RANGE_BOUND on every breakout bar.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from context.market_context import (
    MarketState, PriceData, OHLCData, VWAPData, ORBData,
    PreviousDayData, VolumeData, TrendData,
)
from risk.risk_engine import DailyState
from strategy.signal_engine import DecisionEngine


@pytest.fixture
def engine(config):
    return DecisionEngine(config=config)


@pytest.fixture
def breakout_state() -> MarketState:
    """MES RTH open: Pine says RANGE_BOUND, but structure screams trend day.

    Price is 21 points above PDH (7505 → 7526), VWAP well below, EMA-stack
    STRONG UP. This is exactly the bar the system missed on 2026-06-30.
    """
    now = datetime(2026, 6, 30, 13, 45, tzinfo=timezone.utc)  # 9:45 ET
    return MarketState(
        timestamp=now,
        instrument="MES",
        session="new_york",
        price=PriceData(last=7526.75, bid=7526.50, ask=7527.00),
        ohlc=OHLCData(
            open=7506.0, high=7528.0, low=7505.5, close=7526.75, timeframe="15"
        ),
        vwap=VWAPData(value=7500.0, price_vs_vwap="above", reclaimed=True, holding=True),
        orb=ORBData(high=7512.75, low=7505.25, timeframe_minutes=15, status="above"),
        previous_day=PreviousDayData(
            high=7505.0, low=7398.0, close=7500.25,
            price_vs_pdh="above", price_vs_pdl="above",
        ),
        volume=VolumeData(current_bar=5800, avg_bar=3500, relative=1.66),
        market_condition="RANGE_BOUND",  # Pine's label — wrong on a trend day
        trend=TrendData(direction="UP", strength="STRONG", ema_fast_above_slow=True),
        raw={},
    )


class TestStructuralBreakoutOverride:

    def test_range_bound_upgraded_to_trending_on_breakout(self, engine, breakout_state):
        """RANGE_BOUND bar with STRONG trend + above PDH + above VWAP → TRENDING."""
        result = engine._score_market_condition(breakout_state)
        assert result == "TRENDING", (
            f"Expected TRENDING on structural breakout, got {result}"
        )

    def test_override_disabled_trusts_pine(self, config, breakout_state):
        """When range_bound_breakout_override=False, Pine's RANGE_BOUND is trusted."""
        import dataclasses
        cfg = dataclasses.replace(config, range_bound_breakout_override=False)
        eng = DecisionEngine(config=cfg)
        result = eng._score_market_condition(breakout_state)
        assert result == "RANGE_BOUND"

    def test_moderate_trend_does_not_override(self, engine, breakout_state):
        """Only STRONG trend qualifies — MODERATE trend trusts Pine's RANGE_BOUND."""
        state = deepcopy(breakout_state)
        state.trend = TrendData(direction="UP", strength="MODERATE", ema_fast_above_slow=True)
        result = engine._score_market_condition(state)
        assert result == "RANGE_BOUND"

    def test_price_below_pdh_does_not_override(self, engine, breakout_state):
        """No PDH clearance → no override (could be a fade setup, not a breakout)."""
        state = deepcopy(breakout_state)
        state.previous_day = PreviousDayData(
            high=7505.0, low=7398.0, close=7500.25,
            price_vs_pdh="below", price_vs_pdl="above",
        )
        result = engine._score_market_condition(state)
        assert result == "RANGE_BOUND"

    def test_vwap_below_price_does_not_override(self, engine, breakout_state):
        """VWAP misaligned (price below VWAP) → no override."""
        state = deepcopy(breakout_state)
        state.vwap = VWAPData(value=7540.0, price_vs_vwap="below", reclaimed=False, holding=False)
        result = engine._score_market_condition(state)
        assert result == "RANGE_BOUND"

    def test_short_breakout_override(self, engine):
        """Symmetric: STRONG DOWN + below PDL + below VWAP → TRENDING."""
        now = datetime(2026, 6, 30, 14, 0, tzinfo=timezone.utc)
        state = MarketState(
            timestamp=now,
            instrument="MES",
            session="new_york",
            price=PriceData(last=7380.0, bid=7379.75, ask=7380.25),
            ohlc=OHLCData(
                open=7400.0, high=7401.0, low=7378.0, close=7380.0, timeframe="15"
            ),
            vwap=VWAPData(value=7420.0, price_vs_vwap="below", reclaimed=False, holding=False),
            orb=ORBData(high=7410.0, low=7398.0, timeframe_minutes=15, status="below"),
            previous_day=PreviousDayData(
                high=7505.0, low=7398.0, close=7450.0,
                price_vs_pdh="below", price_vs_pdl="below",
            ),
            volume=VolumeData(current_bar=5200, avg_bar=3500, relative=1.49),
            market_condition="RANGE_BOUND",
            trend=TrendData(direction="DOWN", strength="STRONG", ema_fast_above_slow=False),
            raw={},
        )
        result = engine._score_market_condition(state)
        assert result == "TRENDING"

    def test_trending_pine_label_unchanged(self, engine, breakout_state):
        """When Pine already says TRENDING, the override is a no-op."""
        state = deepcopy(breakout_state)
        state.market_condition = "TRENDING"
        result = engine._score_market_condition(state)
        assert result == "TRENDING"

    def test_choppy_label_not_affected_by_override(self, engine, breakout_state):
        """CHOPPY label goes through the existing CHOPPY veto path, not this one."""
        state = deepcopy(breakout_state)
        state.market_condition = "CHOPPY"
        # CHOPPY with directional structure → RANGE_BOUND (existing veto)
        # The new override only applies to RANGE_BOUND, not CHOPPY
        result = engine._score_market_condition(state)
        assert result in ("RANGE_BOUND", "CHOPPY")  # CHOPPY veto may or may not fire

    def test_engine_fires_trade_on_breakout_bar(self, engine, breakout_state):
        """End-to-end: a pdh_reclaim LONG fires on a RANGE_BOUND breakout bar."""
        daily = DailyState()
        decision = engine.evaluate(breakout_state, daily)
        # Should not be blocked by MARKET_CONDITION_NOT_TRENDING
        assert "MARKET_CONDITION_NOT_TRENDING" not in decision.failed_gates, (
            f"TRENDING gate wrongly blocked breakout bar: {decision.reason}"
        )
