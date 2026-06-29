"""Momentum entry re-anchor: for LIMIT/level setups whose price has already moved in
the trade's favor past the resting entry (the trend-day no-fill case), re-anchor the
entry to the live close and rebuild stop/target preserving the original risk/reward.
Bounded so it can never chase a feed-gap dislocation. Gated, default off.
"""

from dataclasses import replace
from datetime import datetime, timezone

from context.market_context import (
    MarketState, PriceData, OHLCData, VWAPData, ORBData,
    PreviousDayData, VolumeData, TrendData,
)
from risk.risk_engine import RiskEngine
from strategy.signal_engine import DecisionEngine, SetupDetail

TICK = 0.25  # MNQ


def _state(close: float) -> MarketState:
    now = datetime(2026, 6, 26, 14, 30, tzinfo=timezone.utc)
    return MarketState(
        timestamp=now, instrument="MNQ", session="new_york",
        price=PriceData(last=close, bid=close - 0.25, ask=close + 0.25),
        ohlc=OHLCData(open=close, high=close + 3, low=close - 3, close=close, timeframe="15m"),
        vwap=VWAPData(value=close, price_vs_vwap="below", reclaimed=False, holding=True),
        orb=ORBData(high=19560.0, low=19520.0, timeframe_minutes=15, status="below"),
        previous_day=PreviousDayData(high=19600.0, low=19400.0, close=19550.0),
        volume=VolumeData(current_bar=4200, avg_bar=3800, relative=1.10),
        market_condition="TRENDING",
        trend=TrendData(direction="DOWN", strength="MODERATE", ema_fast_above_slow=False),
        raw={},
    )


def _short_setup(entry=19500.0, stop=19507.5, target=19485.0):
    # SHORT vwap_hold: risk 7.5, reward 15 → R:R 2.0
    return SetupDetail(direction="SHORT", entry=entry, stop=stop, target=target,
                       rr_ratio=2.0, strategy="vwap_hold", notes="base")


def _eng(config, *, on=True):
    return DecisionEngine(replace(config, momentum_entry_reanchor=on))


def test_flag_off_is_noop(config):
    eng = _eng(config, on=False)
    setup = _short_setup()
    out = eng._maybe_reanchor_entry(setup, _state(19490.0))
    assert out is setup  # unchanged object


def test_short_reanchors_to_close_preserving_rr(config):
    eng = _eng(config)
    setup = _short_setup(entry=19500.0, stop=19507.5, target=19485.0)  # risk 7.5, reward 15
    # price ran DOWN to 19490 (10pt below entry, inside the 15pt reward) → would miss → re-anchor
    out = eng._maybe_reanchor_entry(setup, _state(19490.0))
    assert out.entry == 19490.0
    assert out.stop == 19497.5    # close + risk(7.5)
    assert out.target == 19475.0  # close - reward(15)
    assert out.rr_ratio == RiskEngine.calculate_rr("SHORT", 19490.0, 19497.5, 19475.0) == 2.0
    assert "re-anchor" in out.notes


def test_long_reanchors_symmetric(config):
    eng = _eng(config)
    setup = SetupDetail(direction="LONG", entry=19500.0, stop=19492.5, target=19515.0,
                        rr_ratio=2.0, strategy="pdh_reclaim", notes=None)  # risk 7.5, reward 15
    out = eng._maybe_reanchor_entry(setup, _state(19510.0))  # ran up 10pt, inside reward
    assert out.entry == 19510.0
    assert out.stop == 19502.5    # close - risk
    assert out.target == 19525.0  # close + reward
    assert out.rr_ratio == 2.0


def test_orb_breakout_reanchors(config):
    # orb_breakout entry rests at orb.high+2t; on a live momentum break the bar
    # closes ABOVE it and the broker's IOC limit entry (slippage-cap path) can't
    # fill above its price → 100% no-fill in live demo. Re-anchor to the close.
    eng = _eng(config)
    setup = SetupDetail(direction="LONG", entry=19500.0, stop=19492.5, target=19515.0,
                        rr_ratio=2.0, strategy="orb_breakout", notes=None)  # risk 7.5, reward 15
    out = eng._maybe_reanchor_entry(setup, _state(19510.0))  # ran up 10pt, inside reward
    assert out.entry == 19510.0
    assert out.stop == 19502.5    # close - risk
    assert out.target == 19525.0  # close + reward
    assert out.rr_ratio == 2.0
    assert "re-anchor" in out.notes


def test_orb_reclaim_reanchors(config):
    eng = _eng(config)
    setup = SetupDetail(direction="LONG", entry=19500.0, stop=19492.5, target=19515.0,
                        rr_ratio=2.0, strategy="orb_reclaim", notes=None)
    out = eng._maybe_reanchor_entry(setup, _state(19510.0))
    assert out.entry == 19510.0
    assert out.stop == 19502.5
    assert out.target == 19525.0


def test_continuation_pullback_not_reanchored(config):
    # continuation_pullback already enters AT the close, so it needs no re-anchor.
    eng = _eng(config)
    setup = replace(_short_setup(), strategy="continuation_pullback")
    out = eng._maybe_reanchor_entry(setup, _state(19490.0))
    assert out is setup  # genuinely excluded — enters at close


def test_gap_past_bracket_not_reanchored(config):
    # close 20pt below entry > 15pt reward → past the bracket → leave for detachment guard
    eng = _eng(config)
    setup = _short_setup(entry=19500.0, stop=19507.5, target=19485.0)
    out = eng._maybe_reanchor_entry(setup, _state(19480.0))
    assert out is setup


def test_within_tick_not_reanchored(config):
    # close only ~adverse-to-fill side / not beyond a tick in favor → no re-anchor
    eng = _eng(config)
    setup = _short_setup(entry=19500.0)
    out = eng._maybe_reanchor_entry(setup, _state(19500.0))  # gap 0
    assert out is setup
