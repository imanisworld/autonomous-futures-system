"""
tests/test_options_adapter_row_builder.py

Increment 13 — options_manager/adapters/row_builder.py tests. Proves the
pure, source-neutral row builder correctly translates already-normalized
adapter data into a WatchlistRow without ever fetching anything, without
inferring entry/invalidation from candles, without inventing missing
optional fields, and without running the scanner or the strategy itself.
"""

from __future__ import annotations

import ast
from pathlib import Path

import options_manager.adapters.base as adapters_base_module
import options_manager.adapters.row_builder as row_builder_module
import options_manager.scanner.base as scanner_base_module
import options_manager.scanner.report as scanner_report_module
import options_manager.scanner.strat_212_scanner as scanner_module
from options_manager.adapters import (
    AdapterCandle,
    AdapterMarketContextSnapshot,
    AdapterOptionQuote,
    AdapterUnderlyingSnapshot,
    build_watchlist_row_from_adapter_data,
)
from options_manager.scanner import scan_watchlist_strat_212
from options_manager.strategies import StrategyContractConstraints, StrategyMarketContext

_SCANNED_ADAPTER_MODULES = (adapters_base_module, row_builder_module)
_SCANNED_SCANNER_MODULES_FOR_BOUNDARY_CHECK = (
    scanner_base_module,
    scanner_module,
    scanner_report_module,
)

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


def _bullish_candles():
    two_bars_back = AdapterCandle(timestamp="t0", open=97.0, high=100.0, low=95.0, close=99.0)
    previous = AdapterCandle(timestamp="t1", open=97.0, high=99.0, low=96.0, close=98.5)
    current = AdapterCandle(timestamp="t2", open=98.0, high=101.0, low=96.5, close=100.5)
    return two_bars_back, previous, current


def _build_row(**overrides):
    two_bars_back, previous, current = _bullish_candles()
    fields = dict(
        ticker="SPY",
        timestamp="2026-01-02T10:00:00Z",
        direction="CALL",
        two_bars_back_type="two_up",
        two_bars_back_candle=two_bars_back,
        previous_candle=previous,
        current_candle=current,
        entry_trigger=99.0,
        underlying_invalidation=95.5,
        target_1=103.0,
        target_2=106.0,
    )
    fields.update(overrides)
    return build_watchlist_row_from_adapter_data(**fields)


# --- 1. valid adapter data builds a WatchlistRow ------------------------------------------------


def test_valid_adapter_data_builds_a_watchlist_row():
    row = _build_row()
    assert row.ticker == "SPY"
    assert row.direction == "CALL"
    assert row.entry_trigger == 99.0
    assert row.underlying_invalidation == 95.5
    assert row.target_1 == 103.0
    assert row.target_2 == 106.0


def test_built_row_scans_to_triggered():
    row = _build_row(
        market_context=StrategyMarketContext(confirmed=True),
        contract_constraints=StrategyContractConstraints(constraints_met=True),
    )
    report = scan_watchlist_strat_212([row])
    assert report.results[0].scan_status == "TRIGGERED"


# --- 2. candles map correctly into Strat212Bars -------------------------------------------------


def test_candles_map_correctly_into_strat212_bars():
    row = _build_row()
    assert row.bars.two_bars_back_type == "two_up"
    assert row.bars.two_bars_back_high == 100.0
    assert row.bars.two_bars_back_low == 95.0
    assert row.bars.previous_high == 99.0
    assert row.bars.previous_low == 96.0
    assert row.bars.current_high == 101.0
    assert row.bars.current_low == 96.5


# --- 3. option quote maps correctly into ContractConstraintsInputs ------------------------------


