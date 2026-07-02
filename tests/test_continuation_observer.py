from dataclasses import replace

from strategy.shadow_setups import evaluate_shadow_setups


def _bars(closes, width=2.0):
    return [
        {
            "ts": str(i),
            "open": close - 0.25,
            "high": close + width / 2,
            "low": close - width / 2,
            "close": close,
            "volume": 1,
        }
        for i, close in enumerate(closes)
    ]


def test_first_pullback_is_observed_with_local_stop(fresh_market_state):
    state = fresh_market_state
    state.trend = replace(state.trend, direction="UP", strength="STRONG")
    found = evaluate_shadow_setups(state, _bars([100, 103, 106, 104]))
    candidate = next(c for c in found if c.strategy == "impulse_first_pullback_observed")
    assert candidate.direction == "LONG"
    assert candidate.stop < 104 < candidate.entry


def test_consolidation_break_candidate_is_observe_only(fresh_market_state):
    state = fresh_market_state
    state.trend = replace(state.trend, direction="DOWN", strength="STRONG")
    bars = [
        {"ts": "0", "open": 110, "high": 111, "low": 108, "close": 109, "volume": 1},
        {"ts": "1", "open": 109, "high": 109, "low": 101, "close": 102, "volume": 1},
        {"ts": "2", "open": 103, "high": 104, "low": 101, "close": 102, "volume": 1},
        {"ts": "3", "open": 102, "high": 103, "low": 101, "close": 102, "volume": 1},
        {"ts": "4", "open": 102, "high": 103, "low": 101, "close": 101.5, "volume": 1},
    ]
    found = evaluate_shadow_setups(state, bars)
    candidate = next(c for c in found if c.strategy == "trend_consolidation_break_observed")
    assert candidate.direction == "SHORT"
    assert candidate.entry < candidate.stop


def test_no_recent_history_means_no_new_continuation_candidates(fresh_market_state):
    names = {c.strategy for c in evaluate_shadow_setups(fresh_market_state, [])}
    assert "impulse_first_pullback_observed" not in names
    assert "trend_consolidation_break_observed" not in names
