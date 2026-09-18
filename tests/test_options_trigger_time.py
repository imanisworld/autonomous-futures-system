"""Trigger-time Strat observer: arm on completed precursor, resolve first break."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from alert_ranker.causal_bars import MINUTE_5, MINUTE_30, Bar
from alert_ranker.trigger_time import (
    TRIGGER_TIMING_ID,
    TRIGGER_TIMING_VERSION,
    arm_trigger_setup,
    resolve_trigger,
)

UTC = timezone.utc


def _bar(
    minute: int,
    high: float,
    low: float,
    *,
    open_: float | None = None,
    close: float | None = None,
) -> Bar:
    start = datetime(2026, 9, 18, 13, 30, tzinfo=UTC) + timedelta(minutes=minute)
    mid = (high + low) / 2
    return Bar(
        start=start,
        open=mid if open_ is None else open_,
        high=high,
        low=low,
        close=mid if close is None else close,
        volume=1_000,
        vwap=mid,
    )


def _fine(start: datetime, offset: int, high: float, low: float) -> Bar:
    mid = (high + low) / 2
    return Bar(
        start=start + timedelta(minutes=offset),
        open=mid,
        high=high,
        low=low,
        close=mid,
        volume=100,
        vwap=mid,
    )


def test_identity_and_no_runtime_side_effects():
    assert TRIGGER_TIMING_ID == "OPTIONS_STRAT_TRIGGER_TIMING"
    assert TRIGGER_TIMING_VERSION == "trigger-v0.1"

    import ast
    import alert_ranker.trigger_time as module

    tree = ast.parse(open(module.__file__).read())
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any(
        any(token in name for token in ("httpx", "discord", "broker", "sqlite"))
        for name in imports
    )


def test_212_arms_on_inside_bar_and_continuation_triggers_on_first_high_break():
    bars = [
        _bar(0, 10, 0),
        _bar(30, 11, 1),
        _bar(60, 10.5, 1.5),
    ]
    armed = arm_trigger_setup(bars)
    assert armed is not None
    assert armed.pattern == "212"
    assert armed.reference_direction == "two_up"
    assert armed.boundary_high == 10.5
    assert armed.boundary_low == 1.5

    fine = [
        _fine(armed.armed_at, 0, 10.4, 2.0),
        _fine(armed.armed_at, 5, 10.6, 2.0),
    ]
    result = resolve_trigger(armed, fine, lower_timeframe=MINUTE_5)
    assert result.status == "TRIGGERED"
    assert result.family == "STRAT_212_CONTINUATION"
    assert result.subtype == "CONTINUATION"
    assert result.direction == "LONG"
    assert result.trigger_level == 10.5
    assert result.invalidation_level == 1.5
    assert result.trigger_bar_start == armed.armed_at + timedelta(minutes=5)


def test_212_reversal_is_known_on_low_break_without_waiting_for_30m_close():
    bars = [
        _bar(0, 10, 0),
        _bar(30, 11, 1),
        _bar(60, 10.5, 1.5),
    ]
    armed = arm_trigger_setup(bars)
    assert armed is not None

    result = resolve_trigger(
        armed,
        [_fine(armed.armed_at, 0, 10.4, 1.4)],
        lower_timeframe=MINUTE_5,
    )
    assert result.status == "TRIGGERED"
    assert result.family == "STRAT_212_REVERSAL"
    assert result.subtype == "REVERSAL"
    assert result.direction == "SHORT"
    assert result.trigger_level == 1.5


def test_same_lower_bar_crossing_both_sides_is_ambiguous_not_hindsight_labeled():
    bars = [
        _bar(0, 10, 0),
        _bar(30, 11, 1),
        _bar(60, 10.5, 1.5),
    ]
    armed = arm_trigger_setup(bars)
    assert armed is not None

    result = resolve_trigger(
        armed,
        [_fine(armed.armed_at, 0, 10.6, 1.4)],
        lower_timeframe=MINUTE_5,
    )
    assert result.status == "AMBIGUOUS"
    assert result.family is None
    assert result.reason_code == "both_boundaries_crossed_same_lower_bar"
    assert result.final_scenario == "outside_bar"


def test_first_break_is_preserved_even_if_bar_later_becomes_outside():
    bars = [
        _bar(0, 10, 0),
        _bar(30, 11, 1),
        _bar(60, 10.5, 1.5),
    ]
    armed = arm_trigger_setup(bars)
    assert armed is not None

    result = resolve_trigger(
        armed,
        [
            _fine(armed.armed_at, 0, 10.6, 2.0),
            _fine(armed.armed_at, 5, 10.4, 1.4),
        ],
        lower_timeframe=MINUTE_5,
    )
    assert result.status == "TRIGGERED"
    assert result.family == "STRAT_212_CONTINUATION"
    assert result.opposite_side_broken_later is True
    assert result.final_scenario == "outside_bar"


def test_312_arms_on_outside_inside_and_records_relation_to_outside_color():
    bars = [
        _bar(0, 10, 5),
        _bar(30, 12, 3, open_=11, close=4),
        _bar(60, 11, 4),
    ]
    armed = arm_trigger_setup(bars)
    assert armed is not None
    assert armed.pattern == "312"
    assert armed.reference_direction == "two_down"

    result = resolve_trigger(
        armed,
        [_fine(armed.armed_at, 0, 11.1, 5.0)],
        lower_timeframe=MINUTE_5,
    )
    assert result.status == "TRIGGERED"
    assert result.family == "STRAT_312"
    assert result.subtype == "REVERSAL"
    assert result.direction == "LONG"


@pytest.mark.parametrize(
    "bars, low, expected",
    [
        (
            [_bar(0, 10, 0), _bar(30, 11, 1), _bar(60, 12, 2)],
            1.9,
            "STRAT_222_REVERSAL",
        ),
        (
            [_bar(0, 10, 5), _bar(30, 12, 3), _bar(60, 13, 4)],
            3.9,
            "STRAT_322_REVERSAL",
        ),
    ],
)
def test_222_and_322_reversal_trigger_on_first_opposite_break(bars, low, expected):
    armed = arm_trigger_setup(bars)
    assert armed is not None
    result = resolve_trigger(
        armed,
        [_fine(armed.armed_at, 0, armed.boundary_high - 0.1, low)],
        lower_timeframe=MINUTE_5,
    )
    assert result.status == "TRIGGERED"
    assert result.family == expected
    assert result.direction == "SHORT"


def test_122_same_direction_break_cancels_reversal_but_opposite_break_triggers():
    bars = [
        _bar(0, 10, 0),
        _bar(30, 9, 1),
        _bar(60, 11, 2),
    ]
    armed = arm_trigger_setup(bars)
    assert armed is not None
    assert armed.pattern == "122"
    assert armed.reference_direction == "two_up"

    cancelled = resolve_trigger(
        armed,
        [_fine(armed.armed_at, 0, 11.1, 3.0)],
        lower_timeframe=MINUTE_5,
    )
    assert cancelled.status == "CANCELLED"
    assert cancelled.reason_code == "same_direction_break_precludes_122_reversal"

    triggered = resolve_trigger(
        armed,
        [_fine(armed.armed_at, 0, 10.9, 1.9)],
        lower_timeframe=MINUTE_5,
    )
    assert triggered.status == "TRIGGERED"
    assert triggered.family == "OTHER:strat_122"
    assert triggered.direction == "SHORT"


def test_32_is_armed_from_completed_outside_bar_and_break_is_actionable_event():
    bars = [
        _bar(0, 10, 0),
        _bar(30, 11, -1, open_=4, close=8),
    ]
    armed = arm_trigger_setup(bars)
    assert armed is not None
    assert armed.pattern == "32"
    assert armed.reference_direction == "two_up"

    result = resolve_trigger(
        armed,
        [_fine(armed.armed_at, 0, 10.5, -1.1)],
        lower_timeframe=MINUTE_5,
    )
    assert result.status == "TRIGGERED"
    assert result.family == "STRAT_32_REVERSAL"
    assert result.subtype == "REVERSAL"
    assert result.direction == "SHORT"


def test_watch_window_is_exactly_next_30m_bar():
    bars = [
        _bar(0, 10, 0),
        _bar(30, 11, 1),
        _bar(60, 10.5, 1.5),
    ]
    armed = arm_trigger_setup(bars)
    assert armed is not None

    too_late = _fine(armed.armed_at, 30, 10.6, 2.0)
    result = resolve_trigger(armed, [too_late], lower_timeframe=MINUTE_5)
    assert result.status == "NO_TRIGGER"


def test_no_arm_when_latest_completed_bar_has_no_supported_precursor():
    bars = [_bar(0, 10, 0), _bar(30, 9.5, 0.5)]
    assert arm_trigger_setup(bars) is None


@pytest.mark.parametrize(
    ("bars", "high", "reason"),
    [
        (
            [_bar(0, 10, 0), _bar(30, 11, 1), _bar(60, 12, 2)],
            12.1,
            "same_direction_222_is_run_context_not_entry",
        ),
        (
            [_bar(0, 10, 5), _bar(30, 12, 3), _bar(60, 13, 4)],
            13.1,
            "same_direction_322_continuation_not_approved",
        ),
    ],
)
def test_same_direction_222_and_322_are_not_promoted_as_entries(bars, high, reason):
    armed = arm_trigger_setup(bars)
    assert armed is not None

    result = resolve_trigger(
        armed,
        [_fine(armed.armed_at, 0, high, armed.boundary_low + 0.1)],
        lower_timeframe=MINUTE_5,
    )

    assert result.status == "CANCELLED"
    assert result.family is None
    assert result.subtype == "CONTINUATION"
    assert result.direction is None
    assert result.reason_code == reason
