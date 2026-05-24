"""
tests/conftest.py

Shared fixtures for all test modules.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import SystemConfig
from context.market_context import (
    MarketState, PriceData, OHLCData, VWAPData, ORBData,
    PreviousDayData, VolumeData, TrendData,
)
from risk.risk_engine import DailyState, TradeSetup


# ─── Config Fixture ────────────────────────────────────────────────────────────

@pytest.fixture
def config() -> SystemConfig:
    """Standard test config — paper mode, LIVE_TRADING_ENABLED=false."""
    return SystemConfig(
        live_trading_enabled=False,
        paper_mode=True,
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
        enabled_concepts=[
            "orb_reclaim", "orb_rejection", "vwap_reclaim",
            "vwap_hold", "pdh_reclaim", "pdl_reclaim", "continuation_pullback"
        ],
        log_dir="logs_test",
        log_level="WARNING",
        risk_rules_path="risk_rules.yaml",
    )


@pytest.fixture
def clean_daily_state() -> DailyState:
    """Fresh daily state — no trades, no losses, no open position."""
    return DailyState(trade_count=0, consecutive_losses=0, has_open_position=False)


@pytest.fixture
def fresh_market_state() -> MarketState:
    """A valid, fresh MNQ ORB reclaim market state."""
    now = datetime.now(timezone.utc)
    return MarketState(
        timestamp=now,
        instrument="MNQ",
        session="new_york",
        price=PriceData(last=19505.25, bid=19505.00, ask=19505.50),
        ohlc=OHLCData(
            open=19480.0, high=19510.0, low=19475.0, close=19505.25, timeframe="5m"
        ),
        vwap=VWAPData(value=19495.0, price_vs_vwap="above", reclaimed=True, holding=True),
        orb=ORBData(high=19498.0, low=19462.0, timeframe_minutes=15, status="reclaimed_high"),
        previous_day=PreviousDayData(high=19520.0, low=19440.0, close=19475.0),
        volume=VolumeData(current_bar=4200, avg_bar=3800, relative=1.10),
        market_condition="TRENDING",
        trend=TrendData(direction="UP", strength="MODERATE", ema_fast_above_slow=True),
        raw={},
    )


@pytest.fixture
def valid_trade_setup() -> TradeSetup:
    return TradeSetup(
        direction="LONG",
        entry=19500.0,
        stop=19480.0,
        target=19540.0,
        rr_ratio=2.0,
        strategy="orb_reclaim",
        instrument="MNQ",
        session="new_york",
    )
