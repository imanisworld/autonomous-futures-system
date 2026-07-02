from datetime import datetime, timedelta, timezone

from replay.retest_lane import FineBar, RetestArm, sensitivity_grid, simulate_arm


NOW = datetime(2026, 6, 2, 15, 0, tzinfo=timezone.utc)


def _arm(direction="LONG"):
    return RetestArm(
        instrument="MES",
        armed_at=NOW,
        direction=direction,
        entry=5240.0,
        stop=5230.0 if direction == "LONG" else 5250.0,
        target=5260.0 if direction == "LONG" else 5220.0,
        strategy="orb_breakout",
    )


def _bar(minutes, *, high=5241.0, low=5239.5, close=5240.25):
    return FineBar(NOW + timedelta(minutes=minutes), "MES", high, low, close)


def test_replay_uses_only_bars_after_arm():
    result = simulate_arm(_arm(), [_bar(0), _bar(5)])
    assert result.status == "TRIGGERED"
    assert result.minutes_to_fill == 5
    assert result.bars_seen == 1


def test_replay_expires_without_using_future_bar():
    result = simulate_arm(_arm(), [_bar(25)], ttl_minutes=20)
    assert result.status == "EXPIRED"
    assert result.bars_seen == 0


def test_short_is_exact_mirror():
    result = simulate_arm(
        _arm("SHORT"),
        [_bar(5, high=5240.5, low=5239.0, close=5239.75)],
    )
    assert result.status == "TRIGGERED"


def test_close_too_far_does_not_chase():
    result = simulate_arm(
        _arm(),
        [_bar(5, high=5242.0, low=5239.5, close=5240.5)],
        max_distance_ticks=1,
    )
    assert result.status == "EXPIRED"


def test_sensitivity_grid_is_predefined_and_deterministic():
    rows = sensitivity_grid([_arm()], [_bar(5)])
    assert len(rows) == 9
    current = next(
        r for r in rows
        if r["ttl_minutes"] == 20 and r["max_distance_ticks"] == 1
    )
    assert current["triggered"] == 1
