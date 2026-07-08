"""
tests/test_options_strategy_212.py

Increment 1 — options_manager/strategies/strat_212.py tests. Proves the
mechanical Strat 2-1-2 continuation validator is advisory-only: no broker
calls, no execution, no order placement, and fails closed to INVALID
whenever entry, invalidation, target, market-context, or contract-
constraint input is missing.
"""

from __future__ import annotations

import ast
from pathlib import Path

import options_manager.strategies.base as strategies_base_module
import options_manager.strategies.strat_212 as strat_212_module
from options_manager.strategies.base import (
    StrategyContractConstraints,
    StrategyMarketContext,
)
from options_manager.strategies.strat_212 import Strat212Bars, evaluate_strat_212

_SCANNED_STRATEGY_MODULES = (strategies_base_module, strat_212_module)

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
)

_FORBIDDEN_ORDER_ACTION_IDENTIFIERS = (
    "place_order",
    "submit_order",
    "cancel_order",
    "replace_order",
    "execute_order",
    "live_order",
)


def _bullish_bars(*, current_high: float = 101.0, current_low: float = 96.5) -> Strat212Bars:
    return Strat212Bars(
        two_bars_back_type="two_up",
        two_bars_back_high=100.0,
        two_bars_back_low=95.0,
        previous_high=99.0,
        previous_low=96.0,
        current_high=current_high,
        current_low=current_low,
    )


def _bearish_bars(*, current_high: float = 98.5, current_low: float = 94.0) -> Strat212Bars:
    return Strat212Bars(
        two_bars_back_type="two_down",
        two_bars_back_high=100.0,
        two_bars_back_low=95.0,
        previous_high=99.0,
        previous_low=96.0,
        current_high=current_high,
        current_low=current_low,
    )


def _confirmed_context() -> StrategyMarketContext:
    return StrategyMarketContext(confirmed=True)


def _met_constraints() -> StrategyContractConstraints:
    return StrategyContractConstraints(constraints_met=True)


# --- 1. valid bullish 2-1-2 ----------------------------------------------------------


def test_valid_bullish_212_returns_valid():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "VALID"
    assert result.reason_code == "valid_212_continuation"
    assert result.direction == "CALL"
    assert result.candle_sequence == "strat_212"
    assert result.entry_trigger == 99.0
    assert result.underlying_invalidation == 95.5
    assert result.target_1 == 103.0
    assert result.target_2 == 106.0


# --- 2. valid bearish 2-1-2 ----------------------------------------------------------


