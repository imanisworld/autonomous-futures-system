from datetime import date, datetime
from zoneinfo import ZoneInfo

from research.replay_12hr_miyagi_honest_fill import replay_signal
from scripts.miyagi_trigger_bar_causal_audit_2026_09_19 import (
    causal_trigger_bar_replay,
    legacy_trigger_bar_replay,
)

ET = ZoneInfo("America/New_York")
DAY = date(2026, 1, 6)


def _bar(hour, minute, *, o, h, low, c):
    return {
        "ts": datetime(2026, 1, 6, hour, minute, tzinfo=ET),
        "open": float(o),
        "high": float(h),
        "low": float(low),
        "close": float(c),
    }


def _signal(direction, *, trigger, stop, target):
    return {
        "date": DAY,
        "instrument": "MNQ",
        "direction": direction,
        "entry_trigger": float(trigger),
        "stop": float(stop),
        "target": float(target),
        "target_2": float(target),
    }
def test_same_trigger_bar_stop_is_not_ignored():
    signal = _signal("SHORT", trigger=100, stop=105, target=95)
    bars = [
        _bar(9, 30, o=110, h=106, low=99, c=100),
        _bar(9, 35, o=100, h=101, low=94, c=96),
    ]

    legacy = legacy_trigger_bar_replay(signal, bars, slippage_ticks=2)
    canonical = replay_signal(signal, bars, slippage_ticks=2)
    causal = causal_trigger_bar_replay(signal, bars, slippage_ticks=2)

    assert legacy["result"] == "WIN"
    assert legacy["exit_reason"] == "TARGET"
    assert canonical == causal
    assert causal["result"] == "LOSS"
    assert causal["exit_reason"] == "STOP"
    assert causal["exit_bar_ts"] == bars[0]["ts"].isoformat()


def test_gap_through_uses_open_as_entry_reference():
    signal = _signal("LONG", trigger=100, stop=95, target=110)
    bars = [
        _bar(9, 30, o=103, h=104, low=102, c=103.5),
        _bar(9, 35, o=104, h=111, low=103.5, c=110),
    ]

    causal = causal_trigger_bar_replay(signal, bars, slippage_ticks=2)

    assert causal["filled"] is True
    assert causal["base_entry_price"] == 103.0
    assert causal["fill_entry_price"] == 103.5
    assert causal["result"] == "WIN"
