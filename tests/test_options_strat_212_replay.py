"""
tests/test_options_strat_212_replay.py

Increment 5 — options_manager/replay/strat_212_replay.py tests. Proves
the advisory-only 2-1-2 replay wrapper is a pure consumer of
evaluate_strat_212(): it replays caller-supplied rows, never fetches
anything, propagates strategy INVALID/WATCH to the correct replay
outcome, resolves same-bar target/stop conflicts conservatively to
STOP_HIT, and computes correct aggregate metrics.
"""

from __future__ import annotations

import ast
from pathlib import Path

import options_manager.replay.base as replay_base_module
import options_manager.replay.strat_212_replay as strat_212_replay_module
from options_manager.context import MarketContextInputs
from options_manager.contracts import ContractConstraintsInputs
from options_manager.levels import LevelFinderInputs
from options_manager.replay import Strat212ReplayRow, replay_strat_212
from options_manager.strategies import (
    Strat212Bars,
    StrategyContractConstraints,
    StrategyMarketContext,
)

_SCANNED_REPLAY_MODULES = (replay_base_module, strat_212_replay_module)

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


def _bullish_bars() -> Strat212Bars:
    return Strat212Bars(
        two_bars_back_type="two_up",
        two_bars_back_high=100.0,
        two_bars_back_low=95.0,
        previous_high=99.0,
        previous_low=96.0,
        current_high=101.0,
        current_low=96.5,
    )


def _bearish_bars() -> Strat212Bars:
    return Strat212Bars(
        two_bars_back_type="two_down",
        two_bars_back_high=100.0,
        two_bars_back_low=95.0,
        previous_high=99.0,
        previous_low=96.0,
        current_high=98.5,
        current_low=94.0,
    )


def _forming_bars() -> Strat212Bars:
    return Strat212Bars(
        two_bars_back_type="two_up",
        two_bars_back_high=100.0,
        two_bars_back_low=95.0,
        previous_high=99.0,
        previous_low=96.0,
        current_high=98.5,
        current_low=96.5,
    )


def _valid_call_row(**overrides) -> Strat212ReplayRow:
    fields = dict(
        ticker="SPY",
        timestamp="2026-01-02T10:00:00Z",
        direction="CALL",
        bars=_bullish_bars(),
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
        market_context=StrategyMarketContext(confirmed=True),
        contract_constraints=StrategyContractConstraints(constraints_met=True),
    )
    fields.update(overrides)
    return Strat212ReplayRow(**fields)


def _valid_put_row(**overrides) -> Strat212ReplayRow:
    fields = dict(
        ticker="QQQ",
        timestamp="2026-01-02T10:00:00Z",
        direction="PUT",
        bars=_bearish_bars(),
        entry_trigger=96.0,
        underlying_invalidation=99.5,
        target_1=92.0,
        target_2=89.0,
        market_context=StrategyMarketContext(confirmed=True),
        contract_constraints=StrategyContractConstraints(constraints_met=True),
    )
    fields.update(overrides)
    return Strat212ReplayRow(**fields)


# --- 1/2/3. valid CALL row can hit target_1 / target_2 / stop -----------------------------


def test_valid_call_row_hits_target_1():
    report = replay_strat_212([_valid_call_row(future_high=104.0, future_low=98.0)])
    assert report.results[0].replay_outcome == "TARGET_1_HIT"


def test_valid_call_row_hits_target_2():
    report = replay_strat_212([_valid_call_row(future_high=107.0, future_low=98.0)])
    assert report.results[0].replay_outcome == "TARGET_2_HIT"


def test_valid_call_row_hits_stop():
    report = replay_strat_212([_valid_call_row(future_high=100.0, future_low=94.0)])
    assert report.results[0].replay_outcome == "STOP_HIT"


# --- 4/5/6. valid PUT row can hit target_1 / target_2 / stop -------------------------------


def test_valid_put_row_hits_target_1():
    report = replay_strat_212([_valid_put_row(future_high=97.0, future_low=91.0)])
    assert report.results[0].replay_outcome == "TARGET_1_HIT"


def test_valid_put_row_hits_target_2():
    report = replay_strat_212([_valid_put_row(future_high=97.0, future_low=88.0)])
    assert report.results[0].replay_outcome == "TARGET_2_HIT"


def test_valid_put_row_hits_stop():
    report = replay_strat_212([_valid_put_row(future_high=100.0, future_low=95.0)])
    assert report.results[0].replay_outcome == "STOP_HIT"


# --- 7. same-bar target/stop conflict resolves conservatively -----------------------------


def test_same_bar_target_and_stop_conflict_resolves_to_stop_hit():
    report = replay_strat_212([_valid_call_row(future_high=104.0, future_low=94.0)])
    result = report.results[0]
    assert result.replay_outcome == "STOP_HIT"
    assert "conservatively" in result.outcome_reason


# --- 8. invalid strategy row produces INVALID replay outcome -------------------------------


def test_invalid_strategy_row_produces_invalid_replay_outcome():
    row = _valid_call_row(underlying_invalidation=None, future_high=104.0, future_low=98.0)
    report = replay_strat_212([row])
    result = report.results[0]
    assert result.status == "INVALID"
    assert result.replay_outcome == "INVALID"
    assert result.reason_code == "missing_invalidation"
    assert result.outcome_reason == "missing_invalidation"


# --- 9. WATCH strategy row produces NOT_TRIGGERED -------------------------------------------


