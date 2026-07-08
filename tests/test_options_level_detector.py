"""
tests/test_options_level_detector.py

Increment 15 — options_manager/levels/level_detector.py tests. Proves
the local level detector is a pure function of caller-supplied OHLC:
it detects prior/inside/outside-bar, PDH/PDL, PWH/PWL, ORB, swing, and
clustered levels only from data it was given, never fabricates a level
for data it wasn't given, and produces deterministic ordering.
"""

from __future__ import annotations

import ast
from pathlib import Path

import options_manager.levels.level_detector as level_detector_module
from options_manager.levels.level_detector import OHLCBar, detect_local_levels

_SCANNED_MODULES = (level_detector_module,)

_FORBIDDEN_IMPORT_FRAGMENTS = (
    "execution",
    "webhook",
    "alert_ranker",
    "options_companion",
    "risk_engine",
    "requests",
    "httpx",
    "urllib",
    "socket",
    "aiohttp",
    "websocket",
    "robin_stocks",
    "ib_insync",
    "ibapi",
)

_FORBIDDEN_ORDER_ACTION_IDENTIFIERS = (
    "place_order",
    "submit_order",
    "cancel_order",
    "replace_order",
    "execute_order",
    "live_order",
)


# --- prior candle levels ------------------------------------------------------------------------


def test_prior_candle_levels():
    result = detect_local_levels(prior_bar=OHLCBar(high=100.0, low=95.0))
    labels = {c.label: c.level for c in result.levels}
    assert labels["prior_high"] == 100.0
    assert labels["prior_low"] == 95.0


# --- inside-bar levels ---------------------------------------------------------------------------


def test_inside_bar_levels():
    result = detect_local_levels(inside_bar=OHLCBar(high=98.5, low=96.5))
    labels = {c.label: c.level for c in result.levels}
    assert labels["inside_bar_high"] == 98.5
    assert labels["inside_bar_low"] == 96.5


# --- outside-bar range levels ---------------------------------------------------------------------


def test_outside_bar_range_levels():
    result = detect_local_levels(outside_bar=OHLCBar(high=108.0, low=93.0))
    labels = {c.label: c.level for c in result.levels}
    assert labels["outside_bar_high"] == 108.0
    assert labels["outside_bar_low"] == 93.0


# --- PDH/PDL from supplied daily bars ---------------------------------------------------------------


def test_pdh_pdl_from_supplied_daily_bar():
    result = detect_local_levels(prior_day_bar=OHLCBar(high=110.0, low=90.0))
    labels = {c.label: c.level for c in result.levels}
    assert labels["pdh"] == 110.0
    assert labels["pdl"] == 90.0


# --- PWH/PWL from supplied weekly bars ---------------------------------------------------------------


def test_pwh_pwl_from_supplied_weekly_bar():
    result = detect_local_levels(prior_week_bar=OHLCBar(high=120.0, low=85.0))
    labels = {c.label: c.level for c in result.levels}
    assert labels["pwh"] == 120.0
    assert labels["pwl"] == 85.0


# --- ORB high/low from supplied opening bars ----------------------------------------------------------


def test_orb_high_low_from_supplied_opening_range_bars():
    bars = (
        OHLCBar(high=101.0, low=99.0),
        OHLCBar(high=103.0, low=98.0),
        OHLCBar(high=102.0, low=97.5),
    )
    result = detect_local_levels(opening_range_bars=bars)
    labels = {c.label: c.level for c in result.levels}
    assert labels["orb_high"] == 103.0
    assert labels["orb_low"] == 97.5


# --- swing high/low detection --------------------------------------------------------------------------


def test_swing_high_low_detection():
    bars = (
        OHLCBar(high=100.0, low=95.0),
        OHLCBar(high=102.0, low=96.0),
        OHLCBar(high=105.0, low=94.0),  # swing high (105) and swing low (94) candidate at center
        OHLCBar(high=101.0, low=97.0),
        OHLCBar(high=99.0, low=98.0),
    )
    result = detect_local_levels(swing_bars=bars, swing_lookback=2)
    swing_highs = [c.level for c in result.levels if c.label == "swing_high"]
    swing_lows = [c.level for c in result.levels if c.label == "swing_low"]
    assert swing_highs == [105.0]
    assert swing_lows == [94.0]


