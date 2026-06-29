"""
tests/test_strat_classifier.py

Coverage for deterministic Strat candle classification.
"""

from strategy.strat_classifier import (
    INSIDE_BAR,
    OUTSIDE_BAR,
    TWO_DOWN,
    TWO_UP,
    StratBar,
    classify_bar,
    classify_from_ohlc,
    classify_sequence,
)


def test_classify_inside_bar():
    result = classify_bar(
        current=StratBar(high=99.0, low=91.0),
        previous=StratBar(high=100.0, low=90.0),
    )

    assert result == INSIDE_BAR


def test_classify_two_up():
    result = classify_bar(
        current=StratBar(high=101.0, low=90.0),
        previous=StratBar(high=100.0, low=90.0),
    )

    assert result == TWO_UP


def test_classify_two_down():
    result = classify_bar(
        current=StratBar(high=100.0, low=89.0),
        previous=StratBar(high=100.0, low=90.0),
    )

    assert result == TWO_DOWN


def test_classify_outside_bar():
    result = classify_bar(
        current=StratBar(high=101.0, low=89.0),
        previous=StratBar(high=100.0, low=90.0),
    )

    assert result == OUTSIDE_BAR


def test_classify_212_bullish_continuation():
    result = classify_sequence(TWO_UP, INSIDE_BAR, TWO_UP)

    assert result.strat_sequence == "strat_212"
    assert result.strat_trigger == "continuation"
    assert result.strat_direction == "LONG"


def test_classify_122_bearish_reversal():
    result = classify_sequence(INSIDE_BAR, TWO_UP, TWO_DOWN)

    assert result.strat_sequence == "strat_122"
    assert result.strat_trigger == "reversal"
    assert result.strat_direction == "SHORT"


def test_classify_22_continuation_and_reversal():
    continuation = classify_sequence(TWO_DOWN, TWO_UP, TWO_UP)
    reversal = classify_sequence(TWO_DOWN, TWO_UP, TWO_DOWN)
    assert continuation.strat_sequence == "strat_22_continuation"
    assert continuation.strat_direction == "LONG"
    assert reversal.strat_sequence == "strat_22_reversal"
    assert reversal.strat_direction == "SHORT"


def test_classify_312_and_322_reversal():
    pattern_312 = classify_sequence(OUTSIDE_BAR, INSIDE_BAR, TWO_UP)
    pattern_322 = classify_sequence(OUTSIDE_BAR, TWO_DOWN, TWO_UP)
    assert pattern_312.strat_sequence == "strat_312"
    assert pattern_312.strat_direction == "LONG"
    assert pattern_322.strat_sequence == "strat_322_reversal"
    assert pattern_322.strat_direction == "LONG"


def test_classify_from_ohlc_without_history_returns_empty_context():
    result = classify_from_ohlc(current_high=101.0, current_low=99.0)

    assert result.current_bar_type is None
    assert result.strat_sequence is None


def test_classify_from_ohlc_derives_current_bar_type():
    result = classify_from_ohlc(
        current_high=101.0,
        current_low=90.0,
        previous_high=100.0,
        previous_low=90.0,
    )

    assert result.current_bar_type == TWO_UP
    assert result.strat_sequence is None


def test_classify_sequence_accepts_numeric_two_bars_back_code():
    """Numeric Strat codes (1=inside, 3=outside) must classify like canonical names.

    Replay/Polygon candle data stores bar types as "1"/"2"/"3" rather than the
    canonical "inside_bar"/"outside_bar". Before normalization these never matched
    the sequence branches, collapsing every 1-2-2 / 3-1-2 / 3-2-2 into plain 2-2.
    """
    # 1-2-2 reversal: two-bars-back is inside ("1")
    reversal = classify_sequence("1", TWO_UP, TWO_DOWN)
    assert reversal.strat_sequence == "strat_122"

    # 3-1-2 breakout: two-bars-back is outside ("3"), previous inside
    breakout = classify_sequence("3", INSIDE_BAR, TWO_UP)
    assert breakout.strat_sequence == "strat_312"

    # 3-2-2 reversal: two-bars-back is outside ("3")
    pattern_322 = classify_sequence("3", TWO_DOWN, TWO_UP)
    assert pattern_322.strat_sequence == "strat_322_reversal"


def test_classify_from_ohlc_classifies_122_with_numeric_two_bars_back():
    """End-to-end replay path: numeric two_bars_back_type still yields 1-2-2."""
    # previous = 2UP vs two-back; current = 2DOWN vs previous; two-back = inside ("1")
    result = classify_from_ohlc(
        current_high=104.0,
        current_low=89.0,
        previous_high=105.0,
        previous_low=95.0,
        two_bars_back_high=100.0,
        two_bars_back_low=90.0,
        two_bars_back_type="1",
    )

    assert result.previous_bar_type == TWO_UP
    assert result.strat_sequence == "strat_122"


def test_classify_from_ohlc_derives_sequence_from_history():
    result = classify_from_ohlc(
        current_high=103.0,
        current_low=100.0,
        previous_high=101.0,
        previous_low=100.0,
        two_bars_back_high=102.0,
        two_bars_back_low=99.0,
        two_bars_back_type=TWO_UP,
    )

    assert result.current_bar_type == TWO_UP
    assert result.previous_bar_type == INSIDE_BAR
    assert result.strat_sequence == "strat_212"
    assert result.strat_direction == "LONG"
