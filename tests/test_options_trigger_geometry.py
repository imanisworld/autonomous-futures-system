from datetime import datetime, timezone

from alert_ranker.causal_bars import Bar
from alert_ranker.trigger_geometry import geometry_for_trigger
from alert_ranker.trigger_time import ArmedStratTrigger, TriggerResolution

UTC = timezone.utc


def _bar(high, low):
    return Bar(
        start=datetime(2026, 9, 18, 13, 30, tzinfo=UTC),
        open=(high + low) / 2,
        high=high,
        low=low,
        close=(high + low) / 2,
        volume=100,
        vwap=(high + low) / 2,
    )


def _armed(pattern="212", high=10, low=5):
    return ArmedStratTrigger(
        pattern=pattern,
        armed_at=datetime(2026, 9, 18, 14, 0, tzinfo=UTC),
        watch_until=datetime(2026, 9, 18, 14, 30, tzinfo=UTC),
        boundary_high=high,
        boundary_low=low,
        reference_direction="two_down",
        source_timeframe="30Min",
    )


def _result(family, direction="LONG", subtype="REVERSAL", trigger=10, stop=5):
    return TriggerResolution(
        status="TRIGGERED",
        pattern="212",
        family=family,
        subtype=subtype,
        direction=direction,
        break_side="HIGH" if direction == "LONG" else "LOW",
        trigger_level=trigger,
        invalidation_level=stop,
        trigger_bar_start=datetime(2026, 9, 18, 14, 0, tzinfo=UTC),
        trigger_bar_timeframe="5Min",
        final_scenario="two_up",
        opposite_side_broken_later=False,
        reason_code="first_boundary_break",
    )


def test_212_reversal_geometry_is_fully_defined():
    g = geometry_for_trigger(
        armed=_armed(),
        result=_result("STRAT_212_REVERSAL"),
        parent_bar=_bar(12, 2),
    )
    assert g.status == "DEFINED"
    assert g.target == 12
    assert g.stop == 5


def test_212_continuation_does_not_invent_target():
    g = geometry_for_trigger(
        armed=_armed(),
        result=_result("STRAT_212_CONTINUATION", subtype="CONTINUATION"),
        parent_bar=_bar(12, 2),
    )
    assert g.status == "STOP_ONLY"
    assert g.target is None
    assert g.stop == 5


def test_222_reversal_defines_magnitude_but_not_stop():
    g = geometry_for_trigger(
        armed=_armed(pattern="222"),
        result=_result("STRAT_222_REVERSAL"),
        parent_bar=_bar(15, 3),
    )
    assert g.status == "TARGET_ONLY"
    assert g.target == 15
    assert g.stop is None


def test_32_refuses_far_side_stop_and_own_target():
    g = geometry_for_trigger(
        armed=_armed(pattern="32"),
        result=_result("STRAT_32_REVERSAL", direction="SHORT", trigger=5, stop=10),
        parent_bar=_bar(12, 2),
    )
    assert g.status == "NO_OWN_MAGNITUDE"
    assert g.target is None
    assert g.stop is None
    assert "not_far_side_of_3" in g.stop_source
