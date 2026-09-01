"""Regression coverage for canonical 2-1-2 reversal confluence scoring.

Relabeling these bars out of ``strat_inside_break`` must not silently remove
the +3 Strat confirmation bonus they already received under the legacy label.
"""

from datetime import datetime, timezone

import pytest

from context.market_context import (
    MarketState,
    OHLCData,
    ORBData,
    PreviousDayData,
    PriceData,
    StratContext,
    VWAPData,
    VolumeData,
)
from strategy.confluence_scorer import score_setup
from strategy.signal_engine import SetupDetail


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_strat_212_reversal_preserves_strat_confirmation_bonus(direction):
    state = MarketState(
        timestamp=datetime.now(timezone.utc),
        instrument="MNQ",
        session="london",
        price=PriceData(last=100.0, bid=99.75, ask=100.25),
        ohlc=OHLCData(open=99.0, high=101.0, low=98.0, close=100.0, timeframe="5m"),
        vwap=VWAPData(value=100.0, price_vs_vwap="at"),
        orb=ORBData(high=105.0, low=95.0, timeframe_minutes=15, status="inside"),
        previous_day=PreviousDayData(high=110.0, low=90.0, close=100.0),
        volume=VolumeData(current_bar=1000, avg_bar=1000, relative=1.0),
        strat=StratContext(
            current_bar_type="two_up" if direction == "LONG" else "two_down",
            previous_bar_type="inside_bar",
            two_bars_back_type="two_down" if direction == "LONG" else "two_up",
            strat_sequence="strat_212_reversal",
            strat_trigger="reversal",
            strat_direction=direction,
        ),
        raw={},
    )
    setup = SetupDetail(
        direction=direction,
        entry=100.0,
        stop=95.0 if direction == "LONG" else 105.0,
        target=105.0 if direction == "LONG" else 95.0,
        rr_ratio=1.0,
        strategy="orb_breakout",
    )

    result = score_setup(state, setup)

    assert result.score == 3
    assert "Strat strat_212_reversal confirmed (+3)" in result.factors
