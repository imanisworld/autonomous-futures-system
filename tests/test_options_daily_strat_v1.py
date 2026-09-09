from __future__ import annotations

from datetime import datetime, timedelta, timezone

from alert_ranker.causal_bars import Bar
from alert_ranker.daily_strat import evaluate_daily_setup


START = datetime(2026, 9, 1, 13, 30, tzinfo=timezone.utc)


def _bar(index: int, high: float, low: float) -> Bar:
    return Bar(
        start=START + timedelta(days=index),
        open=(high + low) / 2,
        high=high,
        low=low,
        close=(high + low) / 2,
        volume=1_000,
    )


def test_daily_222_continuation_and_reversal():
    three_back = _bar(0, 10, 0)
    two_back = _bar(1, 11, 1)  # 2U
    previous = _bar(2, 12, 2)  # 2U

    continuation = evaluate_daily_setup(
        [three_back, two_back, previous], _bar(3, 13, 3)
    )
    assert continuation.status == "TRIGGERED"
    assert continuation.setup_type == "DAILY_222_CONTINUATION"
    assert continuation.sequence == "strat_222_continuation"
    assert continuation.direction == "CALL"
    assert continuation.entry_trigger == 12
    assert continuation.invalidation == 2

    reversal = evaluate_daily_setup(
        [three_back, two_back, previous], _bar(3, 11.5, 1.5)
    )
    assert reversal.status == "TRIGGERED"
    assert reversal.setup_type == "DAILY_222_REVERSAL"
    assert reversal.direction == "PUT"
    assert reversal.entry_trigger == 2
    assert reversal.invalidation == 12


def test_daily_322_continuation_reversal_and_32_watch():
    three_back = _bar(0, 10, 0)
    two_back = _bar(1, 11, -1)  # 3 outside
    previous = _bar(2, 12, 0)  # 2U

    watch = evaluate_daily_setup(
        [three_back, two_back, previous], _bar(3, 11.5, 0.5)
    )
    assert watch.status == "WATCH"
    assert watch.setup_type == "DAILY_32_DEVELOPING"
    assert watch.direction is None

    continuation = evaluate_daily_setup(
        [three_back, two_back, previous], _bar(3, 13, 1)
    )
    assert continuation.status == "TRIGGERED"
    assert continuation.setup_type == "DAILY_322_CONTINUATION"
    assert continuation.direction == "CALL"

    reversal = evaluate_daily_setup(
        [three_back, two_back, previous], _bar(3, 11.5, -0.5)
    )
    assert reversal.status == "TRIGGERED"
    assert reversal.setup_type == "DAILY_322_REVERSAL"
    assert reversal.direction == "PUT"


def test_daily_212_v1_is_continuation_only():
    three_back = _bar(0, 10, 0)
    two_back = _bar(1, 11, 1)  # 2U
    previous = _bar(2, 10.5, 1.5)  # inside 1

    continuation = evaluate_daily_setup(
        [three_back, two_back, previous], _bar(3, 11, 2)
    )
    assert continuation.status == "TRIGGERED"
    assert continuation.setup_type == "DAILY_212_CONTINUATION"
    assert continuation.direction == "CALL"
    assert continuation.entry_trigger == 10.5
    assert continuation.invalidation == 1.5

    reversal = evaluate_daily_setup(
        [three_back, two_back, previous], _bar(3, 10, 1)
    )
    assert reversal.status == "NO_TRADE"
    assert reversal.reason_code == "daily_212_reversal_not_in_v1"


def test_daily_323_is_not_mislabeled_as_322():
    three_back = _bar(0, 10, 0)
    two_back = _bar(1, 11, -1)  # 3
    previous = _bar(2, 12, 0)  # 2U
    outside_current = _bar(3, 13, -0.5)  # 3 vs previous

    result = evaluate_daily_setup(
        [three_back, two_back, previous], outside_current
    )
    assert result.status == "NO_TRADE"
    assert result.reason_code == "daily_323_not_in_population"