def test_option_quote_maps_correctly_into_contract_constraints_inputs():
    quote = AdapterOptionQuote(
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
        earnings_risk="NONE",
        event_risk="NONE",
    )
    row = _build_row(
        contract_constraints=StrategyContractConstraints(),
        option_quote=quote,
        max_premium=5.0,
        max_spread_percent=0.10,
        min_volume=100,
        min_open_interest=200,
        min_dte=7,
        max_theta_abs=0.10,
    )
    inputs = row.contract_constraints_inputs
    assert inputs is not None
    assert inputs.ticker == "SPY"
    assert inputs.direction == "CALL"
    assert inputs.expiration == "2026-08-01"
    assert inputs.dte == 30
    assert inputs.strike == 505.0
    assert inputs.premium == 2.50
    assert inputs.bid == 2.45
    assert inputs.ask == 2.55
    assert inputs.spread_percent == 0.04
    assert inputs.volume == 500
    assert inputs.open_interest == 1000
    assert inputs.delta == 0.35
    assert inputs.theta == -0.05
    assert inputs.iv == 0.25
    assert inputs.max_premium == 5.0
    assert inputs.max_spread_percent == 0.10
    assert inputs.min_volume == 100
    assert inputs.min_open_interest == 200
    assert inputs.min_dte == 7
    assert inputs.max_theta_abs == 0.10
    assert inputs.earnings_risk == "NONE"
    assert inputs.event_risk == "NONE"


# --- 4. level data maps correctly into LevelFinderInputs ----------------------------------------


def test_level_data_maps_correctly_into_level_finder_inputs():
    snapshot = AdapterUnderlyingSnapshot(
        spot_price=100.0,
        resistance_levels=(103.0, 108.0),
        support_levels=(95.0,),
        gamma_resistance=105.0,
        gamma_support=94.0,
    )
    row = _build_row(
        entry_trigger=100.0,
        underlying_invalidation=97.0,
        target_1=None,
        target_2=None,
        underlying_snapshot=snapshot,
        min_rr_threshold=1.5,
        min_distance_to_target=1.0,
    )
    level_inputs = row.level_inputs
    assert level_inputs is not None
    assert level_inputs.direction == "CALL"
    assert level_inputs.entry == 100.0
    assert level_inputs.underlying_invalidation == 97.0
    assert level_inputs.resistance_levels == (103.0, 108.0)
    assert level_inputs.support_levels == (95.0,)
    assert level_inputs.gamma_resistance == 105.0
    assert level_inputs.gamma_support == 94.0
    assert level_inputs.min_rr_threshold == 1.5
    assert level_inputs.min_distance_to_target == 1.0


# --- 5. market context maps correctly into MarketContextInputs ---------------------------------


