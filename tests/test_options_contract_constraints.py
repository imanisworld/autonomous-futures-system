"""
tests/test_options_contract_constraints.py

Increment 4 — options_manager/contracts/contract_validator.py tests.
Proves the advisory-only contract-constraints validator is a pure
function that produces real VALID/CAUTION/INVALID content from a
caller-supplied contract's own data (liquidity, spread, greeks, DTE) and
caller-supplied risk limits, fails closed to INVALID whenever a required
input is missing, hard-rejects contracts that violate a risk limit, and
returns CAUTION (never a false VALID) whenever a contract is merely
near a threshold.
"""

from __future__ import annotations

import ast
from pathlib import Path

import options_manager.contracts.base as contracts_base_module
import options_manager.contracts.contract_validator as contract_validator_module
from options_manager.contracts.base import ContractConstraintsInputs
from options_manager.contracts.contract_validator import evaluate_contract_constraints

_SCANNED_CONTRACT_MODULES = (contracts_base_module, contract_validator_module)

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
    "options_manager.levels",
    "options_manager.levels.base",
    "options_manager.levels.target_finder",
    "options_manager.context",
    "options_manager.context.base",
    "options_manager.context.market_validator",
)

_FORBIDDEN_ORDER_ACTION_IDENTIFIERS = (
    "place_order",
    "submit_order",
    "cancel_order",
    "replace_order",
    "execute_order",
    "live_order",
)


def _valid_call_inputs(**overrides) -> ContractConstraintsInputs:
    fields = dict(
        direction="CALL",
        ticker="SPY",
        expiration="2026-08-01",
        dte=30,
        strike=505.0,
        premium=2.50,
        bid=2.45,
        ask=2.55,
        spread_percent=0.04,
        volume=500,
        open_interest=1000,
        delta=0.35,
        theta=-0.05,
        iv=0.25,
        max_premium=5.0,
        max_spread_percent=0.10,
        min_volume=100,
        min_open_interest=200,
        min_dte=7,
        max_theta_abs=0.10,
        earnings_risk="NONE",
        event_risk="NONE",
    )
    fields.update(overrides)
    return ContractConstraintsInputs(**fields)


def _valid_put_inputs(**overrides) -> ContractConstraintsInputs:
    fields = dict(
        direction="PUT",
        ticker="QQQ",
        expiration="2026-08-01",
        dte=30,
        strike=395.0,
        premium=2.50,
        bid=2.45,
        ask=2.55,
        spread_percent=0.04,
        volume=500,
        open_interest=1000,
        delta=-0.35,
        theta=-0.05,
        iv=0.25,
        max_premium=5.0,
        max_spread_percent=0.10,
        min_volume=100,
        min_open_interest=200,
        min_dte=7,
        max_theta_abs=0.10,
        earnings_risk="NONE",
        event_risk="NONE",
    )
    fields.update(overrides)
    return ContractConstraintsInputs(**fields)


# --- 1. valid CALL contract passes ------------------------------------------------------


def test_valid_call_contract_returns_valid():
    result = evaluate_contract_constraints(_valid_call_inputs())
    assert result.status == "VALID"
    assert result.confirmed is True
    assert result.reason_code == "contract_confirmed"
    assert result.warnings == []
    assert result.contract_score == 6.0


# --- 2. valid PUT contract passes -------------------------------------------------------


def test_valid_put_contract_returns_valid():
    result = evaluate_contract_constraints(_valid_put_inputs())
    assert result.status == "VALID"
    assert result.confirmed is True
    assert result.reason_code == "contract_confirmed"
    assert result.warnings == []


# --- 3. missing required fields fail closed ----------------------------------------------


def test_invalid_direction_fails_closed_to_invalid():
    result = evaluate_contract_constraints(_valid_call_inputs(direction="LONG"))  # type: ignore[arg-type]
    assert result.status == "INVALID"
    assert result.reason_code == "invalid_direction"


def test_missing_ticker_fails_closed_to_invalid():
    result = evaluate_contract_constraints(_valid_call_inputs(ticker=None))
    assert result.status == "INVALID"
    assert result.reason_code == "missing_ticker"