def test_watch_strategy_row_produces_not_triggered():
    row = Strat212ReplayRow(
        ticker="SPY",
        timestamp="2026-01-02T10:00:00Z",
        direction="CALL",
        bars=_forming_bars(),
        entry_trigger=None,
        underlying_invalidation=None,
        target_1=None,
        target_2=None,
        market_context=StrategyMarketContext(),
        contract_constraints=StrategyContractConstraints(),
    )
    report = replay_strat_212([row])
    result = report.results[0]
    assert result.status == "WATCH"
    assert result.replay_outcome == "NOT_TRIGGERED"
    assert result.reason_code == "setup_forming_not_triggered"


# --- 10. valid strategy with no future path returns NO_OUTCOME_DATA ------------------------


def test_valid_strategy_with_no_future_path_returns_no_outcome_data():
    report = replay_strat_212([_valid_call_row()])
    result = report.results[0]
    assert result.status == "VALID"
    assert result.replay_outcome == "NO_OUTCOME_DATA"


# --- 11. aggregate metrics count outcomes correctly -----------------------------------------


def test_aggregate_metrics_count_outcomes_correctly():
    rows = [
        _valid_call_row(future_high=104.0, future_low=98.0),  # TARGET_1_HIT
        _valid_call_row(future_high=107.0, future_low=98.0),  # TARGET_2_HIT
        _valid_call_row(future_high=100.0, future_low=94.0),  # STOP_HIT
        _valid_call_row(underlying_invalidation=None),  # INVALID
        _valid_call_row(),  # NO_OUTCOME_DATA
    ]
    report = replay_strat_212(rows)
    assert report.total_rows == 5
    assert report.valid_setups == 4
    assert report.invalid_setups == 1
    assert report.watch_setups == 0
    assert report.target_1_hits == 1
    assert report.target_2_hits == 1
    assert report.stop_hits == 1
    assert report.no_outcome_data == 1
    assert report.win_rate_target_1 == 2 / 3


def test_win_rate_is_none_when_no_resolved_trades():
    report = replay_strat_212([_valid_call_row()])
    assert report.win_rate_target_1 is None


# --- 12. rejection_counts_by_reason works ---------------------------------------------------


def test_rejection_counts_by_reason_works():
    rows = [
        _valid_call_row(underlying_invalidation=None),  # missing_invalidation
        _valid_call_row(underlying_invalidation=None),  # missing_invalidation
        _valid_call_row(target_1=None, target_2=None),  # missing_target_1
    ]
    report = replay_strat_212(rows)
    assert report.rejection_counts_by_reason == {
        "missing_invalidation": 2,
        "missing_target_1": 1,
    }


# --- 13. target/context/contract fields are preserved in replay output ---------------------


def test_derived_target_context_and_contract_fields_are_preserved_in_replay_output():
    row = Strat212ReplayRow(
        ticker="SPY",
        timestamp="2026-01-02T10:00:00Z",
        direction="CALL",
        bars=_bullish_bars(),
        entry_trigger=100.0,
        underlying_invalidation=97.0,
        level_inputs=LevelFinderInputs(
            direction="CALL",
            entry=100.0,
            underlying_invalidation=97.0,
            resistance_levels=(103.0, 108.0),
            gamma_resistance=105.0,
        ),
        market_context=StrategyMarketContext(),
        market_context_inputs=MarketContextInputs(
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
        ),
        contract_constraints=StrategyContractConstraints(),
        contract_constraints_inputs=ContractConstraintsInputs(
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
        ),
        future_high=104.0,
        future_low=98.0,
    )
    report = replay_strat_212([row])
    result = report.results[0]
    assert result.status == "VALID"
    assert result.target_1 == 103.0
    assert result.target_2 == 105.0
    assert result.context_status == "VALID"
    assert result.contract_status == "VALID"
    assert result.replay_outcome == "TARGET_1_HIT"


# --- structural safety (matches this buildout's established pattern) ----------------------


def _imported_modules(module) -> list[str]:
    """Absolute module names only; relative imports (level > 0) resolve
    within the same package and are excluded rather than misreported as a
    cross-boundary import (see the Increment 1/2/3/4 fix for the same
    issue)."""
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def test_replay_modules_do_not_import_replay_engine_or_candle_loader():
    for module in _SCANNED_REPLAY_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            assert name != "replay" and not name.startswith("replay."), (
                f"{module.__name__} must not import replay.* (replay_engine.py has "
                f"execution/broker/journal imports; candle_loader.py is not needed "
                f"since rows already carry their own bars): {name}"
            )


def test_replay_modules_have_no_forbidden_imports():
    for module in _SCANNED_REPLAY_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in name, f"{module.__name__} must not import {name!r}"


def test_replay_modules_have_no_cross_boundary_imports_at_all():
    for module in _SCANNED_REPLAY_MODULES:
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


def test_replay_modules_do_not_import_live_context_loader():
    for module in _SCANNED_REPLAY_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            assert name != "context" and not name.startswith("context."), (
                f"{module.__name__} must not import the live context.* loader: {name}"
            )


def test_replay_modules_have_no_order_action_verbs():
    for module in _SCANNED_REPLAY_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in _FORBIDDEN_ORDER_ACTION_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_replay_modules_do_not_mutate_live_options_flag():
    for module in _SCANNED_REPLAY_MODULES:
        source = Path(module.__file__).read_text()
        assert "LIVE_OPTIONS_TRADING_ENABLED" not in source


def test_replay_modules_do_not_modify_strat_212_source():
    source = Path(strat_212_replay_module.__file__).read_text()
    assert "def evaluate_strat_212" not in source
