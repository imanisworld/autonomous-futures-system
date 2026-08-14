from __future__ import annotations

import pytest

from alert_ranker.scanner import signal_direction


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        (20_000, "LONG"),
        ("+20k", "LONG"),
        ("$1.5M", "LONG"),
        ("BULLISH", "LONG"),
        ("UP", "LONG"),
        (-20_000, "SHORT"),
        ("-20k", "SHORT"),
        ("-$1.5M", "SHORT"),
        ("BEARISH", "SHORT"),
        ("DOWN", "SHORT"),
    ),
)
def test_signal_sign_or_explicit_label_determines_direction(value, expected):
    assert signal_direction(value) == expected


@pytest.mark.parametrize(
    "value",
    (0, 0.0, "0", "0k", "", None, "FLAT", "UNKNOWN", "ambiguous", float("nan")),
)
def test_zero_or_ambiguous_signal_fails_safe(value):
    assert signal_direction(value) is None