def test_valid_bearish_212_returns_valid():
    result = evaluate_strat_212(
        _bearish_bars(),
        direction="PUT",
        entry_trigger=96.0,
        underlying_invalidation=99.5,
        target_1=92.0,
        target_2=89.0,
        market_context=_confirmed_context(),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "VALID"
    assert result.reason_code == "valid_212_continuation"
    assert result.direction == "PUT"
    assert result.candle_sequence == "strat_212"


# --- 3. watch state before trigger ----------------------------------------------------


def test_bullish_setup_forming_but_not_triggered_returns_watch():
    bars = Strat212Bars(
        two_bars_back_type="two_up",
        two_bars_back_high=100.0,
        two_bars_back_low=95.0,
        previous_high=99.0,
        previous_low=96.0,
        current_high=98.5,  # still inside the previous (inside) bar — no breakout yet
        current_low=96.5,
    )
    result = evaluate_strat_212(
        bars,
        direction="CALL",
        entry_trigger=None,
        underlying_invalidation=None,
        target_1=None,
        target_2=None,
        market_context=StrategyMarketContext(),
        contract_constraints=StrategyContractConstraints(),
    )
    assert result.status == "WATCH"
    assert result.reason_code == "setup_forming_not_triggered"


# --- 4. invalid sequence ---------------------------------------------------------------


def test_outside_bar_current_is_invalid_sequence():
    bars = Strat212Bars(
        two_bars_back_type="two_up",
        two_bars_back_high=100.0,
        two_bars_back_low=95.0,
        previous_high=99.0,
        previous_low=96.0,
        current_high=102.0,  # breaks both sides of the inside bar -> outside bar
        current_low=93.0,
    )
    result = evaluate_strat_212(
        bars,
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "sequence_not_212"


# --- 5. missing invalidation -------------------------------------------------------------


def test_missing_invalidation_fails_closed_to_invalid():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=None,
        target_1=103.0,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "missing_invalidation"


# --- 6. missing targets --------------------------------------------------------------------


def test_missing_target_1_fails_closed_to_invalid():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=None,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "missing_target_1"


def test_missing_target_2_fails_closed_to_invalid():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=None,
        market_context=_confirmed_context(),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "missing_target_2"


# --- 7. missing market context -----------------------------------------------------------


def test_missing_market_context_fails_closed_to_invalid():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=StrategyMarketContext(),  # confirmed=None by default
        contract_constraints=_met_constraints(),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "missing_market_context"


def test_rejected_market_context_fails_closed_to_invalid():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=StrategyMarketContext(confirmed=False, notes="SPY/QQQ conflict"),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "market_context_rejected"


# --- 8. missing contract constraints ------------------------------------------------------


def test_missing_contract_constraints_fails_closed_to_invalid():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=StrategyContractConstraints(),  # constraints_met=None
    )
    assert result.status == "INVALID"
    assert result.reason_code == "missing_contract_constraints"


def test_rejected_contract_constraints_fails_closed_to_invalid():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=StrategyContractConstraints(
            constraints_met=False, notes="spread too wide"
        ),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "contract_constraints_rejected"


# --- invalidation/target side-sanity (defensive, not in the required list but cheap) -------


def test_invalidation_on_wrong_side_for_call_is_invalid():
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="CALL",
        entry_trigger=99.0,
        underlying_invalidation=100.0,  # above entry — wrong side for CALL
        target_1=103.0,
        target_2=106.0,
        market_context=_confirmed_context(),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "invalidation_wrong_side"


def test_direction_mismatch_against_classified_sequence_is_invalid():
    # Bars form a bullish strat_212, but the caller asks for PUT.
    result = evaluate_strat_212(
        _bullish_bars(),
        direction="PUT",
        entry_trigger=99.0,
        underlying_invalidation=100.5,
        target_1=95.0,
        target_2=92.0,
        market_context=_confirmed_context(),
        contract_constraints=_met_constraints(),
    )
    assert result.status == "INVALID"
    assert result.reason_code == "direction_mismatch"


# --- 9. advisory output cannot bypass existing risk gates -----------------------------------
#
# Increment 1 wires nothing into options_manager's existing pipeline — a
# StrategySignal has no code path into risk_gate/contract_quality/
# dry_run_review/human_confirm/order_ticket/broker_boundary/
# mock_broker_preview/storage/http_api/app because the strategy layer
# never imports any of them. Proven structurally (AST import scan) rather
# than by exercising the pipeline, since no wiring exists yet to exercise.


def _imported_modules(module) -> list[str]:
    """Absolute module names only. `ast.ImportFrom.level > 0` marks a
    relative import (e.g. `from .base import X` inside strat_212.py) —
    those resolve within the same package, not to a top-level module
    named "base", so they are excluded rather than misreported as a
    cross-boundary import."""
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def test_strategy_modules_do_not_import_existing_pipeline_modules():
    for module in _SCANNED_STRATEGY_MODULES:
        imported = _imported_modules(module)
        overlap = set(imported) & set(_EXISTING_PIPELINE_MODULES)
        assert not overlap, (
            f"{module.__name__} must not import existing pipeline modules "
            f"(would create a bypass path): {overlap}"
        )


def test_strategy_modules_only_cross_boundary_import_is_strategy_strat_classifier():
    for module in _SCANNED_STRATEGY_MODULES:
        imported = _imported_modules(module)
        outside_options_manager = [
            name
            for name in imported
            if not name.startswith("options_manager")
            and name not in ("__future__", "dataclasses", "typing")
        ]
        assert set(outside_options_manager) <= {"strategy.strat_classifier", "strategy"}, (
            f"{module.__name__} has an unexpected cross-boundary import: "
            f"{outside_options_manager}"
        )


def test_strategy_modules_have_no_forbidden_imports():
    for module in _SCANNED_STRATEGY_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in name, f"{module.__name__} must not import {name!r}"


def test_strategy_modules_have_no_order_action_verbs():
    for module in _SCANNED_STRATEGY_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in _FORBIDDEN_ORDER_ACTION_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_strategy_modules_do_not_mutate_live_options_flag():
    for module in _SCANNED_STRATEGY_MODULES:
        source = Path(module.__file__).read_text()
        assert "LIVE_OPTIONS_TRADING_ENABLED\"] =" not in source
        assert "LIVE_OPTIONS_TRADING_ENABLED'] =" not in source
        assert "LIVE_OPTIONS_TRADING_ENABLED" not in source
