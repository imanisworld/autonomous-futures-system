from strategy.range_fade import RangeBar, RangeFadeConfig, RangeTracker


def _bar(i, open_, high, low, close, condition="CONSOLIDATING", strength="WEAK", volume=100):
    return RangeBar(
        timestamp=f"2026-06-01T14:{i:02d}:00+00:00",
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        avg_volume=100,
        market_condition=condition,
        trend_direction="SIDEWAYS",
        trend_strength=strength,
    )


def _active_tracker():
    tracker = RangeTracker(RangeFadeConfig(confirmation_bars=6))
    bars = [
        _bar(0, 102, 104, 100, 103),
        _bar(1, 103, 110, 102, 109),
        _bar(2, 108, 109, 101, 102),
        _bar(3, 102, 108, 100, 107),
        _bar(4, 107, 110, 103, 104),
        _bar(5, 104, 108, 101, 105),
    ]
    for bar in bars:
        assert tracker.update(bar) is None
    assert tracker.active is not None
    return tracker


def test_range_uses_prior_bars_and_emits_long_rejection():
    tracker = _active_tracker()
    signal = tracker.update(_bar(6, 101, 104, 99.5, 103))
    assert signal is not None
    assert signal.direction == "LONG"
    assert signal.support == 100
    assert signal.resistance == 110
    assert signal.target == 105


def test_range_emits_short_rejection():
    tracker = _active_tracker()
    signal = tracker.update(_bar(6, 109, 110.5, 106, 107))
    assert signal is not None
    assert signal.direction == "SHORT"


def test_one_close_outside_pauses_but_does_not_invalidate():
    tracker = _active_tracker()
    assert tracker.update(_bar(6, 109, 112, 108, 111)) is None
    assert tracker.active is not None


def test_two_closes_outside_invalidate_frozen_range():
    tracker = _active_tracker()
    tracker.update(_bar(6, 109, 112, 108, 111))
    tracker.update(_bar(7, 111, 113, 110, 112))
    assert tracker.active is None


def test_high_volume_break_invalidates_immediately():
    tracker = _active_tracker()
    tracker.update(_bar(6, 109, 112, 108, 111, volume=130))
    assert tracker.active is None

