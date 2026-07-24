"""vwap_hold Strat-confirmation: bare "2" must fail closed, not confirm.

Regression for the bug where `_try_vwap_hold` accepted a directionally
ambiguous bare "2" Strat bar-type token as if it confirmed `two_down` —
exactly backwards, since "2" carries no direction (csv_to_replay.py can
still emit it when its source CSV lacks a directional column for a type-2
bar). See docs/bare-2-bar-type-lineage-note-2026-07-24.md.
"""

from datetime import datetime, timezone

import pytest

from context.market_context import (
    MarketState, PriceData, OHLCData, VWAPData, ORBData,
    PreviousDayData, VolumeData, TrendData, StratContext,
)
from strategy.signal_engine import DecisionEngine
from strategy.strat_classifier import INSIDE_BAR, OUTSIDE_BAR, TWO_DOWN, TWO_UP, normalize_bar_type

VWAP = 19495.0


def _short_state(current_bar_type: str | None) -> MarketState:
    """A state where vwap_hold SHORT qualifies on every OTHER condition
    (below VWAP, downtrend, in proximity range) — isolates the Strat
    bar-type confirmation as the only variable under test."""
    now = datetime(2026, 6, 26, 14, 30, tzinfo=timezone.utc)
    close = VWAP - 2.0
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
        strat=StratContext(current_bar_type=current_bar_type) if current_bar_type is not None else None,
        raw={},
    )


class TestNormalizeBarTypeCanonicalContract:
    """Locks in the contract strategy/strat_classifier.py already documents:
    bare "2" is deliberately never resolved to a direction."""

    def test_bare_2_stays_ambiguous(self):
        assert normalize_bar_type("2") == "2"
        assert normalize_bar_type("2") != TWO_UP
        assert normalize_bar_type("2") != TWO_DOWN

    def test_directional_short_forms_resolve(self):
        assert normalize_bar_type("2d") == TWO_DOWN
        assert normalize_bar_type("2D") == TWO_DOWN
        assert normalize_bar_type("2u") == TWO_UP
        assert normalize_bar_type("2U") == TWO_UP

    def test_canonical_long_forms_pass_through(self):
        assert normalize_bar_type("two_down") == TWO_DOWN
        assert normalize_bar_type("two_up") == TWO_UP

    def test_inside_and_outside_unaffected(self):
        assert normalize_bar_type("1") == INSIDE_BAR
        assert normalize_bar_type("3") == OUTSIDE_BAR


class TestVwapHoldFailsClosedOnAmbiguousBarType:

    def test_bare_2_does_not_confirm_two_down(self, config):
        """The core regression: every other vwap_hold condition qualifies,
        but the bar-type evidence is ambiguous — must fail closed (None),
        not be silently treated as confirming two_down."""
        engine = DecisionEngine(config=config)
        assert engine._try_vwap_hold(_short_state("2")) is None

    def test_two_up_does_not_confirm_two_down(self, config):
        """Sanity: an unambiguous WRONG direction must also fail closed
        (this already worked pre-fix; confirms the fix didn't loosen it)."""
        engine = DecisionEngine(config=config)
        assert engine._try_vwap_hold(_short_state("two_up")) is None
        assert engine._try_vwap_hold(_short_state("2u")) is None

    def test_two_down_still_confirms(self, config):
        """Positive control: genuine, unambiguous two_down evidence must
        still fire — proves the fix didn't just kill the feature."""
        engine = DecisionEngine(config=config)
        setup = engine._try_vwap_hold(_short_state("two_down"))
        assert setup is not None
        assert setup.strategy == "vwap_hold"
        assert setup.direction == "SHORT"

    def test_short_form_2d_still_confirms(self, config):
        """Dialect coverage: the short-form alias must still resolve and
        confirm, same as the canonical long form."""
        engine = DecisionEngine(config=config)
        setup = engine._try_vwap_hold(_short_state("2d"))
        assert setup is not None
        assert setup.direction == "SHORT"
        # Case-insensitive: the old ad hoc tuple only accepted lowercase
        # "2d" — normalize_bar_type additionally accepts "2D".
        setup_upper = engine._try_vwap_hold(_short_state("2D"))
        assert setup_upper is not None

    def test_absent_strat_context_still_skips_confirmation(self, config):
        """Unrelated, pre-existing behavior, unchanged by this fix: when
        Strat context is entirely absent (not merely ambiguous), the
        confirmation requirement is skipped by design, not failed closed."""
        engine = DecisionEngine(config=config)
        setup = engine._try_vwap_hold(_short_state(None))
        assert setup is not None
        assert setup.direction == "SHORT"
