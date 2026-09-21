from datetime import date, datetime, timedelta, timezone

import pytest

from alert_ranker.causal_bars import Bar
from research.strat_reference_212_v01 import (
    FTFC_CONFLICT,
    FTFC_DOWN,
    FTFC_UNAVAILABLE,
    FTFC_UP,
    classify_ftfc,
    observe_candidate,
)
from strategy.strat_classifier import TWO_DOWN, TWO_UP


BASE = datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc)


def b(offset_minutes, o, h, l, c):
    return Bar(
        start=BASE + timedelta(minutes=offset_minutes),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=1.0,
    )


def test_bullish_reversal_uses_inside_high_and_parent_high_magnitude():
    parent = b(0, 10, 11, 8, 9)
    inside = b(60, 9, 10, 8.5, 9.5)
    watch = [
        b(120, 9.5, 10.2, 9.2, 10.1),
        b(125, 10.1, 11.1, 9.9, 11.0),
    ]
    event = observe_candidate(
        instrument="MNQ",
        day=date(2026, 1, 5),
        parent=parent,
        inside=inside,
        parent_type=TWO_DOWN,
        watch=watch,
        midpoint=date(2026, 1, 1),
    )
    assert event.direction == "LONG"
    assert event.trigger_price == 10
    assert event.magnitude == 11
    assert event.magnitude_reached is True
    assert event.structural_failure_after_trigger is False
    assert event.resolution_ambiguous is False


def test_bearish_reversal_uses_inside_low_and_parent_low_magnitude():
    parent = b(0, 10, 12, 9, 11)
    inside = b(60, 11, 11.5, 10, 10.5)
    watch = [b(120, 10.5, 10.8, 8.8, 9.0)]
    event = observe_candidate(
        instrument="MES",
        day=date(2026, 1, 5),
        parent=parent,
        inside=inside,
        parent_type=TWO_UP,
        watch=watch,
        midpoint=date(2026, 2, 1),
    )
    assert event.direction == "SHORT"
    assert event.trigger_price == 10
    assert event.magnitude == 9
    assert event.magnitude_reached is True


def test_same_5m_bar_breaking_both_inside_boundaries_is_ambiguous():
    parent = b(0, 10, 11, 8, 9)
    inside = b(60, 9, 10, 8.5, 9.5)
    watch = [b(120, 9.5, 10.1, 8.4, 9.0)]
    event = observe_candidate(
        instrument="MNQ",
        day=date(2026, 1, 5),
        parent=parent,
        inside=inside,
        parent_type=TWO_DOWN,
        watch=watch,
        midpoint=date(2026, 1, 1),
    )
    assert event.ambiguous is True
    assert event.trigger_time is None


def test_opposite_boundary_first_cancels_reversal_trigger():
    parent = b(0, 10, 11, 8, 9)
    inside = b(60, 9, 10, 8.5, 9.5)
    watch = [
        b(120, 9.5, 9.8, 8.4, 8.7),
        b(125, 8.7, 10.2, 8.6, 10.1),
    ]
    event = observe_candidate(
        instrument="MNQ",
        day=date(2026, 1, 5),
        parent=parent,
        inside=inside,
        parent_type=TWO_DOWN,
        watch=watch,
        midpoint=date(2026, 1, 1),
    )
    assert event.opposite_boundary_first is True
    assert event.trigger_time is None


def test_trigger_after_immediately_following_source_bar_is_ignored():
    parent = b(0, 10, 11, 8, 9)
    inside = b(60, 9, 10, 8.5, 9.5)
    watch = [b(180, 9.5, 10.2, 9.0, 10.1)]
    event = observe_candidate(
        instrument="MNQ",
        day=date(2026, 1, 5),
        parent=parent,
        inside=inside,
        parent_type=TWO_DOWN,
        watch=watch,
        midpoint=date(2026, 1, 1),
    )
    assert event.trigger_time is None
    assert event.watch_window_unresolved is True


def test_post_trigger_structural_failure_blocks_later_magnitude_credit():
    parent = b(0, 10, 11, 8, 9)
    inside = b(60, 9, 10, 8.5, 9.5)
    watch = [
        b(120, 9.5, 10.2, 9.0, 10.1),
        b(125, 10.1, 10.4, 8.4, 8.7),
        b(130, 8.7, 11.2, 8.6, 11.0),
    ]
    event = observe_candidate(
        instrument="MNQ",
        day=date(2026, 1, 5),
        parent=parent,
        inside=inside,
        parent_type=TWO_DOWN,
        watch=watch,
        midpoint=date(2026, 1, 1),
    )
    assert event.trigger_time is not None
    assert event.structural_failure_after_trigger is True
    assert event.magnitude_reached is False
    assert event.watch_window_unresolved is False


def test_same_5m_bar_magnitude_and_failure_is_resolution_ambiguous():
    parent = b(0, 10, 11, 8, 9)
    inside = b(60, 9, 10, 8.5, 9.5)
    watch = [
        b(120, 9.5, 10.2, 9.0, 10.1),
        b(125, 10.1, 11.2, 8.4, 9.0),
    ]
    event = observe_candidate(
        instrument="MNQ",
        day=date(2026, 1, 5),
        parent=parent,
        inside=inside,
        parent_type=TWO_DOWN,
        watch=watch,
        midpoint=date(2026, 1, 1),
    )
    assert event.resolution_ambiguous is True
    assert event.magnitude_reached is False
    assert event.structural_failure_after_trigger is False


def test_excursions_stop_at_first_structural_terminal_event():
    parent = b(0, 10, 11, 8, 9)
    inside = b(60, 9, 10, 8.5, 9.5)
    watch = [
        b(120, 9.5, 10.2, 9.0, 10.1),
        b(125, 10.1, 11.1, 9.5, 11.0),
        b(130, 11.0, 50.0, 1.0, 20.0),
    ]
    event = observe_candidate(
        instrument="MNQ",
        day=date(2026, 1, 5),
        parent=parent,
        inside=inside,
        parent_type=TWO_DOWN,
        watch=watch,
        midpoint=date(2026, 1, 1),
    )
    assert event.magnitude_reached is True
    assert event.mfe_points == pytest.approx(1.1)
    assert event.mae_points == pytest.approx(1.0)


def test_ftfc_is_up_only_when_price_is_above_all_required_opens():
    assert classify_ftfc(
        last_price=101,
        monthly_open=95,
        weekly_open=96,
        daily_open=97,
        current_60m_open=100,
    ) == FTFC_UP


def test_ftfc_is_down_only_when_price_is_below_all_required_opens():
    assert classify_ftfc(
        last_price=90,
        monthly_open=95,
        weekly_open=96,
        daily_open=97,
        current_60m_open=91,
    ) == FTFC_DOWN


def test_ftfc_is_conflict_when_required_opens_disagree():
    assert classify_ftfc(
        last_price=100,
        monthly_open=95,
        weekly_open=105,
        daily_open=97,
        current_60m_open=99,
    ) == FTFC_CONFLICT


def test_ftfc_missing_open_fails_closed():
    assert classify_ftfc(
        last_price=100,
        monthly_open=None,
        weekly_open=95,
        daily_open=96,
        current_60m_open=97,
    ) == FTFC_UNAVAILABLE
