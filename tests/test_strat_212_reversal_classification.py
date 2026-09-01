"""Regression coverage for canonical 2-1-2 reversal identity.

The legacy Pine payload labels an otherwise-unmatched inside-bar break as
``strat_inside_break``. When the bar before the inside bar is directional and
the resolving bar breaks the opposite way, the sequence is specifically a
2-1-2 reversal and must not be routed through the generic inside-break identity.
"""

import json
from pathlib import Path

from strategy.strat_classifier import (
    INSIDE_BAR,
    STRAT_212_REVERSAL,
    TWO_DOWN,
    TWO_UP,
    StratContext,
    classify_sequence,
)


def test_classify_bullish_212_reversal():
    result = classify_sequence(TWO_DOWN, INSIDE_BAR, TWO_UP)

    assert result.strat_sequence == STRAT_212_REVERSAL
    assert result.strat_trigger == "reversal"
    assert result.strat_direction == "LONG"


def test_classify_bearish_212_reversal():
    result = classify_sequence(TWO_UP, INSIDE_BAR, TWO_DOWN)

    assert result.strat_sequence == STRAT_212_REVERSAL
    assert result.strat_trigger == "reversal"
    assert result.strat_direction == "SHORT"


def test_212_continuation_identity_is_unchanged():
    bullish = classify_sequence(TWO_UP, INSIDE_BAR, TWO_UP)
    bearish = classify_sequence(TWO_DOWN, INSIDE_BAR, TWO_DOWN)

    assert bullish.strat_sequence == "strat_212"
    assert bullish.strat_trigger == "continuation"
    assert bullish.strat_direction == "LONG"
    assert bearish.strat_sequence == "strat_212"
    assert bearish.strat_trigger == "continuation"
    assert bearish.strat_direction == "SHORT"


def test_generic_inside_break_stays_generic_without_directional_precursor():
    result = classify_sequence(None, INSIDE_BAR, TWO_UP)

    assert result.strat_sequence == "strat_inside_break"
    assert result.strat_trigger == "breakout"
    assert result.strat_direction == "LONG"


def test_legacy_pine_inside_break_label_is_corrected_fail_closed():
    result = StratContext(
        current_bar_type=TWO_UP,
        previous_bar_type=INSIDE_BAR,
        two_bars_back_type=TWO_DOWN,
        strat_sequence="strat_inside_break",
        strat_trigger="breakout",
        strat_direction="LONG",
    )

    assert result.strat_sequence == STRAT_212_REVERSAL
    assert result.strat_trigger == "reversal"
    assert result.strat_direction == "LONG"


def test_non_212_inside_break_payload_is_not_rewritten():
    result = StratContext(
        current_bar_type=TWO_UP,
        previous_bar_type=INSIDE_BAR,
        two_bars_back_type=None,
        strat_sequence="strat_inside_break",
        strat_trigger="breakout",
        strat_direction="LONG",
    )

    assert result.strat_sequence == "strat_inside_break"
    assert result.strat_trigger == "breakout"


def test_market_state_schema_accepts_212_reversal_identity():
    schema = json.loads(Path("market_state.schema.json").read_text())
    enum_values = schema["properties"]["strat"]["properties"]["strat_sequence"]["enum"]

    assert STRAT_212_REVERSAL in enum_values
