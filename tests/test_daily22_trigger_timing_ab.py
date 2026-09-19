from __future__ import annotations

from context import daily_22_swing_collector as lane


def _long():
    return {"direction": "LONG", "planned_entry": 100.0, "stop": 90.0, "target": 120.0}


def _short():
    return {"direction": "SHORT", "planned_entry": 100.0, "stop": 110.0, "target": 80.0}


def test_planned_entry_is_exactly_two_r():
    assert lane._actual_rr(_long(), 100.0) == 2.0
    assert lane._actual_rr(_short(), 100.0) == 2.0


def test_one_adverse_tick_makes_true_touch_fail_two_r_floor():
    tick = lane.TICK
    assert lane._actual_rr(_long(), 100.0 + tick) < lane.MIN_ACTUAL_RR
    assert lane._actual_rr(_short(), 100.0 - tick) < lane.MIN_ACTUAL_RR


def test_favorable_reentry_can_restore_more_than_two_r():
    tick = lane.TICK
    assert lane._actual_rr(_long(), 100.0 - tick) > lane.MIN_ACTUAL_RR
    assert lane._actual_rr(_short(), 100.0 + tick) > lane.MIN_ACTUAL_RR


def test_daily_contract_keeps_realistic_adverse_slippage():
    assert lane.SLIPPAGE_TICKS > 0
    assert lane.MIN_ACTUAL_RR == 2.0
