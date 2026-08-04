"""
tests/test_rh_options_regex_redos.py

CodeQL alert #26 (py/polynomial-redos, HIGH) — alert_ranker/rh_options.py.

The label/value patterns used `\\s*[:.]?\\s*` (and `\\s*[:=]?\\s*`): two
adjacent quantified whitespace groups separated by an optional single
character. A run of whitespace can be split between the two groups in n
different ways, so a non-matching tail forces the engine to backtrack
quadratically.

The entry point `parse_messy_rh_options_text()` squashes whitespace before
these patterns run, so the quadratic path was latent rather than live.
`_match_group()` / `_number_after_label()` are generic helpers though, and
any future caller passing unsquashed text would make it reachable.

The fix rewrites the separator as `\\s*(?:[:.]\\s*)?`, which accepts exactly
the same language with exactly one way to match a whitespace run.

These tests assert both halves of that claim: the language is unchanged, and
the scaling is linear rather than quadratic.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

import alert_ranker.rh_options as rh_options
from alert_ranker.rh_options import (
    _number_after_label,
    _squash_text,
    parse_messy_rh_options_text,
)

# The two separator forms, isolated. `_OLD` is the pre-fix shape and is kept
# here only as the timing control — it is no longer used in production code.
_OLD_SEPARATOR = r"\s*[:.]?\s*"
_NEW_SEPARATOR = r"\s*(?:[:.]\s*)?"

_OLD_TARGET = rf"\bTARGET{_OLD_SEPARATOR}(\d+(?:\.\d+)?)\b"
_NEW_TARGET = rf"\bTARGET{_NEW_SEPARATOR}(\d+(?:\.\d+)?)\b"


def _separator_variants() -> list[str]:
    """Every short string over the alphabet the separator can see."""
    alphabet = " \t:.X1"
    variants = [""]
    for _ in range(3):
        variants += [prefix + char for prefix in variants for char in alphabet]
    return sorted(set(variants))


def test_separator_forms_accept_the_same_language() -> None:
    """The rewrite must not change which inputs parse, or to what value."""
    old = re.compile(_OLD_TARGET)
    new = re.compile(_NEW_TARGET)
    for variant in _separator_variants():
        text = f"TARGET{variant}600"
        old_match = old.search(text)
        new_match = new.search(text)
        assert (old_match is None) == (new_match is None), text
        if old_match is not None:
            assert old_match.span() == new_match.span(), text
            assert old_match.group(1) == new_match.group(1), text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("TARGET 600", "600"),
        ("TARGET: 600", "600"),
        ("TARGET:600", "600"),
        ("TARGET : 600", "600"),
        ("TARGET. 600", "600"),
        ("TARGET .600", "600"),
        ("TARGET   600.25", "600.25"),
        ("TARGET600", "600"),
    ],
)
def test_target_separator_still_matches_real_shapes(text: str, expected: str) -> None:
    match = re.search(_NEW_TARGET, text)
    assert match is not None
    assert match.group(1) == expected


@pytest.mark.parametrize(
    ("label", "text", "expected"),
    [
        ("PRICE", "PRICE 593.48", 593.48),
        ("PRICE", "PRICE: 593.48", 593.48),
        ("PRICE", "PRICE = 593.48", 593.48),
        ("PRICE", "PRICE=593.48", 593.48),
        ("OPEN INTEREST", "OPEN INTEREST: 1200", 1200.0),
        ("DTE", "DTE 14", 14.0),
        ("PRICE", "NO PRICE HERE", None),
    ],
)
def test_number_after_label_behaviour_unchanged(
    label: str, text: str, expected: float | None
) -> None:
    assert _number_after_label(text, label) == expected


def test_messy_parse_of_a_realistic_alert_is_unchanged() -> None:
    text = (
        "SPY BULLISH 600C 12/20\n"
        "SIGNA 82 A+\n"
        "DAILY BULLISH  WEEKLY BULLISH\n"
        "GEX POSITIVE\n"
        "spot $593.48\n"
        "Target 1: $600\n"
        "premium: 250   dte: 14   qty 1\n"
        "vol 12000  oi: 45000\n"
    )
    parsed = parse_messy_rh_options_text(text)["parsed"]
    assert parsed["ticker"] == "SPY"
    assert parsed["direction"] == "LONG"
    assert parsed["contract_type"] == "CALL"
    assert parsed["strike"] == 600.0
    assert parsed["current_price"] == 593.48
    assert parsed["gex_resistance_wall"] == 600.0
    assert parsed["premium"] == 250.0
    assert parsed["dte"] == 14
    assert parsed["option_volume"] == 12000
    assert parsed["open_interest"] == 45000


def test_entry_point_squashes_before_matching() -> None:
    """Documents why the alert was latent, not live, at the HTTP boundary."""
    assert _squash_text("TARGET" + " " * 5000 + ": 600") == "TARGET : 600"


def _worst_case_ms(pattern: str, spaces: int) -> float:
    """Time one search against `TARGET<spaces>X` — a run with no digit after."""
    compiled = re.compile(pattern)
    subject = "TARGET" + " " * spaces + "X"
    started = time.perf_counter()
    compiled.search(subject)
    return (time.perf_counter() - started) * 1000.0


@pytest.mark.parametrize("spaces", [2000, 4000, 8000, 16000])
def test_fixed_pattern_stays_fast_on_long_whitespace_runs(spaces: int) -> None:
    """Absolute ceiling. The quadratic form needed 21/84/341/1459 ms here."""
    assert _worst_case_ms(_NEW_TARGET, spaces) < 50.0


def test_fixed_pattern_scales_linearly_not_quadratically() -> None:
    """Doubling the input must roughly double the work, not quadruple it.

    Measured against the wall clock, so the assertion is deliberately loose:
    quadratic growth is 4x per doubling and would fail a 3x bound many times
    over, while linear growth (~2x) clears it even on a noisy machine. The
    smallest sizes are timed but not ratio-checked — they are fast enough
    that timer noise dominates.
    """
    sizes = [2000, 4000, 8000, 16000]
    # Warm the compiled-pattern cache so the first sample is not an outlier.
    _worst_case_ms(_NEW_TARGET, 1000)
    timings = {size: min(_worst_case_ms(_NEW_TARGET, size) for _ in range(5)) for size in sizes}

    total = sum(timings.values())
    assert total < 100.0, f"fixed pattern is unexpectedly slow: {timings}"

    for smaller, larger in zip(sizes, sizes[1:]):
        if timings[smaller] < 0.05:  # below timer resolution; ratio is meaningless
            continue
        ratio = timings[larger] / timings[smaller]
        assert ratio < 3.0, f"{larger}/{smaller} grew {ratio:.1f}x: {timings}"


def _number_after_label_ms(spaces: int) -> float:
    """Time the real production helper on unsquashed text — the latent surface."""
    subject = "PRICE" + " " * spaces + "X"
    started = time.perf_counter()
    _number_after_label(subject, "PRICE")
    return (time.perf_counter() - started) * 1000.0


def test_number_after_label_scales_linearly_on_unsquashed_text() -> None:
    """Regression guard on production code, not on a copy of the pattern.

    `_number_after_label()` is a generic helper; a caller that skips
    `_squash_text()` hands it a raw whitespace run. This is the test that
    actually fails if the ambiguous separator comes back.
    """
    sizes = [2000, 4000, 8000, 16000]
    _number_after_label_ms(1000)  # warm the pattern cache
    timings = {size: min(_number_after_label_ms(size) for _ in range(5)) for size in sizes}

    total = sum(timings.values())
    assert total < 100.0, f"_number_after_label is unexpectedly slow: {timings}"

    for smaller, larger in zip(sizes, sizes[1:]):
        if timings[smaller] < 0.05:
            continue
        ratio = timings[larger] / timings[smaller]
        assert ratio < 3.0, f"{larger}/{smaller} grew {ratio:.1f}x: {timings}"


def test_module_contains_no_ambiguous_whitespace_separators() -> None:
    """Whole-file guard: no `\\s*X?\\s*` shape may reappear in rh_options.py.

    CodeQL reported six message instances for this one alert, so the fix has
    to hold for every pattern in the module, not just the two TARGET lines.
    """
    source = Path(rh_options.__file__).read_text(encoding="utf-8")
    ambiguous = re.compile(r"\\s\*(?:\[[^\]]*\]|\\[A-Za-z]|[^\\\[\s])\?\\s\*")
    offenders = [
        f"line {source[: match.start()].count(chr(10)) + 1}: {match.group(0)}"
        for match in ambiguous.finditer(source)
    ]
    assert not offenders, "ambiguous whitespace separators reintroduced: " + "; ".join(offenders)


def test_old_pattern_was_genuinely_quadratic() -> None:
    """Control: proves the timing harness above can actually detect the bug."""
    small = min(_worst_case_ms(_OLD_TARGET, 1000) for _ in range(3))
    large = min(_worst_case_ms(_OLD_TARGET, 4000) for _ in range(3))
    # 4x the input; quadratic predicts ~16x, linear predicts ~4x.
    assert large / small > 8.0, f"expected quadratic blowup, got {large / small:.1f}x"
