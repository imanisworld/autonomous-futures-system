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