def test_swing_detection_finds_no_pivot_when_none_exists():
    # Strictly increasing highs/lows -- no interior bar is a local extreme.
    bars = tuple(OHLCBar(high=100.0 + i, low=90.0 + i) for i in range(5))
    result = detect_local_levels(swing_bars=bars, swing_lookback=2)
    assert not any(c.label in ("swing_high", "swing_low") for c in result.levels)


# --- support/resistance clustering -----------------------------------------------------------------------


def test_support_resistance_clustering():
    result = detect_local_levels(
        current_price=100.0,
        prior_bar=OHLCBar(high=103.0, low=97.0),
        prior_day_bar=OHLCBar(high=103.2, low=96.8),
        cluster_distance=0.5,
    )
    # 103.0 and 103.2 should cluster into one resistance level near ~103.1;
    # 97.0 and 96.8 should cluster into one support level near ~96.9.
    assert len(result.resistance_levels) == 1
    assert len(result.support_levels) == 1
    assert 103.0 <= result.resistance_levels[0] <= 103.2
    assert 96.8 <= result.support_levels[0] <= 97.0


def test_resistance_and_support_are_empty_without_current_price():
    result = detect_local_levels(prior_bar=OHLCBar(high=103.0, low=97.0))
    assert result.resistance_levels == ()
    assert result.support_levels == ()


def test_resistance_and_support_split_correctly_without_clustering():
    result = detect_local_levels(
        current_price=100.0,
        prior_bar=OHLCBar(high=103.0, low=97.0),
        prior_day_bar=OHLCBar(high=105.0, low=95.0),
    )
    assert result.resistance_levels == (103.0, 105.0)
    assert result.support_levels == (97.0, 95.0)


# --- insufficient bars returns no fabricated levels --------------------------------------------------------


def test_insufficient_swing_bars_returns_no_fabricated_levels():
    bars = (OHLCBar(high=100.0, low=95.0), OHLCBar(high=101.0, low=96.0))
    result = detect_local_levels(swing_bars=bars, swing_lookback=2)
    assert not any(c.label in ("swing_high", "swing_low") for c in result.levels)
    assert any("insufficient swing_bars" in w for w in result.warnings)


def test_empty_opening_range_bars_returns_no_fabricated_levels():
    result = detect_local_levels(opening_range_bars=())
    assert not any(c.label in ("orb_high", "orb_low") for c in result.levels)
    assert any("opening_range_bars empty" in w for w in result.warnings)


def test_all_omitted_inputs_returns_empty_result_with_warnings():
    result = detect_local_levels()
    assert result.levels == []
    assert result.resistance_levels == ()
    assert result.support_levels == ()
    assert len(result.warnings) >= 5


# --- deterministic ordering -------------------------------------------------------------------------------------


def test_deterministic_ordering():
    result_1 = detect_local_levels(
        prior_bar=OHLCBar(high=103.0, low=97.0),
        prior_day_bar=OHLCBar(high=105.0, low=95.0),
        inside_bar=OHLCBar(high=101.0, low=99.0),
    )
    result_2 = detect_local_levels(
        prior_bar=OHLCBar(high=103.0, low=97.0),
        prior_day_bar=OHLCBar(high=105.0, low=95.0),
        inside_bar=OHLCBar(high=101.0, low=99.0),
    )
    assert result_1.levels == result_2.levels
    values = [c.level for c in result_1.levels]
    assert values == sorted(values)


# --- structural safety (matches this buildout's established pattern) --------------------------------------------


def _imported_modules(module) -> list[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def test_level_detector_has_no_forbidden_imports():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in name, f"{module.__name__} must not import {name!r}"


def test_level_detector_has_no_cross_boundary_imports_outside_stdlib():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        outside = [
            name
            for name in imported
            if not name.startswith("options_manager")
            and name not in ("__future__", "dataclasses", "typing")
        ]
        assert not outside, f"{module.__name__} has an unexpected import: {outside}"


def test_level_detector_has_no_order_action_verbs():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in _FORBIDDEN_ORDER_ACTION_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_level_detector_does_not_mutate_live_options_flag():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        assert "LIVE_OPTIONS_TRADING_ENABLED" not in source


def test_level_detector_does_not_write_files():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in ("open(", ".write(", ".write_text(", ".write_bytes("):
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_level_detector_does_not_reimplement_strategy_or_scanner_logic():
    source = Path(level_detector_module.__file__).read_text()
    assert "def evaluate_strat_212" not in source
    assert "def scan_watchlist_strat_212" not in source
    assert "classify_from_ohlc" not in source
