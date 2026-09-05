"""Regression for the stale ORB bracket (2026-09-04) and orb_breakout re-emission.

Two proven defects starved the MNQ inverse-ORB lane (44/44 orb_breakout setups
ENTRY_DETACHED between 2026-08-25 and 2026-09-04, zero TRADE decisions):

* Pine never clears orb_high/orb_low, so the Asian session carried the
  PREVIOUS day's 09:30 NY range (2026-09-04T04:30Z: 29359.75/29199.25 while
  price was ~29,600).
* orb_breakout fired on the persistent "above"/"below" status, re-emitting the
  same detached bracket for hours after the break.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from context.market_context import OHLCData, ORBData, TrendData, VWAPData, VolumeData
from replay import ReplayCandleLoader, ReplayEngine
from strategy.signal_engine import DecisionEngine
from webhook.payload import AlertPayload
from webhook.state_builder import build_market_state


@pytest.fixture
def engine(config):
    return DecisionEngine(config=config)


# 2026-09-04T04:30Z MNQ, Asian session: Pine still carried the 2026-09-03
# 13:30Z NY opening range while the bar traded ~29,600.
SEPT4_ASIAN_PAYLOAD = dict(
    ticker="MNQ1!",
    timestamp="2026-09-04T04:30:00Z",
    open=29585.0,
    high=29598.5,
    low=29580.25,
    close=29593.2,
    volume=900,
    timeframe="15",
    vwap=29570.0,
    orb_high=29359.75,
    orb_low=29199.25,
    orb_status="above",
    previous_bar_high=29590.0,
    previous_bar_low=29572.0,
    trend_direction="UP",
    market_condition="TRENDING",
)


def _payload(**overrides) -> AlertPayload:
    return AlertPayload(**{**SEPT4_ASIAN_PAYLOAD, **overrides})


def _sample_candle():
    return ReplayCandleLoader().load_jsonl("data/replay/sample_day_mnq.jsonl")[0]


def _replay_state(config, tmp_path, candle):
    return ReplayEngine(config=config, log_dir=str(tmp_path))._market_state_from_candle(candle)


def _fresh_long_break(fresh_market_state, instrument="MES"):
    """Bar right after the break: previous bar still traded at the ORB high."""
    state = deepcopy(fresh_market_state)
    state.instrument = instrument
    state.session = "new_york"
    state.orb = ORBData(high=7500.0, low=7490.0, timeframe_minutes=15, status="above")
    state.trend = TrendData(direction="UP", strength="STRONG", ema_fast_above_slow=True)
    state.vwap = VWAPData(value=7495.0, price_vs_vwap="above", reclaimed=False, holding=True)
    state.ohlc = OHLCData(open=7499.0, high=7503.0, low=7498.5, close=7502.0, timeframe=15)
    state.volume = VolumeData(current_bar=1500.0, avg_bar=1000.0, relative=1.5)
    state.previous_bar_high = 7500.75
    state.previous_bar_low = 7497.0
    return state


def _fresh_short_break(fresh_market_state):
    state = deepcopy(fresh_market_state)
    state.instrument = "MES"
    state.session = "new_york"
    state.orb = ORBData(high=7500.0, low=7490.0, timeframe_minutes=15, status="below")
    state.trend = TrendData(direction="DOWN", strength="STRONG", ema_fast_above_slow=False)
    state.vwap = VWAPData(value=7495.0, price_vs_vwap="below", reclaimed=False, holding=True)
    state.ohlc = OHLCData(open=7491.0, high=7491.5, low=7487.0, close=7488.0, timeframe=15)
    state.volume = VolumeData(current_bar=1500.0, avg_bar=1000.0, relative=1.5)
    state.previous_bar_high = 7493.0
    state.previous_bar_low = 7489.25
    return state


# ── Defect 1: yesterday's NY ORB must not become today's Asian ORB ───────────

def test_sept4_asian_payload_has_no_orb_on_the_live_path(engine):
    state = build_market_state(_payload())
    assert state.session == "asian"
    assert state.orb.status == "undefined"
    # placeholder levels are the bar's own range, never the stale NY range
    assert (state.orb.high, state.orb.low) == (29598.5, 29580.25)
    assert engine._try_orb_breakout(state) is None
    assert engine._try_orb_reclaim(state) is None


def test_sept4_asian_candle_has_no_orb_on_the_replay_path(config, tmp_path):
    candle = replace(
        _sample_candle(),
        session="asian",
        high=29598.5,
        low=29580.25,
        close=29593.2,
        orb_high=29359.75,
        orb_low=29199.25,
        orb_status="above",
    )
    state = _replay_state(config, tmp_path, candle)
    assert state.orb.status == "undefined"
    assert (state.orb.high, state.orb.low) == (29598.5, 29580.25)


def test_live_and_replay_route_the_asian_orb_identically(engine, config, tmp_path):
    live = build_market_state(_payload())
    candle = replace(
        _sample_candle(),
        session="asian",
        high=29598.5,
        low=29580.25,
        close=29593.2,
        orb_high=29359.75,
        orb_low=29199.25,
        orb_status="above",
    )
    replayed = _replay_state(config, tmp_path, candle)
    assert (live.orb.high, live.orb.low, live.orb.status) == (
        replayed.orb.high, replayed.orb.low, replayed.orb.status,
    )


def test_off_hours_payload_has_no_orb_either():
    # 17:00–18:00 ET maintenance halt
    state = build_market_state(_payload(timestamp="2026-09-04T21:15:00Z"))
    assert state.session == "off_hours"
    assert state.orb.status == "undefined"


def test_new_york_payload_still_uses_the_ny_orb():
    state = build_market_state(
        _payload(
            timestamp="2026-09-04T14:00:00Z",
            orb_high=29645.25,
            orb_low=29553.75,
            orb_status="above",
        )
    )
    assert state.session == "new_york"
    assert (state.orb.high, state.orb.low, state.orb.status) == (29645.25, 29553.75, "above")


def test_london_payload_still_uses_the_london_orb():
    state = build_market_state(
        _payload(
            timestamp="2026-09-04T07:15:00Z",
            london_orb_high=29623.75,
            london_orb_low=29575.0,
            london_orb_status="inside",
        )
    )
    assert state.session == "london"
    assert (state.orb.high, state.orb.low, state.orb.status) == (29623.75, 29575.0, "inside")


def test_previous_bar_range_reaches_market_state_on_both_paths(config, tmp_path):
    live = build_market_state(_payload())
    assert (live.previous_bar_high, live.previous_bar_low) == (29590.0, 29572.0)
    candle = replace(_sample_candle(), previous_bar_high=101.5, previous_bar_low=99.25)
    replayed = _replay_state(config, tmp_path, candle)
    assert (replayed.previous_bar_high, replayed.previous_bar_low) == (101.5, 99.25)


# ── Defect 2: orb_breakout must be a fresh break, not "still above" ─────────

def test_fresh_long_breakout_is_still_a_candidate(engine, fresh_market_state):
    setup = engine._try_orb_breakout(_fresh_long_break(fresh_market_state))
    assert setup is not None
    assert (setup.strategy, setup.direction) == ("orb_breakout", "LONG")
    assert setup.entry == pytest.approx(7500.5)


def test_fresh_long_breakout_reaches_the_candidate_list(config, fresh_market_state):
    import dataclasses
    enabled = list(config.enabled_concepts) + ["orb_breakout"]
    engine = DecisionEngine(config=dataclasses.replace(config, enabled_concepts=enabled))
    state = _fresh_long_break(fresh_market_state, instrument="MNQ")
    candidates = engine._find_setup_candidates(state, "TRENDING", None)
    assert any(c.strategy == "orb_breakout" and c.direction == "LONG" for c in candidates)
    # the same engine, one bar later with the range fully above the ORB high,
    # no longer lists it
    state.previous_bar_low = 7501.0
    later = engine._find_setup_candidates(state, "TRENDING", None)
    assert not any(c.strategy == "orb_breakout" for c in later)


def test_fresh_short_breakdown_is_still_a_candidate(engine, fresh_market_state):
    setup = engine._try_orb_breakout(_fresh_short_break(fresh_market_state))
    assert setup is not None
    assert (setup.strategy, setup.direction) == ("orb_breakout", "SHORT")


def test_bar_merely_remaining_above_the_orb_does_not_re_emit(engine, fresh_market_state):
    state = _fresh_long_break(fresh_market_state)
    # previous bar was entirely above the ORB high: the break is old news
    state.previous_bar_low = 7501.0
    assert engine._try_orb_breakout(state) is None


def test_bar_merely_remaining_below_the_orb_does_not_re_emit(engine, fresh_market_state):
    state = _fresh_short_break(fresh_market_state)
    state.previous_bar_high = 7489.0
    assert engine._try_orb_breakout(state) is None


def test_unknown_previous_bar_fails_closed(engine, fresh_market_state):
    state = _fresh_long_break(fresh_market_state)
    state.previous_bar_high = None
    state.previous_bar_low = None
    assert engine._try_orb_breakout(state) is None


def test_sept3_new_york_persistent_above_case_no_longer_emits(engine, fresh_market_state):
    # 2026-09-03T15:15Z: ORB 29359.75/29199.25 formed at 13:30Z, close 29464.75,
    # TRENDING, vol 2.06x — the old code emitted a LONG at 29360.25, 104 pts
    # under the market, and ENTRY_DETACHED rejected it.
    state = deepcopy(fresh_market_state)
    state.instrument = "MNQ"
    state.session = "new_york"
    state.orb = ORBData(high=29359.75, low=29199.25, timeframe_minutes=15, status="above")
    state.trend = TrendData(direction="UP", strength="STRONG", ema_fast_above_slow=True)
    state.vwap = VWAPData(value=29400.0, price_vs_vwap="above", reclaimed=False, holding=True)
    state.ohlc = OHLCData(open=29440.0, high=29470.0, low=29435.0, close=29464.75, timeframe=15)
    state.volume = VolumeData(current_bar=2060.0, avg_bar=1000.0, relative=2.06)
    state.previous_bar_high = 29445.0
    state.previous_bar_low = 29420.0
    assert engine._try_orb_breakout(state) is None


# ── Identity: orb_breakout stays on above/below, orb_reclaim on reclaimed_* ──

def test_orb_breakout_never_fires_on_reclaimed_high(engine, fresh_market_state):
    state = _fresh_long_break(fresh_market_state)
    state.orb = replace(state.orb, status="reclaimed_high")
    assert engine._try_orb_breakout(state) is None


def test_orb_reclaim_never_fires_on_above(engine, fresh_market_state):
    assert engine._try_orb_reclaim(_fresh_long_break(fresh_market_state)) is None


def test_orb_reclaim_trigger_is_unchanged(engine, fresh_market_state):
    # conftest's fresh_market_state carries status="reclaimed_high"
    assert fresh_market_state.orb.status == "reclaimed_high"
    setup = engine._try_orb_reclaim(fresh_market_state)
    assert setup is not None and setup.strategy == "orb_reclaim"
