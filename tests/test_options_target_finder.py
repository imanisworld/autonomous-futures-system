"""
tests/test_options_target_finder.py

Increment 2 — options_manager/levels/target_finder.py tests. Proves the
advisory-only level/target finder is a pure function that fails closed to
INVALID whenever entry, invalidation, or a valid target candidate is
missing, or when a target is too close or below the minimum R:R
threshold.
"""

from __future__ import annotations

import ast
from pathlib import Path

import options_manager.levels.base as levels_base_module
import options_manager.levels.target_finder as target_finder_module
from options_manager.levels.base import LevelFinderInputs
from options_manager.levels.target_finder import find_targets

_SCANNED_LEVEL_MODULES = (levels_base_module, target_finder_module)

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

_EXISTING_PIPELINE_MODULES = (
    "options_manager.risk_gate",
    "options_manager.contract_quality",
    "options_manager.dry_run_review",
    "options_manager.human_confirm",
    "options_manager.order_ticket",
    "options_manager.broker_boundary",
    "options_manager.mock_broker_preview",
    "options_manager.storage",
    "options_manager.http_api",
    "options_manager.app",
    "options_manager.strategies",
    "options_manager.strategies.base",
    "options_manager.strategies.strat_212",
)

_FORBIDDEN_ORDER_ACTION_IDENTIFIERS = (
    "place_order",
    "submit_order",
    "cancel_order",
    "replace_order",
    "execute_order",
    "live_order",
)


# --- 1. valid CALL (matches the worked example exactly) -------------------------------


def test_valid_call_targets_match_worked_example():
    result = find_targets(
        LevelFinderInputs(
            direction="CALL",
            entry=100.0,
            underlying_invalidation=97.0,
            resistance_levels=(103.0, 108.0),
            gamma_resistance=105.0,
        )
    )
    assert result.status == "VALID"
    assert result.reason_code == "valid_targets"
    assert result.target_1 == 103.0
    assert result.target_2 == 105.0
    assert result.risk_amount == 3.0
    assert result.reward_1 == 3.0
    assert result.rr_1 == 1.0
    assert result.distance_to_target_1 == 3.0
    assert result.distance_to_target_2 == 5.0


# --- 2. valid PUT (matches the worked example exactly) --------------------------------


def test_valid_put_targets_match_worked_example():
    result = find_targets(
        LevelFinderInputs(
            direction="PUT",
            entry=100.0,
            underlying_invalidation=103.0,
            support_levels=(97.0, 94.0),
            gamma_support=95.0,
        )
    )
    assert result.status == "VALID"
    assert result.reason_code == "valid_targets"
    assert result.target_1 == 97.0
    assert result.target_2 == 95.0
    assert result.risk_amount == 3.0
    assert result.reward_1 == 3.0
    assert result.rr_1 == 1.0


# --- 3. missing entry ------------------------------------------------------------------


def test_missing_entry_fails_closed_to_invalid():
    result = find_targets(
        LevelFinderInputs(
            direction="CALL",
            entry=None,
            underlying_invalidation=97.0,
            resistance_levels=(103.0, 108.0),
        )
    )
    assert result.status == "INVALID"
    assert result.reason_code == "missing_entry"


# --- 4. missing invalidation -------------------------------------------------------------


def test_missing_invalidation_fails_closed_to_invalid():
    result = find_targets(
        LevelFinderInputs(
            direction="CALL",
            entry=100.0,
            underlying_invalidation=None,
            resistance_levels=(103.0, 108.0),
        )
    )
    assert result.status == "INVALID"
    assert result.reason_code == "missing_invalidation"


# --- 5. invalid direction -----------------------------------------------------------------


def test_invalid_direction_fails_closed_to_invalid():
    result = find_targets(
        LevelFinderInputs(
            direction="LONG",  # type: ignore[arg-type]
            entry=100.0,
            underlying_invalidation=97.0,
            resistance_levels=(103.0,),
        )
    )
    assert result.status == "INVALID"
    assert result.reason_code == "invalid_direction"


# --- 6. invalidation on wrong side ---------------------------------------------------------


def test_call_invalidation_above_entry_fails_closed_to_invalid():
    result = find_targets(
        LevelFinderInputs(
            direction="CALL",
            entry=100.0,
            underlying_invalidation=101.0,  # wrong side for CALL
            resistance_levels=(103.0, 108.0),
        )
    )
    assert result.status == "INVALID"
    assert result.reason_code == "invalidation_wrong_side"


def test_put_invalidation_below_entry_fails_closed_to_invalid():
    result = find_targets(
        LevelFinderInputs(
            direction="PUT",
            entry=100.0,
            underlying_invalidation=99.0,  # wrong side for PUT
            support_levels=(97.0, 94.0),
        )
    )
    assert result.status == "INVALID"
    assert result.reason_code == "invalidation_wrong_side"


