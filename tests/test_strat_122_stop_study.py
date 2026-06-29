from datetime import datetime, timedelta, timezone

from scripts.strat_122_stop_study import Candidate, detect_wide_122, resolve


def _bar(minute, high, low, close=None):
    return {
        "_dt": datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minute),
        "timestamp": (
            datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minute)
        ).isoformat(),
        "high": high,
        "low": low,
        "close": close if close is not None else (high + low) / 2,
    }


def test_detects_wide_bullish_122_and_builds_mes_pullback():
    rows = [
        _bar(0, 101, 99),       # comparison bar
        _bar(15, 100.5, 99.5),  # inside
        _bar(30, 100, 97),      # 2 down
        _bar(45, 114, 97.5),    # 2 up, wide reversal
    ]

    found = detect_wide_122(rows, "MES")

    assert len(found) == 1
    _, original, pullback = found[0]
    assert original.direction == "LONG"
    assert original.entry == 114.25
    assert original.stop == 96.5
    assert pullback.entry == 111.5  # structural stop + 15 point cap
    assert pullback.target == 141.5


def test_resolver_requires_future_limit_touch_and_is_stop_first():
    candidate = Candidate(
        instrument="MES",
        detected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        direction="LONG",
        entry=100,
        stop=90,
        target=120,
    )
    no_fill = resolve(candidate, [_bar(15, 121, 101)], order_type="limit")
    both = resolve(candidate, [_bar(15, 121, 89)], order_type="limit")

    assert no_fill["result"] == "NO_FILL"
    assert both["result"] == "LOSS"
    assert both["pnl"] < 0


def test_detector_rejects_pattern_across_gap():
    rows = [
        _bar(0, 101, 99),
        _bar(15, 100.5, 99.5),
        _bar(30, 100, 97),
        _bar(60, 104, 96),
    ]

    assert detect_wide_122(rows, "MES") == []
