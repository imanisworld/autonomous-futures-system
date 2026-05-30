"""
tests/test_confluence_scorer.py

Unit tests for strategy/confluence_scorer.py.

Tests are self-contained — no pytest fixture dependencies.
Use the _state() / _setup() helpers to build variants via keyword args.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from context.market_context import (
    MarketState, PriceData, OHLCData, VWAPData, ORBData,
    PreviousDayData, VolumeData, TrendData,
)
from strategy.confluence_scorer import ConfluenceScore, score_setup
from strategy.signal_engine import SetupDetail
from strategy.strat_classifier import StratContext


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _setup(direction: str = "LONG") -> SetupDetail:
    entry = 19505.25
    stop = 19485.25
    target = 19545.25
    return SetupDetail(
        direction=direction,
        entry=entry,
        stop=stop,
        target=target,
        rr_ratio=2.0,
        strategy="orb_reclaim",
    )


def _state(
    session: str = "new_york",
    price_vs_vwap: str = "above",
    trend_direction: str | None = "UP",
    trend_strength: str | None = "MODERATE",
    volume_relative: float | None = 1.0,
    orb_status: str | None = "reclaimed_high",
    strat_sequence: str | None = None,
    strat_direction: str | None = None,
) -> MarketState:
    strat = None
    if strat_sequence is not None or strat_direction is not None:
        strat = StratContext(
            current_bar_type="two_up",
            previous_bar_type="inside_bar",
            two_bars_back_type="two_up",
            strat_sequence=strat_sequence,
            strat_trigger="continuation",
            strat_direction=strat_direction,
        )

    trend = None
    if trend_direction is not None:
        trend = TrendData(
            direction=trend_direction,
            strength=trend_strength,
            ema_fast_above_slow=(trend_direction == "UP"),
        )

    return MarketState(
        timestamp=datetime.now(timezone.utc),
        instrument="MNQ",
        session=session,
        price=PriceData(last=19505.25, bid=19505.00, ask=19505.50),
        ohlc=OHLCData(open=19480.0, high=19510.0, low=19475.0, close=19505.25, timeframe="5m"),
        vwap=VWAPData(value=19495.0, price_vs_vwap=price_vs_vwap, reclaimed=True, holding=True),
        orb=ORBData(high=19498.0, low=19462.0, timeframe_minutes=15, status=orb_status),
        previous_day=PreviousDayData(high=19520.0, low=19440.0, close=19475.0),
        volume=VolumeData(
            current_bar=int((volume_relative or 1.0) * 3800),
            avg_bar=3800,
            relative=volume_relative,
        ),
        market_condition="TRENDING",
        trend=trend,
        strat=strat,
        raw={},
    )


# ─── Grade tiers ─────────────────────────────────────────────────────────────

class TestGrades:
    def test_a_plus_with_all_factors(self):
        state = _state(
            price_vs_vwap="above",
            trend_direction="UP",
            trend_strength="STRONG",
            volume_relative=1.4,
            orb_status="reclaimed_high",
            strat_sequence="strat_212",
            strat_direction="LONG",
        )
        result = score_setup(state, _setup("LONG"))
        assert result.grade == "A+"
        assert result.score >= 9

    def test_a_grade(self):
        # VWAP(2) + trend UP MODERATE(2) + volume 1.3x(2) + NY(1) + ORB(1) = 8
        state = _state(
            price_vs_vwap="above",
            trend_direction="UP",
            trend_strength="MODERATE",
            volume_relative=1.3,
            orb_status="reclaimed_high",
        )
        result = score_setup(state, _setup("LONG"))
        assert result.grade == "A"
        assert result.score == 8

    def test_b_grade(self):
        # VWAP(2) + trend UP(2) + NY(1) = 5; add ORB(1) = 6
        state = _state(
            price_vs_vwap="above",
            trend_direction="UP",
            trend_strength="MODERATE",
            volume_relative=0.9,   # no volume bonus
            orb_status="reclaimed_high",
        )
        result = score_setup(state, _setup("LONG"))
        assert result.grade in ("B", "C")
        assert 5 <= result.score <= 7

    def test_c_grade(self):
        # VWAP(2) + NY(1) + ORB(1) = 4 — but trend is SIDEWAYS so no trend points, no penalty
        # and no volume bonus
        state = _state(
            price_vs_vwap="above",
            trend_direction="SIDEWAYS",
            volume_relative=0.9,
            orb_status="reclaimed_high",
        )
        result = score_setup(state, _setup("LONG"))
        assert result.score <= 5

    def test_weak_grade(self):
        # Only NY session in weak conditions
        state = _state(
            price_vs_vwap="below",     # VWAP not aligned
            trend_direction=None,       # no trend data
            volume_relative=0.6,        # low volume penalty
            orb_status="inside",        # ORB not confirming
        )
        result = score_setup(state, _setup("LONG"))
        assert result.grade == "WEAK"
        assert result.score < 5


# ─── Individual factor scoring ───────────────────────────────────────────────

class TestFactors:
    def test_strat_212_long_adds_three(self):
        state = _state(strat_sequence="strat_212", strat_direction="LONG",
                       trend_direction=None, price_vs_vwap="at",
                       volume_relative=1.0, orb_status="inside", session="london")
        result = score_setup(state, _setup("LONG"))
        assert any("strat_212" in f for f in result.factors)
        assert result.score >= 3

    def test_strat_122_short_adds_three(self):
        state = _state(strat_sequence="strat_122", strat_direction="SHORT",
                       price_vs_vwap="below", trend_direction=None,
                       volume_relative=1.0, orb_status="inside", session="london")
        result = score_setup(state, _setup("SHORT"))
        assert any("strat_122" in f for f in result.factors)

    def test_strat_inside_break_adds_three(self):
        state = _state(strat_sequence="strat_inside_break", strat_direction="LONG",
                       trend_direction=None, price_vs_vwap="at",
                       volume_relative=1.0, orb_status="inside", session="london")
        result = score_setup(state, _setup("LONG"))
        assert any("strat_inside_break" in f for f in result.factors)

    def test_strat_outside_continuation_does_not_add_strat_bonus(self):
        # strat_outside_continuation is NOT in the scored sequences
        state = _state(strat_sequence="strat_outside_continuation", strat_direction="LONG",
                       trend_direction=None, price_vs_vwap="at",
                       volume_relative=1.0, orb_status="inside", session="london")
        result = score_setup(state, _setup("LONG"))
        assert not any("strat_outside_continuation" in f for f in result.factors)

    def test_vwap_above_long_adds_two(self):
        state = _state(price_vs_vwap="above", trend_direction=None,
                       volume_relative=1.0, orb_status="inside", session="london")
        result = score_setup(state, _setup("LONG"))
        assert any("VWAP" in f for f in result.factors)
        assert result.score >= 2

    def test_vwap_below_short_adds_two(self):
        state = _state(price_vs_vwap="below", trend_direction=None,
                       volume_relative=1.0, orb_status="inside", session="london")
        result = score_setup(state, _setup("SHORT"))
        assert any("VWAP" in f for f in result.factors)

    def test_vwap_above_short_no_vwap_points(self):
        state = _state(price_vs_vwap="above", trend_direction=None,
                       volume_relative=1.0, orb_status="inside", session="london")
        result = score_setup(state, _setup("SHORT"))
        assert not any("VWAP" in f for f in result.factors)

    def test_trend_up_long_adds_two(self):
        state = _state(price_vs_vwap="at", trend_direction="UP", trend_strength="MODERATE",
                       volume_relative=1.0, orb_status="inside", session="london")
        result = score_setup(state, _setup("LONG"))
        assert any("Trend UP" in f for f in result.factors)
        assert result.score >= 2

    def test_trend_down_short_adds_two(self):
        state = _state(price_vs_vwap="at", trend_direction="DOWN", trend_strength="MODERATE",
                       volume_relative=1.0, orb_status="inside", session="london")
        result = score_setup(state, _setup("SHORT"))
        assert any("Trend DOWN" in f for f in result.factors)

    def test_trend_up_short_no_trend_points(self):
        state = _state(price_vs_vwap="at", trend_direction="UP", trend_strength="MODERATE",
                       volume_relative=1.0, orb_status="inside", session="london")
        result = score_setup(state, _setup("SHORT"))
        assert not any("Trend UP" in f for f in result.factors)

    def test_volume_above_1p2_adds_two(self):
        state = _state(price_vs_vwap="at", trend_direction=None,
                       volume_relative=1.2, orb_status="inside", session="london")
        result = score_setup(state, _setup("LONG"))
        assert any("Volume" in f and "(+2)" in f for f in result.factors)

    def test_volume_exactly_1p1_no_volume_points(self):
        state = _state(price_vs_vwap="at", trend_direction=None,
                       volume_relative=1.1, orb_status="inside", session="london")
        result = score_setup(state, _setup("LONG"))
        assert not any("Volume" in f and "(+2)" in f for f in result.factors)

    def test_ny_session_adds_one(self):
        state = _state(price_vs_vwap="at", trend_direction=None,
                       volume_relative=1.0, orb_status="inside", session="new_york")
        result = score_setup(state, _setup("LONG"))
        assert any("NY session" in f for f in result.factors)

    def test_london_session_no_session_points(self):
        state = _state(price_vs_vwap="at", trend_direction=None,
                       volume_relative=1.0, orb_status="inside", session="london")
        result = score_setup(state, _setup("LONG"))
        assert not any("NY session" in f for f in result.factors)

    def test_strong_trend_bonus_when_aligned(self):
        state = _state(price_vs_vwap="at", trend_direction="UP", trend_strength="STRONG",
                       volume_relative=1.0, orb_status="inside", session="london")
        result = score_setup(state, _setup("LONG"))
        assert any("Strong trend bonus" in f for f in result.factors)
        assert result.score >= 3  # trend(2) + bonus(1)

    def test_strong_trend_bonus_not_added_when_opposing(self):
        state = _state(price_vs_vwap="at", trend_direction="DOWN", trend_strength="STRONG",
                       volume_relative=1.0, orb_status="inside", session="london")
        result = score_setup(state, _setup("LONG"))
        assert not any("Strong trend bonus" in f for f in result.factors)

    def test_orb_reclaimed_high_long_adds_one(self):
        state = _state(price_vs_vwap="at", trend_direction=None,
                       volume_relative=1.0, orb_status="reclaimed_high", session="london")
        result = score_setup(state, _setup("LONG"))
        assert any("ORB" in f for f in result.factors)

    def test_orb_above_long_adds_one(self):
        state = _state(price_vs_vwap="at", trend_direction=None,
                       volume_relative=1.0, orb_status="above", session="london")
        result = score_setup(state, _setup("LONG"))
        assert any("ORB" in f for f in result.factors)

    def test_orb_rejected_high_short_adds_one(self):
        state = _state(price_vs_vwap="at", trend_direction=None,
                       volume_relative=1.0, orb_status="rejected_high", session="london")
        result = score_setup(state, _setup("SHORT"))
        assert any("ORB" in f for f in result.factors)

    def test_orb_inside_long_no_orb_points(self):
        state = _state(price_vs_vwap="at", trend_direction=None,
                       volume_relative=1.0, orb_status="inside", session="london")
        result = score_setup(state, _setup("LONG"))
        assert not any("ORB" in f for f in result.factors)


# ─── Penalties ───────────────────────────────────────────────────────────────

class TestPenalties:
    def test_against_trend_deducts_three(self):
        # UP trend but SHORT setup → penalty
        state = _state(price_vs_vwap="at", trend_direction="UP", trend_strength="MODERATE",
                       volume_relative=1.0, orb_status="inside", session="london")
        result = score_setup(state, _setup("SHORT"))
        assert any("Against trend" in p for p in result.penalties)

    def test_low_volume_deducts_two(self):
        state = _state(price_vs_vwap="at", trend_direction=None,
                       volume_relative=0.7, orb_status="inside", session="london")
        result = score_setup(state, _setup("LONG"))
        assert any("Low volume" in p for p in result.penalties)

    def test_score_clamped_at_zero_never_negative(self):
        # Low volume(-2) + against trend(-3) with no positive factors = would be -5
        state = _state(price_vs_vwap="below", trend_direction="UP", trend_strength="STRONG",
                       volume_relative=0.5, orb_status="inside", session="london")
        result = score_setup(state, _setup("SHORT"))
        assert result.score >= 0

    def test_both_penalties_can_stack(self):
        state = _state(price_vs_vwap="at", trend_direction="UP", trend_strength="MODERATE",
                       volume_relative=0.6, orb_status="inside", session="london")
        result = score_setup(state, _setup("SHORT"))
        assert any("Against trend" in p for p in result.penalties)
        assert any("Low volume" in p for p in result.penalties)

    def test_sideways_trend_no_penalty(self):
        # SIDEWAYS trend with SHORT setup → no penalty (only UP/DOWN trigger the -3)
        state = _state(price_vs_vwap="at", trend_direction="SIDEWAYS",
                       volume_relative=1.0, orb_status="inside", session="london")
        result = score_setup(state, _setup("SHORT"))
        assert not any("Against trend" in p for p in result.penalties)


# ─── Veto ─────────────────────────────────────────────────────────────────────

class TestVeto:
    def test_strat_direction_short_vetoes_long_setup(self):
        state = _state(strat_sequence="strat_212", strat_direction="SHORT",
                       price_vs_vwap="above", trend_direction="UP",
                       volume_relative=1.5, orb_status="reclaimed_high")
        result = score_setup(state, _setup("LONG"))
        assert result.score == 0
        assert result.grade == "WEAK"
        assert len(result.factors) == 0
        assert any("contradicts" in p for p in result.penalties)

    def test_strat_direction_long_vetoes_short_setup(self):
        state = _state(strat_sequence="strat_122", strat_direction="LONG",
                       price_vs_vwap="below", trend_direction="DOWN",
                       volume_relative=1.5, orb_status="rejected_high")
        result = score_setup(state, _setup("SHORT"))
        assert result.score == 0
        assert result.grade == "WEAK"

    def test_strat_direction_none_no_veto(self):
        # strat exists but direction not classified → no veto
        state = _state(strat_sequence="strat_212", strat_direction=None,
                       price_vs_vwap="above", trend_direction="UP",
                       volume_relative=1.2, orb_status="reclaimed_high")
        result = score_setup(state, _setup("LONG"))
        assert result.score > 0

    def test_no_strat_no_veto(self):
        state = _state(strat_sequence=None, strat_direction=None,
                       price_vs_vwap="above", trend_direction="UP",
                       volume_relative=1.2, orb_status="reclaimed_high")
        result = score_setup(state, _setup("LONG"))
        assert result.score > 0


# ─── Score clamping ───────────────────────────────────────────────────────────

class TestScoreClamping:
    def test_score_cannot_exceed_ten(self):
        # Every possible positive factor in play
        state = _state(
            price_vs_vwap="above",
            trend_direction="UP",
            trend_strength="STRONG",
            volume_relative=2.0,
            orb_status="reclaimed_high",
            strat_sequence="strat_212",
            strat_direction="LONG",
        )
        result = score_setup(state, _setup("LONG"))
        assert result.score <= 10

    def test_score_clamped_at_zero(self):
        state = _state(
            price_vs_vwap="below",
            trend_direction="UP",
            trend_strength="STRONG",
            volume_relative=0.3,
            orb_status="inside",
            session="london",
        )
        result = score_setup(state, _setup("SHORT"))
        assert result.score >= 0


# ─── None / missing field handling ───────────────────────────────────────────

class TestNoneHandling:
    def test_volume_relative_none_no_crash(self):
        state = _state(volume_relative=None, trend_direction=None,
                       orb_status="inside", session="london")
        result = score_setup(state, _setup("LONG"))
        assert isinstance(result, ConfluenceScore)
        # No volume factor or penalty
        assert not any("Volume" in f for f in result.factors)
        assert not any("Low volume" in p for p in result.penalties)

    def test_trend_none_no_crash(self):
        state = _state(trend_direction=None, volume_relative=1.0,
                       orb_status="inside", session="london")
        result = score_setup(state, _setup("LONG"))
        assert isinstance(result, ConfluenceScore)
        assert not any("Trend" in f for f in result.factors)
        assert not any("Against trend" in p for p in result.penalties)

    def test_strat_none_no_crash_and_no_veto(self):
        state = _state(strat_sequence=None, strat_direction=None,
                       trend_direction="UP", price_vs_vwap="above",
                       volume_relative=1.3)
        result = score_setup(state, _setup("LONG"))
        assert isinstance(result, ConfluenceScore)
        assert result.score > 0  # other factors still score

    def test_strat_sequence_none_no_strat_points(self):
        # strat exists but sequence is None
        state = _state(strat_sequence=None, strat_direction="LONG",
                       trend_direction=None, price_vs_vwap="at",
                       volume_relative=1.0, orb_status="inside", session="london")
        result = score_setup(state, _setup("LONG"))
        assert not any("strat_" in f for f in result.factors)
