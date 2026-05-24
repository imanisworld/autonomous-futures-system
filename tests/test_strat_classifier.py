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