def test_missing_expiration_fails_closed_to_invalid():
    result = evaluate_contract_constraints(_valid_call_inputs(expiration=None))
    assert result.status == "INVALID"
    assert result.reason_code == "missing_expiration"


def test_missing_dte_fails_closed_to_invalid():
    result = evaluate_contract_constraints(_valid_call_inputs(dte=None))
    assert result.status == "INVALID"
    assert result.reason_code == "missing_dte"


def test_missing_strike_fails_closed_to_invalid():
    result = evaluate_contract_constraints(_valid_call_inputs(strike=None))
    assert result.status == "INVALID"
    assert result.reason_code == "missing_strike"


def test_missing_premium_fails_closed_to_invalid():
    result = evaluate_contract_constraints(_valid_call_inputs(premium=None))
    assert result.status == "INVALID"
    assert result.reason_code == "missing_premium"


def test_missing_bid_fails_closed_to_invalid():
    result = evaluate_contract_constraints(_valid_call_inputs(bid=None))
    assert result.status == "INVALID"
    assert result.reason_code == "missing_bid"


def test_missing_ask_fails_closed_to_invalid():
    result = evaluate_contract_constraints(_valid_call_inputs(ask=None))
    assert result.status == "INVALID"
    assert result.reason_code == "missing_ask"


def test_missing_spread_percent_fails_closed_to_invalid():
    result = evaluate_contract_constraints(_valid_call_inputs(spread_percent=None))
    assert result.status == "INVALID"
    assert result.reason_code == "missing_spread_percent"


def test_missing_volume_fails_closed_to_invalid():
    result = evaluate_contract_constraints(_valid_call_inputs(volume=None))
    assert result.status == "INVALID"
    assert result.reason_code == "missing_volume"


def test_missing_open_interest_fails_closed_to_invalid():
    result = evaluate_contract_constraints(_valid_call_inputs(open_interest=None))
    assert result.status == "INVALID"
    assert result.reason_code == "missing_open_interest"


def test_missing_delta_fails_closed_to_invalid():
    result = evaluate_contract_constraints(_valid_call_inputs(delta=None))
    assert result.status == "INVALID"
    assert result.reason_code == "missing_delta"


def test_missing_theta_fails_closed_to_invalid():
    result = evaluate_contract_constraints(_valid_call_inputs(theta=None))
    assert result.status == "INVALID"
    assert result.reason_code == "missing_theta"


def test_missing_iv_fails_closed_to_invalid():
    result = evaluate_contract_constraints(_valid_call_inputs(iv=None))
    assert result.status == "INVALID"
    assert result.reason_code == "missing_iv"


def test_missing_risk_limits_fails_closed_to_invalid():
    result = evaluate_contract_constraints(_valid_call_inputs(max_premium=None))
    assert result.status == "INVALID"
    assert result.reason_code == "missing_risk_limits"


# --- 4. hard-reject conditions -----------------------------------------------------------


def test_premium_over_max_fails():
    result = evaluate_contract_constraints(_valid_call_inputs(premium=6.0))
    assert result.status == "INVALID"
    assert result.reason_code == "premium_over_max"


def test_spread_too_wide_fails():
    result = evaluate_contract_constraints(_valid_call_inputs(spread_percent=0.25))
    assert result.status == "INVALID"
    assert result.reason_code == "spread_too_wide"


def test_low_volume_fails():
    result = evaluate_contract_constraints(_valid_call_inputs(volume=50))
    assert result.status == "INVALID"
    assert result.reason_code == "volume_too_low"


def test_low_open_interest_fails():
    result = evaluate_contract_constraints(_valid_call_inputs(open_interest=50))
    assert result.status == "INVALID"
    assert result.reason_code == "open_interest_too_low"


def test_dte_too_short_fails():
    result = evaluate_contract_constraints(_valid_call_inputs(dte=3))
    assert result.status == "INVALID"
    assert result.reason_code == "dte_too_short"


def test_theta_too_high_fails_when_threshold_supplied():
    result = evaluate_contract_constraints(
        _valid_call_inputs(theta=-0.20, max_theta_abs=0.10)
    )
    assert result.status == "INVALID"
    assert result.reason_code == "theta_too_high"


