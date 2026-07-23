"""Focused Polygon-generation and replay-routing parity for the London ORB."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from replay import ReplayCandleLoader, ReplayEngine
from scripts.polygon_to_replay import ORB_MINUTES, derive_candles


ET = ZoneInfo("America/New_York")


def _bar(ts: datetime, *, o: float, h: float, l: float, c: float) -> dict:
    return {
        "ts": int(ts.timestamp()),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 100,
    }


def _polygon_bars() -> list[dict]:
    """Continuous 5m bars with two exact London opens and a known day-N range."""
    start = datetime(2026, 6, 8, 9, 30, tzinfo=ET)
    end = datetime(2026, 6, 10, 3, 5, tzinfo=ET)
    special = {
        # Day N: three-bar 15-minute London ORB, then all four transition states.
        datetime(2026, 6, 9, 3, 0, tzinfo=ET): (105, 110, 100, 105),
        datetime(2026, 6, 9, 3, 5, tzinfo=ET): (105, 112, 99, 111),
        datetime(2026, 6, 9, 3, 10, tzinfo=ET): (111, 111.5, 98, 99),
        datetime(2026, 6, 9, 3, 15, tzinfo=ET): (99, 120, 99, 113),
        datetime(2026, 6, 9, 3, 20, tzinfo=ET): (113, 114, 110, 111),
        datetime(2026, 6, 9, 3, 25, tzinfo=ET): (111, 112, 96, 97),
        datetime(2026, 6, 9, 3, 30, tzinfo=ET): (97, 100, 96, 99),
        # Day N+1 must overwrite the frozen day-N 112/98 range immediately.
        datetime(2026, 6, 10, 3, 0, tzinfo=ET): (205, 210, 200, 205),
        datetime(2026, 6, 10, 3, 5, tzinfo=ET): (205, 211, 199, 206),
    }
    bars = []
    ts = start
    while ts <= end:
        o, h, l, c = special.get(ts, (105, 106, 104, 105))
        bars.append(_bar(ts, o=o, h=h, l=l, c=c))
        ts += timedelta(minutes=5)
    return bars


@pytest.fixture(scope="module")
def london_candles() -> dict[datetime, dict]:
    candles = derive_candles(_polygon_bars(), "MNQ", 5)
    return {
        datetime.fromisoformat(candle["timestamp"]).astimezone(ET): candle
        for candle in candles
    }


def test_london_reset_is_immediate_at_exact_0300(london_candles):
    opening = london_candles[datetime(2026, 6, 9, 3, 0, tzinfo=ET)]
    assert opening["london_orb_high"] == 110
    assert opening["london_orb_low"] == 100
    assert opening["london_orb_status"] == "inside"


def test_london_uses_existing_orb_bar_count_and_accumulates_then_freezes(
    london_candles,
):
    assert ORB_MINUTES == 15
    second = london_candles[datetime(2026, 6, 9, 3, 5, tzinfo=ET)]
    completed = london_candles[datetime(2026, 6, 9, 3, 10, tzinfo=ET)]
    after = london_candles[datetime(2026, 6, 9, 3, 15, tzinfo=ET)]
    assert (second["london_orb_high"], second["london_orb_low"]) == (112, 99)
    assert (completed["london_orb_high"], completed["london_orb_low"]) == (112, 98)
    # The 03:15 bar's own 120/99 range cannot mutate a completed three-bar ORB.
    assert (after["london_orb_high"], after["london_orb_low"]) == (112, 98)


def test_frozen_london_range_persists_outside_london(london_candles):
    noon = london_candles[datetime(2026, 6, 9, 12, 0, tzinfo=ET)]
    assert noon["session"] == "new_york"
    assert (noon["london_orb_high"], noon["london_orb_low"]) == (112, 98)


def test_next_day_0300_immediately_overwrites_prior_london_range(london_candles):
    next_open = london_candles[datetime(2026, 6, 10, 3, 0, tzinfo=ET)]
    assert (next_open["london_orb_high"], next_open["london_orb_low"]) == (210, 200)
    assert next_open["london_orb_status"] is not None


def test_unfinished_london_orb_does_not_absorb_later_non_london_bar():
    london_open = datetime(2026, 6, 9, 3, 0, tzinfo=ET)
    ny_open = datetime(2026, 6, 9, 9, 30, tzinfo=ET)
    bars = []
    for bar in _polygon_bars():
        bar_et = datetime.fromtimestamp(bar["ts"], tz=ET)
        if bar_et <= london_open:
            bars.append(bar)
        elif bar_et == ny_open:
            bars.append(
                _bar(ny_open, o=105, h=150, l=50, c=105)
            )
            break

    candles = derive_candles(bars, "MNQ", 5)
    ny_candle = next(
        candle
        for candle in candles
        if datetime.fromisoformat(candle["timestamp"]).astimezone(ET) == ny_open
    )
    assert ny_candle["session"] == "new_york"
    assert (ny_candle["london_orb_high"], ny_candle["london_orb_low"]) == (110, 100)


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [
        (3, 15, "reclaimed_high"),
        (3, 20, "rejected_high"),
        (3, 25, "reclaimed_low"),
        (3, 30, "rejected_low"),
    ],
)
def test_london_transition_states_use_shared_status_helper(
    london_candles, hour, minute, expected
):
    candle = london_candles[datetime(2026, 6, 9, hour, minute, tzinfo=ET)]
    assert candle["london_orb_status"] == expected


def _sample_candle():
    return ReplayCandleLoader().load_jsonl("data/replay/sample_day_mnq.jsonl")[0]


def test_replay_london_uses_present_london_fields_verbatim(config, tmp_path):
    candle = replace(
        _sample_candle(),
        session="london",
        london_orb_high=210,
        london_orb_low=200,
        london_orb_status="rejected_high",
    )
    state = ReplayEngine(config=config, log_dir=str(tmp_path))._market_state_from_candle(
        candle
    )
    assert (state.orb.high, state.orb.low) == (210, 200)
    assert state.orb.status == "rejected_high"


def test_replay_legacy_london_candle_fails_closed_without_ny_fallback(
    config, tmp_path
):
    candle = replace(
        _sample_candle(),
        session="london",
        high=110,
        low=100,
        orb_high=999,
        orb_low=998,
        orb_status="above",
        london_orb_high=None,
        london_orb_low=None,
        london_orb_status=None,
    )
    state = ReplayEngine(config=config, log_dir=str(tmp_path))._market_state_from_candle(
        candle
    )
    assert (state.orb.high, state.orb.low) == (110, 100)
    assert state.orb.status == "undefined"


def test_replay_new_york_still_uses_standard_orb(config, tmp_path):
    candle = replace(
        _sample_candle(),
        session="new_york",
        orb_high=120,
        orb_low=90,
        orb_status="above",
        london_orb_high=210,
        london_orb_low=200,
        london_orb_status="rejected_high",
    )
    state = ReplayEngine(config=config, log_dir=str(tmp_path))._market_state_from_candle(
        candle
    )
    assert (state.orb.high, state.orb.low) == (120, 90)
    assert state.orb.status == "above"


def test_next_day_generated_london_candle_routes_new_not_prior_range(
    london_candles, config, tmp_path
):
    raw = london_candles[datetime(2026, 6, 10, 3, 0, tzinfo=ET)]
    candle = ReplayCandleLoader._parse(raw)
    state = ReplayEngine(config=config, log_dir=str(tmp_path))._market_state_from_candle(
        candle
    )
    assert state.session == "london"
    assert (state.orb.high, state.orb.low) == (210, 200)
    assert (state.orb.high, state.orb.low) != (112, 98)
