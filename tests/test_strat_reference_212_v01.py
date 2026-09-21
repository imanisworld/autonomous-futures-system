from datetime import date, datetime, timedelta, timezone

from alert_ranker.causal_bars import Bar
from research.strat_reference_212_v01 import observe_candidate
from strategy.strat_classifier import TWO_DOWN, TWO_UP


def b(minute, o, h, l, c):
    return Bar(
        start=datetime(2026, 1, 5, 15, minute, tzinfo=timezone.utc),
        open=o, high=h, low=l, close=c, volume=1.0,
    )


def test_bullish_reversal_uses_inside_high_and_parent_high_magnitude():
    parent = b(0, 10, 11, 8, 9)
    inside = b(5, 9, 10, 8.5, 9.5)
    watch = [
        b(10, 9.5, 10.2, 9.2, 10.1),
        b(15, 10.1, 11.1, 9.9, 11.0),
    ]
    event = observe_candidate(
        instrument="MNQ", day=date(2026, 1, 5), parent=parent, inside=inside,
        parent_type=TWO_DOWN, watch=watch, midpoint=date(2026, 1, 1),
    )
    assert event.direction == "LONG"
    assert event.trigger_price == 10
    assert event.magnitude == 11
    assert event.magnitude_reached is True


def test_bearish_reversal_uses_inside_low_and_parent_low_magnitude():
    parent = b(0, 10, 12, 9, 11)
    inside = b(5, 11, 11.5, 10, 10.5)
    watch = [b(10, 10.5, 10.8, 8.8, 9.0)]
    event = observe_candidate(
        instrument="MES", day=date(2026, 1, 5), parent=parent, inside=inside,
        parent_type=TWO_UP, watch=watch, midpoint=date(2026, 2, 1),
    )
    assert event.direction == "SHORT"
    assert event.trigger_price == 10
    assert event.magnitude == 9
    assert event.magnitude_reached is True


def test_same_5m_bar_breaking_both_inside_boundaries_is_ambiguous():
    parent = b(0, 10, 11, 8, 9)
    inside = b(5, 9, 10, 8.5, 9.5)
    watch = [b(10, 9.5, 10.1, 8.4, 9.0)]
    event = observe_candidate(
        instrument="MNQ", day=date(2026, 1, 5), parent=parent, inside=inside,
        parent_type=TWO_DOWN, watch=watch, midpoint=date(2026, 1, 1),
    )
    assert event.ambiguous is True
    assert event.trigger_time is None


def test_opposite_boundary_first_cancels_reversal_trigger():
    parent = b(0, 10, 11, 8, 9)
    inside = b(5, 9, 10, 8.5, 9.5)
    watch = [
        b(10, 9.5, 9.8, 8.4, 8.7),
        b(15, 8.7, 10.2, 8.6, 10.1),
    ]
    event = observe_candidate(
        instrument="MNQ", day=date(2026, 1, 5), parent=parent, inside=inside,
        parent_type=TWO_DOWN, watch=watch, midpoint=date(2026, 1, 1),
    )
    assert event.opposite_boundary_first is True
    assert event.trigger_time is None