def test_theta_not_checked_without_explicit_threshold():
    result = evaluate_contract_constraints(
        _valid_call_inputs(theta=-0.90, max_theta_abs=None)
    )
    assert result.status == "VALID"


def test_high_earnings_risk_fails():
    result = evaluate_contract_constraints(_valid_call_inputs(earnings_risk="HIGH"))
    assert result.status == "INVALID"
    assert result.reason_code == "earnings_risk_high"


def test_high_event_risk_fails():
    result = evaluate_contract_constraints(_valid_call_inputs(event_risk="HIGH"))
    assert result.status == "INVALID"
    assert result.reason_code == "event_risk_high"


def test_bad_bid_fails():
    result = evaluate_contract_constraints(_valid_call_inputs(bid=0.0))
    assert result.status == "INVALID"
    assert result.reason_code == "bid_invalid"


def test_bad_ask_fails():
    result = evaluate_contract_constraints(_valid_call_inputs(bid=2.50, ask=2.50))
    assert result.status == "INVALID"
    assert result.reason_code == "ask_invalid"


# --- 5. near-threshold CAUTION -------------------------------------------------------------


def test_near_threshold_spread_returns_caution():
    result = evaluate_contract_constraints(
        _valid_call_inputs(spread_percent=0.095, max_spread_percent=0.10)
    )
    assert result.status == "CAUTION"
    assert result.reason_code == "contract_mixed"
    assert result.warnings


def test_near_threshold_dte_returns_caution():
    result = evaluate_contract_constraints(_valid_call_inputs(dte=8, min_dte=7))
    assert result.status == "CAUTION"
    assert result.reason_code == "contract_mixed"


def test_near_threshold_volume_returns_caution():
    result = evaluate_contract_constraints(_valid_call_inputs(volume=110, min_volume=100))
    assert result.status == "CAUTION"
    assert result.reason_code == "contract_mixed"


def test_near_threshold_open_interest_returns_caution():
    result = evaluate_contract_constraints(
        _valid_call_inputs(open_interest=220, min_open_interest=200)
    )
    assert result.status == "CAUTION"
    assert result.reason_code == "contract_mixed"


def test_near_threshold_theta_returns_caution():
    result = evaluate_contract_constraints(
        _valid_call_inputs(theta=-0.095, max_theta_abs=0.10)
    )
    assert result.status == "CAUTION"
    assert result.reason_code == "contract_mixed"


def test_elevated_iv_returns_caution():
    result = evaluate_contract_constraints(_valid_call_inputs(iv=0.85))
    assert result.status == "CAUTION"
    assert result.reason_code == "contract_mixed"


# --- structural safety (matches this buildout's established pattern) ----------------------


def _imported_modules(module) -> list[str]:
    """Absolute module names only; relative imports (level > 0) resolve
    within the same package and are excluded rather than misreported as a
    cross-boundary import (see the Increment 1/2/3 fix for the same
    issue)."""
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def test_contract_modules_have_no_forbidden_imports():
    for module in _SCANNED_CONTRACT_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in name, f"{module.__name__} must not import {name!r}"


def test_contract_modules_do_not_import_existing_pipeline_strategies_levels_or_context_modules():
    for module in _SCANNED_CONTRACT_MODULES:
        imported = _imported_modules(module)
        overlap = set(imported) & set(_EXISTING_PIPELINE_MODULES)
        assert not overlap, (
            f"{module.__name__} must not import existing pipeline/strategies/levels/"
            f"context modules (Increment 4 is additive-only, not wired in): {overlap}"
        )


def test_contract_modules_have_no_cross_boundary_imports_at_all():
    for module in _SCANNED_CONTRACT_MODULES:
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


def test_contract_modules_have_no_order_action_verbs():
    for module in _SCANNED_CONTRACT_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in _FORBIDDEN_ORDER_ACTION_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_contract_modules_do_not_mutate_live_options_flag():
    for module in _SCANNED_CONTRACT_MODULES:
        source = Path(module.__file__).read_text()
        assert "LIVE_OPTIONS_TRADING_ENABLED" not in source
