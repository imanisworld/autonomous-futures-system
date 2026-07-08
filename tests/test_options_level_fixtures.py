"""
tests/test_options_level_fixtures.py

Increment 16 — options_manager/levels/fixtures.py tests. Proves the
deterministic level-detector fixture dataset covers every category the
local level detector supports (including a fail-closed case), runs to
stable per-case results, and never fabricates a level or calls the
scanner/strategy evaluator.
"""

from __future__ import annotations

import ast
from pathlib import Path

import options_manager.levels.fixtures as fixtures_module
from options_manager.levels.fixtures import (
    LevelFixtureSummary,
    build_level_detector_fixture_dataset,
    run_level_detector_fixture_dataset,
    summarize_level_detector_fixture_dataset,
)

_SCANNED_MODULES = (fixtures_module,)

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

_FORBIDDEN_CREDENTIAL_IDENTIFIERS = (
    "api_key",
    "apikey",
    "credential",
    "secret",
    "password",
    "token",
)

_EXPECTED_CASE_NAMES = {
    "prior_candle",
    "inside_bar",
    "outside_bar",
    "pdh_pdl",
    "pwh_pwl",
    "orb",
    "swing",
    "clustering",
    "insufficient_data",
}


# --- fixture dataset contains expected named cases ----------------------------------------------


def test_fixture_dataset_contains_expected_named_cases():
    cases = build_level_detector_fixture_dataset()
    assert set(cases.keys()) == _EXPECTED_CASE_NAMES
    assert len(cases) == 9


# --- detector output is deterministic ------------------------------------------------------------


def test_detector_output_is_deterministic():
    results_1 = run_level_detector_fixture_dataset()
    results_2 = run_level_detector_fixture_dataset()
    assert results_1 == results_2


# --- expected levels are stable -------------------------------------------------------------------


def test_expected_levels_are_stable():
    results = run_level_detector_fixture_dataset()

    def labels(name):
        return {c.label: c.level for c in results[name].levels}

    assert labels("prior_candle") == {"prior_high": 100.0, "prior_low": 95.0}
    assert labels("inside_bar") == {"inside_bar_high": 98.5, "inside_bar_low": 96.5}
    assert labels("outside_bar") == {"outside_bar_high": 108.0, "outside_bar_low": 93.0}
    assert labels("pdh_pdl") == {"pdh": 110.0, "pdl": 90.0}
    assert labels("pwh_pwl") == {"pwh": 120.0, "pwl": 85.0}
    assert labels("orb") == {"orb_high": 103.0, "orb_low": 97.5}

    swing_result = results["swing"]
    swing_highs = [c.level for c in swing_result.levels if c.label == "swing_high"]
    swing_lows = [c.level for c in swing_result.levels if c.label == "swing_low"]
    assert swing_highs == [105.0]
    assert swing_lows == [94.0]


# --- fail-closed fixture produces warnings, no fabricated levels ---------------------------------


def test_fail_closed_fixture_produces_warnings_not_fabricated_levels():
    results = run_level_detector_fixture_dataset()
    result = results["insufficient_data"]
    assert result.levels == []
    assert result.resistance_levels == ()
    assert result.support_levels == ()
    assert len(result.warnings) == 7


# --- support/resistance ordering is stable ---------------------------------------------------------


def test_support_resistance_ordering_is_stable():
    results_1 = run_level_detector_fixture_dataset()
    results_2 = run_level_detector_fixture_dataset()
    clustering_1 = results_1["clustering"]
    clustering_2 = results_2["clustering"]
    assert clustering_1.resistance_levels == clustering_2.resistance_levels
    assert clustering_1.support_levels == clustering_2.support_levels
    assert len(clustering_1.resistance_levels) == 1
    assert len(clustering_1.support_levels) == 1
    assert 103.0 <= clustering_1.resistance_levels[0] <= 103.2
    assert 96.8 <= clustering_1.support_levels[0] <= 97.0


# --- fixture summary counts are stable ---------------------------------------------------------------


def test_fixture_summary_counts_are_stable():
    summary_1 = summarize_level_detector_fixture_dataset()
    summary_2 = summarize_level_detector_fixture_dataset()
    assert isinstance(summary_1, LevelFixtureSummary)
    assert summary_1 == summary_2
    assert summary_1.total_cases == 9
    assert summary_1.total_levels_found == sum(summary_1.levels_by_case.values())
    assert summary_1.total_warnings == sum(summary_1.warnings_by_case.values())
    assert set(summary_1.levels_by_case.keys()) == _EXPECTED_CASE_NAMES
    assert set(summary_1.warnings_by_case.keys()) == _EXPECTED_CASE_NAMES
    assert summary_1.levels_by_case["insufficient_data"] == 0
    assert summary_1.warnings_by_case["insufficient_data"] == 7


def test_fixture_helpers_do_not_mutate_state_between_calls():
    dataset_1 = build_level_detector_fixture_dataset()
    dataset_2 = build_level_detector_fixture_dataset()
    assert dataset_1 == dataset_2
    assert dataset_1 is not dataset_2


# --- structural safety (matches this buildout's established pattern) -----------------------------------


def _imported_modules(module) -> list[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def test_fixture_modules_have_no_forbidden_imports():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in name, f"{module.__name__} must not import {name!r}"


def test_fixture_modules_have_no_cross_boundary_imports_outside_options_manager():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        outside = [
            name
            for name in imported
            if not name.startswith("options_manager")
            and name not in ("__future__", "dataclasses", "typing")
        ]
        assert not outside, f"{module.__name__} has an unexpected import: {outside}"


def test_fixture_modules_have_no_credential_identifiers():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text().lower()
        for forbidden in _FORBIDDEN_CREDENTIAL_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_fixture_modules_have_no_order_action_verbs():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in _FORBIDDEN_ORDER_ACTION_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_fixture_modules_do_not_mutate_live_options_flag():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        assert "LIVE_OPTIONS_TRADING_ENABLED" not in source


def test_fixture_modules_do_not_read_or_write_files():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in ("open(", ".write(", ".write_text(", ".write_bytes(", ".read_text("):
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_fixture_modules_do_not_call_scanner_or_strategy():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        assert "scan_watchlist_strat_212" not in source
        assert "evaluate_strat_212" not in source
