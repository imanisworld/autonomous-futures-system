from scripts.orb_false_break_entry_architecture_ab import (
    _signal_close_fill,
    _signal_close_result,
    _valid_bracket,
)


def test_signal_close_adverse_slippage_direction():
    assert _signal_close_fill("LONG", 100.0, 0.25, 2) == 100.5
    assert _signal_close_fill("SHORT", 100.0, 0.25, 2) == 99.5


def test_signal_close_requires_fill_inside_original_bracket():
    assert _valid_bracket("LONG", 100.0, 99.0, 103.0)
    assert not _valid_bracket("LONG", 99.0, 99.0, 103.0)
    assert _valid_bracket("SHORT", 100.0, 101.0, 97.0)
    assert not _valid_bracket("SHORT", 101.0, 101.0, 97.0)


def test_signal_close_excludes_signal_bar_and_uses_stop_first():
    candidate = {"direction": "LONG", "stop": 99.0, "target": 102.0}
    bars = [
        {"close": 100.0, "low": 90.0, "high": 110.0},
        {"close": 100.5, "low": 98.5, "high": 102.5},
    ]
    gross, filled, outcome, fill = _signal_close_result(
        candidate, bars, 0, 0.25, 0
    )
    assert filled is True
    assert fill == 100.0
    assert outcome == "stop"
    assert gross == -1.0
