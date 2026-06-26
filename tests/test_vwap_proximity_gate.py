"""VWAP-proximity gate: a VWAP-anchored setup must not fire when the live close
has run far from VWAP (no retest to trade), because its entry would rest off-market
and never fill — and, being higher-priority, it blocks momentum setups below it.

Reproduces the 2026-06-26 no-fill day: vwap_hold SHORT firing with the entry pinned
at VWAP while price was 20-490 points below it, so every IOC limit cancelled unfilled.
"""

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from context.market_context import (
    MarketState, PriceData, OHLCData, VWAPData, ORBData,
    PreviousDayData, VolumeData, TrendData,
)
from strategy.signal_engine import DecisionEngine

VWAP = 19495.0
TICK = 0.25  # MNQ


def _short_state(close: float) -> MarketState:
    """A state where vwap_hold SHORT qualifies (below VWAP, downtrend), with the
    live close at ``close``. strat left None so the two_down check is skipped;
    raw empty so the BOS/MSS check is skipped."""
    now = datetime(2026, 6, 26, 14, 30, tzinfo=timezone.utc)
    return MarketState(
        timestamp=now,
        instrument="MNQ",
        session="new_york",
        price=PriceData(last=close, bid=close - 0.25, ask=close + 0.25),
        ohlc=OHLCData(open=close + 2, high=close + 3, low=close - 1, close=close, timeframe="15m"),
        vwap=VWAPData(value=VWAP, price_vs_vwap="below", reclaimed=False, holding=True),
        orb=ORBData(high=19560.0, low=19520.0, timeframe_minutes=15, status="below"),
        previous_day=PreviousDayData(high=19600.0, low=19400.0, close=19550.0),
        volume=VolumeData(current_bar=4200, avg_bar=3800, relative=1.10),
        market_condition="TRENDING",
        trend=TrendData(direction="DOWN", strength="MODERATE", ema_fast_above_slow=False),
        raw={},
    )


def _engine(config, max_ticks: float) -> DecisionEngine:
    return DecisionEngine(replace(config, vwap_entry_max_distance_ticks=max_ticks))


def test_vwap_hold_fires_when_price_near_vwap(config):
    # close 8 ticks (2 pts) below VWAP, gate = 12 ticks → still a retest → fires.
    eng = _engine(config, 12)
    setup = eng._try_vwap_hold(_short_state(VWAP - 2.0))
    assert setup is not None
    assert setup.strategy == "vwap_hold"
    assert setup.direction == "SHORT"


def test_vwap_hold_gated_when_price_far_from_vwap(config):
    # close 60 ticks (15 pts) below VWAP, gate = 12 ticks → no retest → gated out.
    eng = _engine(config, 12)
    assert eng._try_vwap_hold(_short_state(VWAP - 15.0)) is None


def test_gate_disabled_preserves_legacy_behaviour(config):
    # gate = 0 (disabled): vwap_hold fires regardless of distance (the old bug).
    eng = _engine(config, 0)
    assert eng._try_vwap_hold(_short_state(VWAP - 15.0)) is not None


def test_gate_applies_to_reclaim_and_rejection(config):
    eng = _engine(config, 12)
    # vwap_reclaim (LONG): price far ABOVE VWAP → gated
    long_state = replace(
        _short_state(VWAP + 15.0),
        vwap=VWAPData(value=VWAP, price_vs_vwap="above", reclaimed=True, holding=True),
        trend=TrendData(direction="UP", strength="MODERATE", ema_fast_above_slow=True),
    )
    assert eng._try_vwap_reclaim(long_state) is None
    # vwap_rejection (SHORT): reclaimed then closed far below VWAP → gated
    rej_state = replace(
        _short_state(VWAP - 15.0),
        vwap=VWAPData(value=VWAP, price_vs_vwap="below", reclaimed=True, holding=False),
    )
    assert eng._try_vwap_rejection(rej_state) is None