# --- 7. no target_1 (no valid levels at all) ------------------------------------------------


def test_no_valid_levels_fails_closed_with_no_target_1():
    result = find_targets(
        LevelFinderInputs(
            direction="CALL",
            entry=100.0,
            underlying_invalidation=97.0,
            resistance_levels=(95.0,),  # below entry — not a valid CALL target
        )
    )
    assert result.status == "INVALID"
    assert result.reason_code == "no_target_1"


# --- 8. no target_2 (exactly one valid level) -----------------------------------------------


def test_single_valid_level_fails_closed_with_no_target_2():
    result = find_targets(
        LevelFinderInputs(
            direction="CALL",
            entry=100.0,
            underlying_invalidation=97.0,
            resistance_levels=(103.0,),
        )
    )
    assert result.status == "INVALID"
    assert result.reason_code == "no_target_2"


# --- 9. target too close ---------------------------------------------------------------------


def test_target_closer_than_minimum_distance_fails_closed_to_invalid():
    result = find_targets(
        LevelFinderInputs(
            direction="CALL",
            entry=100.0,
            underlying_invalidation=97.0,
            resistance_levels=(103.0, 108.0),
            min_distance_to_target=5.0,  # target_1 distance is only 3.0
        )
    )
    assert result.status == "INVALID"
    assert result.reason_code == "target_too_close"


# --- 10. R:R below threshold ------------------------------------------------------------------


def test_rr_below_threshold_fails_closed_to_invalid():
    result = find_targets(
        LevelFinderInputs(
            direction="CALL",
            entry=100.0,
            underlying_invalidation=97.0,  # risk = 3
            resistance_levels=(102.0, 108.0),  # reward_1 = 2 -> rr_1 = 0.667
            min_rr_threshold=1.0,
        )
    )
    assert result.status == "INVALID"
    assert result.reason_code == "rr_below_threshold"


# --- duplicate/edge-case sanity (cheap, not in the required list) -----------------------------


def test_duplicate_level_across_resistance_and_gamma_collapses_to_one_candidate():
    result = find_targets(
        LevelFinderInputs(
            direction="CALL",
            entry=100.0,
            underlying_invalidation=97.0,
            resistance_levels=(103.0, 108.0),
            gamma_resistance=103.0,  # duplicate of an existing resistance level
        )
    )
    assert result.status == "VALID"
    assert result.target_1 == 103.0
    assert result.target_2 == 108.0


def test_level_exactly_at_entry_is_excluded_as_a_candidate():
    result = find_targets(
        LevelFinderInputs(
            direction="CALL",
            entry=100.0,
            underlying_invalidation=97.0,
            resistance_levels=(100.0, 103.0, 108.0),  # 100.0 == entry, must be excluded
        )
    )
    assert result.status == "VALID"
    assert result.target_1 == 103.0
    assert result.target_2 == 108.0


# --- structural safety (matches this buildout's established pattern) --------------------------


def _imported_modules(module) -> list[str]:
    """Absolute module names only; relative imports (level > 0) resolve
    within the same package and are excluded rather than misreported as a
    cross-boundary import (see the Increment 1 fix for the same issue)."""
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def test_level_modules_have_no_forbidden_imports():
    for module in _SCANNED_LEVEL_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in name, f"{module.__name__} must not import {name!r}"


def test_level_modules_do_not_import_existing_pipeline_or_strategies_modules():
    for module in _SCANNED_LEVEL_MODULES:
        imported = _imported_modules(module)
        overlap = set(imported) & set(_EXISTING_PIPELINE_MODULES)
        assert not overlap, (
            f"{module.__name__} must not import existing pipeline/strategies "
            f"modules (Increment 2 is additive-only, not wired in): {overlap}"
        )


def test_level_modules_have_no_cross_boundary_imports_at_all():
    for module in _SCANNED_LEVEL_MODULES:
        imported = _imported_modules(module)
        outside_options_manager = [
            name
            for name in imported
            if not name.startswith("options_manager")
            and name not in ("__future__", "dataclasses", "typing")
        ]
        assert not outside_options_manager, (
            f"{module.__name__} has an unexpected cross-boundary import: "
            f"{outside_options_manager}"
        )


def test_level_modules_have_no_order_action_verbs():
    for module in _SCANNED_LEVEL_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in _FORBIDDEN_ORDER_ACTION_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_level_modules_do_not_mutate_live_options_flag():
    for module in _SCANNED_LEVEL_MODULES:
        source = Path(module.__file__).read_text()
        assert "LIVE_OPTIONS_TRADING_ENABLED" not in source