def test_market_context_maps_correctly_into_market_context_inputs():
    snapshot = AdapterMarketContextSnapshot(
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
    row = _build_row(
        market_context=StrategyMarketContext(),
        market_context_snapshot=snapshot,
        underlying_snapshot=AdapterUnderlyingSnapshot(spot_price=500.0),
        min_distance_to_gamma_level=2.0,
    )
    inputs = row.market_context_inputs
    assert inputs is not None
    assert inputs.direction == "CALL"
    assert inputs.ticker == "SPY"
    assert inputs.underlying_price == 500.0
    assert inputs.spy_trend == "bullish"
    assert inputs.qqq_trend == "bullish"
    assert inputs.spy_above_flip is True
    assert inputs.qqq_above_flip is True
    assert inputs.gex_regime == "positive"
    assert inputs.price_above_gex_flip is True
    assert inputs.signa_direction == "bullish"
    assert inputs.signa_grade == "A"
    assert inputs.signa_score == 80.0
    assert inputs.higher_timeframe_alignment == "aligned"
    assert inputs.gap_direction == "none"
    assert inputs.distance_to_gamma_resistance == 5.0
    assert inputs.distance_to_gamma_support == 5.0
    assert inputs.event_risk == "none"
    assert inputs.min_distance_to_gamma_level == 2.0


def test_market_context_inputs_underlying_price_is_none_without_snapshot():
    snapshot = AdapterMarketContextSnapshot(event_risk="none")
    row = _build_row(market_context_snapshot=snapshot, underlying_snapshot=None)
    assert row.market_context_inputs.underlying_price is None


# --- 6. explicit targets/context/contracts still pass through ----------------------------------


def test_explicit_targets_context_and_contracts_still_pass_through():
    row = _build_row(
        market_context=StrategyMarketContext(confirmed=True),
        contract_constraints=StrategyContractConstraints(constraints_met=True),
    )
    assert row.target_1 == 103.0
    assert row.target_2 == 106.0
    assert row.market_context.confirmed is True
    assert row.contract_constraints.constraints_met is True
    assert row.level_inputs is None
    assert row.market_context_inputs is None
    assert row.contract_constraints_inputs is None


# --- 7. missing optional market/contract fields do not get invented ----------------------------


def test_missing_optional_fields_are_not_invented():
    row = _build_row()
    assert row.level_inputs is None
    assert row.market_context_inputs is None
    assert row.contract_constraints_inputs is None
    # Default (unresolved) placeholders, not a fabricated favorable value.
    assert row.market_context.confirmed is None
    assert row.contract_constraints.constraints_met is None


def test_partial_market_context_snapshot_is_not_backfilled():
    snapshot = AdapterMarketContextSnapshot(spy_trend="bullish")
    row = _build_row(market_context_snapshot=snapshot)
    inputs = row.market_context_inputs
    assert inputs.spy_trend == "bullish"
    assert inputs.qqq_trend is None
    assert inputs.event_risk is None


# --- 8. row builder does not scan ---------------------------------------------------------------


def test_row_builder_does_not_scan():
    source = Path(row_builder_module.__file__).read_text()
    assert "scan_watchlist_strat_212(" not in source


# --- 9. row builder does not evaluate strategy --------------------------------------------------


def test_row_builder_does_not_evaluate_strategy():
    source = Path(row_builder_module.__file__).read_text()
    assert "evaluate_strat_212(" not in source
    assert "def evaluate_strat_212" not in source


# --- structural safety (matches this buildout's established pattern) ---------------------------


def _imported_modules(module) -> list[str]:
    """Absolute module names only; relative imports (level > 0) resolve
    within the same package and are excluded rather than misreported as a
    cross-boundary import (see the Increment 1-12 fix for the same
    issue)."""
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


# --- 10. scanner does not import adapters --------------------------------------------------------


def test_scanner_modules_do_not_import_adapters():
    for module in _SCANNED_SCANNER_MODULES_FOR_BOUNDARY_CHECK:
        imported = _imported_modules(module)
        for name in imported:
            assert not name.startswith("options_manager.adapters"), (
                f"{module.__name__} must not import options_manager.adapters: {name}"
            )


# --- 11. adapters/base.py does not import scanner; row_builder may import only WatchlistRow ------


def test_adapters_base_does_not_import_scanner():
    imported = _imported_modules(adapters_base_module)
    for name in imported:
        assert not name.startswith("options_manager.scanner"), (
            f"adapters/base.py must not import options_manager.scanner: {name}"
        )


def test_row_builder_only_imports_watchlist_row_from_scanner():
    tree = ast.parse(Path(row_builder_module.__file__).read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "options_manager.scanner"
            and node.level == 0
        ):
            imported_names = {alias.name for alias in node.names}
            assert imported_names == {"WatchlistRow"}, (
                f"row_builder.py may only import WatchlistRow from options_manager.scanner, "
                f"found: {imported_names}"
            )


# --- 12/13/14/15/16/17. forbidden imports / network / credentials / order verbs / live flag / files ---


def test_adapter_modules_have_no_forbidden_imports():
    for module in _SCANNED_ADAPTER_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in name, f"{module.__name__} must not import {name!r}"


def test_adapter_modules_have_no_cross_boundary_imports_outside_options_manager():
    for module in _SCANNED_ADAPTER_MODULES:
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


def test_adapter_modules_have_no_credential_identifiers():
    for module in _SCANNED_ADAPTER_MODULES:
        source = Path(module.__file__).read_text().lower()
        for forbidden in _FORBIDDEN_CREDENTIAL_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_adapter_modules_have_no_order_action_verbs():
    for module in _SCANNED_ADAPTER_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in _FORBIDDEN_ORDER_ACTION_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_adapter_modules_do_not_mutate_live_options_flag():
    for module in _SCANNED_ADAPTER_MODULES:
        source = Path(module.__file__).read_text()
        assert "LIVE_OPTIONS_TRADING_ENABLED" not in source


def test_adapter_modules_do_not_write_files():
    for module in _SCANNED_ADAPTER_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in ("open(", ".write(", ".write_text(", ".write_bytes("):
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"
