"""
tests/test_options_market_context.py

Increment 3 — options_manager/context/market_validator.py tests. Proves
the advisory-only market-context validator is a pure function that
produces real VALID/CAUTION/INVALID content from caller-supplied SPY/
QQQ/GEX/Signa/HTF/event-risk inputs, fails closed to INVALID whenever a
required input is missing, and returns CAUTION (never a false VALID)
whenever the context is merely mixed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import options_manager.context.base as context_base_module
import options_manager.context.market_validator as market_validator_module
from options_manager.context.base import MarketContextInputs
from options_manager.context.market_validator import evaluate_market_context

_SCANNED_CONTEXT_MODULES = (context_base_module, market_validator_module)

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
)

_FORBIDDEN_ORDER_ACTION_IDENTIFIERS = (
    "place_order",
    "submit_order",
    "cancel_order",
    "replace_order",
    "execute_order",
    "live_order",
)


def _valid_call_inputs(**overrides) -> MarketContextInputs:
    fields = dict(
        direction="CALL",
        ticker="SPY",
        underlying_price=500.0,
        spy_trend="bullish",
        qqq_trend="bullish",
        spy_above_flip=True,
        qqq_above_flip=True,
        gex_regime="positive",
        price_above_gex_flip=True,
        signa_direction="bullish",
        signa_grade="A",
        signa_score=80.0,
        higher_timeframe_alignment="aligned",
        gap_direction="none",
        distance_to_gamma_resistance=5.0,
        distance_to_gamma_support=5.0,
        event_risk="none",
    )
    fields.update(overrides)
    return MarketContextInputs(**fields)


def _valid_put_inputs(**overrides) -> MarketContextInputs:
    fields = dict(
        direction="PUT",
        ticker="QQQ",
        underlying_price=400.0,
        spy_trend="bearish",
        qqq_trend="bearish",
        spy_above_flip=False,
        qqq_above_flip=False,
        gex_regime="negative",
        price_above_gex_flip=False,
        signa_direction="bearish",
        signa_grade="A",
        signa_score=80.0,
        higher_timeframe_alignment="aligned",
        gap_direction="none",
        distance_to_gamma_resistance=5.0,
        distance_to_gamma_support=5.0,
        event_risk="none",
    )
    fields.update(overrides)
    return MarketContextInputs(**fields)


# --- 1. valid CALL context passes ------------------------------------------------------


def test_valid_call_context_returns_valid():
    result = evaluate_market_context(_valid_call_inputs())
    assert result.status == "VALID"
    assert result.confirmed is True
    assert result.reason_code == "context_confirmed"
    assert result.warnings == []
    assert result.context_score == 5.0


# --- 2. valid PUT context passes --------------------------------------------------------


def test_valid_put_context_returns_valid():
    result = evaluate_market_context(_valid_put_inputs())
    assert result.status == "VALID"
    assert result.confirmed is True
    assert result.reason_code == "context_confirmed"
    assert result.warnings == []


# --- 3. mixed context returns CAUTION ---------------------------------------------------


def test_mixed_index_alignment_returns_caution_not_invalid():
    result = evaluate_market_context(
        _valid_call_inputs(qqq_trend="neutral", qqq_above_flip=False)
    )
    assert result.status == "CAUTION"
    assert result.confirmed is True
    assert result.reason_code == "context_mixed"
    assert result.warnings


# --- 4. missing SPY/QQQ fails closed -----------------------------------------------------


def test_missing_spy_qqq_context_fails_closed_to_invalid():
    result = evaluate_market_context(_valid_call_inputs(spy_trend=None))
    assert result.status == "INVALID"
    assert result.confirmed is False
    assert result.reason_code == "missing_spy_qqq_context"


# --- 5. missing GEX degrades to CAUTION, never INVALID -----------------------------------


def test_missing_gex_regime_degrades_to_caution_not_invalid():
    """GEX is optional enrichment, not a gate input. Rejecting on it would
    make an unsubscribed vendor feed a hard dependency of the lane."""
    result = evaluate_market_context(_valid_call_inputs(gex_regime=None))
    assert result.status == "CAUTION"
    assert result.confirmed is True
    assert result.gex_available is False
    assert any("GEX_UNAVAILABLE" in w for w in result.warnings)


def test_missing_price_above_gex_flip_also_degrades_to_caution():
    result = evaluate_market_context(_valid_call_inputs(price_above_gex_flip=None))
    assert result.status == "CAUTION"
    assert result.gex_available is False
    assert any("GEX_UNAVAILABLE" in w for w in result.warnings)


def test_half_supplied_gex_block_is_treated_as_unavailable():
    """A regime with no flip side is unusable; the validator must not infer
    the missing half."""
    result = evaluate_market_context(
        _valid_call_inputs(gex_regime="positive", price_above_gex_flip=None)
    )
    assert result.gex_available is False
    assert any("GEX_UNAVAILABLE" in w for w in result.warnings)


def test_gex_unavailable_drops_component_from_both_sides_of_context_score():
    """The absent component must score as neither aligned nor opposed."""
    with_gex = evaluate_market_context(_valid_call_inputs())
    without_gex = evaluate_market_context(
        _valid_call_inputs(gex_regime=None, price_above_gex_flip=None)
    )
    assert with_gex.context_score == 5.0
    assert with_gex.context_score_max == 5.0
    assert without_gex.context_score == 4.0
    assert without_gex.context_score_max == 4.0


def test_gex_unavailable_skips_gamma_wall_targeting():
    """min_distance_to_gamma_level cannot be enforced without walls, and the
    validator must say so rather than silently drop the threshold."""
    result = evaluate_market_context(
        _valid_call_inputs(
            gex_regime=None,
            price_above_gex_flip=None,
            distance_to_gamma_resistance=0.01,
            min_distance_to_gamma_level=5.0,
        )
    )
    # Would have been INVALID/gamma_resistance_too_close with GEX present.
    assert result.status == "CAUTION"
    assert any("gamma-distance inputs ignored" in w for w in result.warnings)


def test_gamma_wall_targeting_still_enforced_when_gex_is_present():
    result = evaluate_market_context(
        _valid_call_inputs(
            distance_to_gamma_resistance=0.01, min_distance_to_gamma_level=5.0
        )
    )
    assert result.status == "INVALID"
    assert result.reason_code == "gamma_resistance_too_close"


def test_signa_conflict_still_rejects_without_gex():
    """Removing the GEX dependency must not weaken the Signa gate."""
    result = evaluate_market_context(
        _valid_call_inputs(
            gex_regime=None,
            price_above_gex_flip=None,
            signa_direction="bearish",
            signa_grade="A",
        )
    )
    assert result.status == "INVALID"
    assert result.reason_code == "signa_conflict"


def test_missing_signa_still_fails_closed_without_gex():
    result = evaluate_market_context(
        _valid_call_inputs(
            gex_regime=None, price_above_gex_flip=None, signa_direction=None
        )
    )
    assert result.status == "INVALID"
    assert result.reason_code == "missing_signa_context"


def test_missing_spy_qqq_still_fails_closed_without_gex():
    result = evaluate_market_context(
        _valid_call_inputs(gex_regime=None, price_above_gex_flip=None, spy_trend=None)
    )
    assert result.status == "INVALID"
    assert result.reason_code == "missing_spy_qqq_context"


def test_missing_htf_still_fails_closed_without_gex():
    result = evaluate_market_context(
        _valid_call_inputs(
            gex_regime=None,
            price_above_gex_flip=None,
            higher_timeframe_alignment=None,
        )
    )
    assert result.status == "INVALID"
    assert result.reason_code == "missing_htf_alignment"


def test_gex_less_context_never_reports_valid():
    """Honest Signa-only operation tops out at CAUTION: the system runs
    without GEX, but never claims full confirmation while it is missing."""
    result = evaluate_market_context(
        _valid_call_inputs(gex_regime=None, price_above_gex_flip=None)
    )
    assert result.status != "VALID"


def test_no_neutral_regime_or_flip_is_fabricated_when_gex_is_absent():
    result = evaluate_market_context(
        _valid_call_inputs(gex_regime=None, price_above_gex_flip=None)
    )
    assert result.gex_available is False
    # No warning may claim a side of a flip that was never supplied.
    assert not any("preferred side of the GEX flip" in w for w in result.warnings)


# --- 6. missing Signa fails closed -------------------------------------------------------


def test_missing_signa_context_fails_closed_to_invalid():
    result = evaluate_market_context(_valid_call_inputs(signa_direction=None))
    assert result.status == "INVALID"
    assert result.reason_code == "missing_signa_context"


# --- 7. missing HTF alignment fails closed -----------------------------------------------


def test_missing_htf_alignment_fails_closed_to_invalid():
    result = evaluate_market_context(_valid_call_inputs(higher_timeframe_alignment=None))
    assert result.status == "INVALID"
    assert result.reason_code == "missing_htf_alignment"


# --- 8. high event risk fails closed -----------------------------------------------------


def test_high_event_risk_fails_closed_to_invalid():
    result = evaluate_market_context(_valid_call_inputs(event_risk="high"))
    assert result.status == "INVALID"
    assert result.reason_code == "event_risk_high"


def test_missing_event_risk_fails_closed_to_invalid():
    result = evaluate_market_context(_valid_call_inputs(event_risk=None))
    assert result.status == "INVALID"
    assert result.reason_code == "missing_event_risk"


# --- 9. CALL rejected when gamma resistance too close ------------------------------------


def test_call_rejected_when_gamma_resistance_too_close():
    result = evaluate_market_context(
        _valid_call_inputs(distance_to_gamma_resistance=0.2, min_distance_to_gamma_level=1.0)
    )
    assert result.status == "INVALID"
    assert result.reason_code == "gamma_resistance_too_close"


# --- 10. PUT rejected when gamma support too close ----------------------------------------


def test_put_rejected_when_gamma_support_too_close():
    result = evaluate_market_context(
        _valid_put_inputs(distance_to_gamma_support=0.2, min_distance_to_gamma_level=1.0)
    )
    assert result.status == "INVALID"
    assert result.reason_code == "gamma_support_too_close"


# --- 11. CALL rejected on bearish market conflict -----------------------------------------


def test_call_rejected_on_bearish_market_conflict():
    result = evaluate_market_context(
        _valid_call_inputs(
            spy_trend="bearish", qqq_trend="bearish", signa_direction="neutral"
        )
    )
    assert result.status == "INVALID"
    assert result.reason_code == "market_conflict"


# --- 12. PUT rejected on bullish market conflict ------------------------------------------


def test_put_rejected_on_bullish_market_conflict():
    result = evaluate_market_context(
        _valid_put_inputs(
            spy_trend="bullish", qqq_trend="bullish", signa_direction="neutral"
        )
    )
    assert result.status == "INVALID"
    assert result.reason_code == "market_conflict"


# --- extra: Signa conflict (strong vs. weak grade) ----------------------------------------


def test_call_rejected_on_strong_bearish_signa_conflict():
    result = evaluate_market_context(
        _valid_call_inputs(signa_direction="bearish", signa_grade="A", signa_score=70.0)
    )
    assert result.status == "INVALID"
    assert result.reason_code == "signa_conflict"


def test_call_allows_weak_bearish_signa_without_hard_rejecting():
    result = evaluate_market_context(
        _valid_call_inputs(signa_direction="bearish", signa_grade="D", signa_score=20.0)
    )
    assert result.status == "CAUTION"
    assert result.reason_code == "context_mixed"


# --- extra: gamma proximity is only enforced when a threshold is supplied ------------------


def test_gamma_proximity_not_enforced_without_explicit_threshold():
    result = evaluate_market_context(
        _valid_call_inputs(distance_to_gamma_resistance=0.01)
    )
    assert result.status == "VALID"


# --- extra: fully opposite higher-timeframe alignment fails closed ------------------------


def test_opposite_htf_alignment_fails_closed_to_invalid():
    result = evaluate_market_context(
        _valid_call_inputs(higher_timeframe_alignment="opposite")
    )
    assert result.status == "INVALID"
    assert result.reason_code == "htf_opposite"


def test_mixed_htf_alignment_returns_caution_not_invalid():
    result = evaluate_market_context(_valid_call_inputs(higher_timeframe_alignment="mixed"))
    assert result.status == "CAUTION"
    assert result.reason_code == "context_mixed"


# --- extra: invalid direction / missing ticker / missing price ----------------------------


def test_invalid_direction_fails_closed_to_invalid():
    result = evaluate_market_context(_valid_call_inputs(direction="LONG"))  # type: ignore[arg-type]
    assert result.status == "INVALID"
    assert result.reason_code == "invalid_direction"


def test_missing_ticker_fails_closed_to_invalid():
    result = evaluate_market_context(_valid_call_inputs(ticker=None))
    assert result.status == "INVALID"
    assert result.reason_code == "missing_ticker"


def test_missing_underlying_price_fails_closed_to_invalid():
    result = evaluate_market_context(_valid_call_inputs(underlying_price=None))
    assert result.status == "INVALID"
    assert result.reason_code == "missing_underlying_price"


# --- structural safety (matches this buildout's established pattern) ----------------------


def _imported_modules(module) -> list[str]:
    """Absolute module names only; relative imports (level > 0) resolve
    within the same package and are excluded rather than misreported as a
    cross-boundary import (see the Increment 1/2 fix for the same issue)."""
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def test_context_modules_have_no_forbidden_imports():
    for module in _SCANNED_CONTEXT_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in name, f"{module.__name__} must not import {name!r}"


def test_context_modules_do_not_import_existing_pipeline_strategies_or_levels_modules():
    for module in _SCANNED_CONTEXT_MODULES:
        imported = _imported_modules(module)
        overlap = set(imported) & set(_EXISTING_PIPELINE_MODULES)
        assert not overlap, (
            f"{module.__name__} must not import existing pipeline/strategies/levels "
            f"modules (Increment 3 is additive-only, not wired in): {overlap}"
        )


def test_context_modules_do_not_import_live_context_loader():
    for module in _SCANNED_CONTEXT_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            assert name != "context" and not name.startswith("context."), (
                f"{module.__name__} must not import the live context.* loader "
                f"(pulls config, not caller-supplied): {name}"
            )


def test_context_modules_have_no_cross_boundary_imports_at_all():
    for module in _SCANNED_CONTEXT_MODULES:
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


def test_context_modules_have_no_order_action_verbs():
    for module in _SCANNED_CONTEXT_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in _FORBIDDEN_ORDER_ACTION_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_context_modules_do_not_mutate_live_options_flag():
    for module in _SCANNED_CONTEXT_MODULES:
        source = Path(module.__file__).read_text()
        assert "LIVE_OPTIONS_TRADING_ENABLED" not in source
