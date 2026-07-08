"""
tests/test_options_strat_212_scanner.py

Increment 9 — options_manager/scanner/strat_212_scanner.py tests. Proves
the advisory-only 2-1-2 watchlist scanner is a pure, caller-supplied-only
consumer of evaluate_strat_212(): it never calls replay_strat_212(),
never fetches anything, and correctly maps every strategy outcome into
the scanner's own TRIGGERED/WATCH/INVALID/NO_TRADE vocabulary --
including the NO_TRADE-vs-INVALID distinction (no actionable setup vs.
a rejected/unsafe/incomplete one).
"""

from __future__ import annotations

import ast
from pathlib import Path

import options_manager.scanner.base as scanner_base_module
import options_manager.scanner.strat_212_scanner as scanner_module
from options_manager.context import MarketContextInputs
from options_manager.contracts import ContractConstraintsInputs
from options_manager.levels import LevelFinderInputs
from options_manager.scanner import ScanReport, WatchlistRow, scan_watchlist_strat_212
from options_manager.strategies import (
    Strat212Bars,
    StrategyContractConstraints,
    StrategyMarketContext,
)

_SCANNED_SCANNER_MODULES = (scanner_base_module, scanner_module)

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

_FORBIDDEN_QUOTE_FETCH_IDENTIFIERS = (
    "get_quote",
    "fetch_quote",
    "get_price",
    "fetch_price",
    "price_snapshot",
    "market_data",
)

_FORBIDDEN_ALERT_IDENTIFIERS = (
    "discord",
    "send_alert",
    "send_email",
    "smtp",
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


def _bad_sequence_bars() -> Strat212Bars:
    return Strat212Bars(
        two_bars_back_type="two_up",
        two_bars_back_high=100.0,
        two_bars_back_low=95.0,
        previous_high=104.0,
        previous_low=99.0,
        current_high=108.0,
        current_low=103.0,
    )


def _valid_call_row(**overrides) -> WatchlistRow:
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
    return WatchlistRow(**fields)


def _valid_put_row(**overrides) -> WatchlistRow:
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
    return WatchlistRow(**fields)


def _aligned_market_context_inputs(**overrides) -> MarketContextInputs:
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


def _valid_contract_constraints_inputs(**overrides) -> ContractConstraintsInputs:
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


# --- 1/2. valid CALL/PUT row maps to TRIGGERED -----------------------------------------------


def test_valid_call_row_maps_to_triggered():
    report = scan_watchlist_strat_212([_valid_call_row()])
    result = report.results[0]
    assert result.scan_status == "TRIGGERED"
    assert result.strategy_status == "VALID"


def test_valid_put_row_maps_to_triggered():
    report = scan_watchlist_strat_212([_valid_put_row()])
    result = report.results[0]
    assert result.scan_status == "TRIGGERED"
    assert result.strategy_status == "VALID"


# --- 3. forming 2-1-2 row maps to WATCH ---------------------------------------------------------


def test_forming_row_maps_to_watch():
    row = WatchlistRow(
        ticker="SPY",
        timestamp="2026-01-02T10:00:00Z",
        direction="CALL",
        bars=_forming_bars(),
        market_context=StrategyMarketContext(),
        contract_constraints=StrategyContractConstraints(),
    )
    report = scan_watchlist_strat_212([row])
    result = report.results[0]
    assert result.scan_status == "WATCH"
    assert result.strategy_status == "WATCH"
    assert result.reason_code == "setup_forming_not_triggered"


# --- 4. non-2-1-2 complete row maps to NO_TRADE --------------------------------------------------


def test_non_212_complete_row_maps_to_no_trade():
    row = _valid_call_row(bars=_bad_sequence_bars())
    report = scan_watchlist_strat_212([row])
    result = report.results[0]
    assert result.strategy_status == "INVALID"
    assert result.reason_code == "sequence_not_212"
    assert result.scan_status == "NO_TRADE"


def test_direction_mismatch_row_maps_to_no_trade():
    # Bearish bars form a real strat_212 SHORT continuation -- requesting
    # CALL against them is "no setup in the requested direction," not a
    # data/safety failure, so this should be NO_TRADE, not INVALID.
    row = _valid_call_row(direction="CALL", bars=_bearish_bars())
    report = scan_watchlist_strat_212([row])
    result = report.results[0]
    assert result.strategy_status == "INVALID"
    assert result.reason_code == "direction_mismatch"
    assert result.scan_status == "NO_TRADE"


def test_excluded_row_maps_to_no_trade_without_evaluating_strategy():
    row = _valid_call_row(exclude=True)
    report = scan_watchlist_strat_212([row])
    result = report.results[0]
    assert result.scan_status == "NO_TRADE"
    assert result.strategy_status is None
    assert result.reason_code == "excluded"
    assert result.signal is None


# --- 5. missing entry maps to INVALID -------------------------------------------------------------


def test_missing_entry_trigger_maps_to_invalid():
    row = _valid_call_row(entry_trigger=None)
    report = scan_watchlist_strat_212([row])
    result = report.results[0]
    assert result.scan_status == "INVALID"
    assert result.reason_code == "missing_entry_trigger"


# --- 6. bad target/R:R maps to INVALID -------------------------------------------------------------


def test_poor_rr_maps_to_invalid():
    row = WatchlistRow(
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
            min_rr_threshold=2.0,
        ),
        market_context=StrategyMarketContext(confirmed=True),
        contract_constraints=StrategyContractConstraints(constraints_met=True),
    )
    report = scan_watchlist_strat_212([row])
    result = report.results[0]
    assert result.scan_status == "INVALID"
    assert result.reason_code == "target_finder_rr_below_threshold"


# --- 7. invalid market context maps to INVALID -----------------------------------------------------


def test_invalid_market_context_maps_to_invalid():
    row = _valid_call_row(
        market_context=StrategyMarketContext(),
        market_context_inputs=_aligned_market_context_inputs(event_risk="high"),
    )
    report = scan_watchlist_strat_212([row])
    result = report.results[0]
    assert result.scan_status == "INVALID"
    assert result.reason_code == "market_context_event_risk_high"


# --- 8. invalid contract constraints maps to INVALID -------------------------------------------------


def test_invalid_contract_constraints_maps_to_invalid():
    row = _valid_call_row(
        contract_constraints=StrategyContractConstraints(),
        contract_constraints_inputs=_valid_contract_constraints_inputs(spread_percent=0.50),
    )
    report = scan_watchlist_strat_212([row])
    result = report.results[0]
    assert result.scan_status == "INVALID"
    assert result.reason_code == "contract_constraints_spread_too_wide"


# --- 9. derived levels/context/contracts compose in one scanner row ------------------------------------


def test_derived_levels_context_and_contracts_compose_in_one_row():
    row = WatchlistRow(
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
        market_context_inputs=_aligned_market_context_inputs(),
        contract_constraints=StrategyContractConstraints(),
        contract_constraints_inputs=_valid_contract_constraints_inputs(),
    )
    report = scan_watchlist_strat_212([row])
    result = report.results[0]
    assert result.scan_status == "TRIGGERED"
    assert result.target_1 == 103.0
    assert result.target_2 == 105.0
    assert result.context_status == "VALID"
    assert result.contract_status == "VALID"


# --- 10. explicit targets/context/contracts still work -------------------------------------------------


def test_explicit_targets_context_and_contracts_still_work():
    report = scan_watchlist_strat_212([_valid_call_row()])
    result = report.results[0]
    assert result.scan_status == "TRIGGERED"
    assert result.target_1 == 103.0
    assert result.target_2 == 106.0
    # Explicit market_context/contract_constraints skip derivation, so
    # context_status/contract_status stay None on the scan result.
    assert result.context_status is None
    assert result.contract_status is None


# --- 11/12. counts_by_status / counts_by_reason are correct ---------------------------------------------


def test_counts_by_status_are_correct():
    rows = [
        _valid_call_row(ticker="A"),
        _valid_put_row(ticker="B"),
        WatchlistRow(
            ticker="C",
            timestamp="2026-01-02T10:00:00Z",
            direction="CALL",
            bars=_forming_bars(),
            market_context=StrategyMarketContext(),
            contract_constraints=StrategyContractConstraints(),
        ),
        _valid_call_row(ticker="D", bars=_bad_sequence_bars()),
        _valid_call_row(ticker="E", entry_trigger=None),
    ]
    report = scan_watchlist_strat_212(rows)
    assert report.total_rows == 5
    assert report.triggered == 2
    assert report.watch == 1
    assert report.no_trade == 1
    assert report.invalid == 1
    assert report.counts_by_status == {
        "TRIGGERED": 2,
        "WATCH": 1,
        "INVALID": 1,
        "NO_TRADE": 1,
    }


def test_counts_by_reason_are_correct():
    rows = [
        _valid_call_row(ticker="A", entry_trigger=None),
        _valid_call_row(ticker="B", entry_trigger=None),
        _valid_call_row(ticker="C", underlying_invalidation=None),
    ]
    report = scan_watchlist_strat_212(rows)
    assert report.counts_by_reason == {
        "missing_entry_trigger": 2,
        "missing_invalidation": 1,
    }


# --- 13. empty watchlist returns deterministic empty ScanReport -----------------------------------------


def test_empty_watchlist_returns_deterministic_empty_report():
    report = scan_watchlist_strat_212([])
    assert isinstance(report, ScanReport)
    assert report.total_rows == 0
    assert report.triggered == 0
    assert report.watch == 0
    assert report.invalid == 0
    assert report.no_trade == 0
    assert report.results == []
    assert report.counts_by_status == {"TRIGGERED": 0, "WATCH": 0, "INVALID": 0, "NO_TRADE": 0}
    assert report.counts_by_reason == {}


# --- structural safety (matches this buildout's established pattern) -------------------------------------


def _imported_modules(module) -> list[str]:
    """Absolute module names only; relative imports (level > 0) resolve
    within the same package and are excluded rather than misreported as a
    cross-boundary import (see the Increment 1-8 fix for the same
    issue)."""
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


# --- 14. scanner does not call replay_strat_212() ---------------------------------------------------------


def test_scanner_does_not_call_replay_strat_212():
    for module in _SCANNED_SCANNER_MODULES:
        source = Path(module.__file__).read_text()
        assert "replay_strat_212(" not in source, (
            f"{module.__name__} must call evaluate_strat_212() directly, not "
            f"options_manager.replay.replay_strat_212()"
        )


# --- 15. scanner does not import replay/replay_engine.py -----------------------------------------------------


def test_scanner_modules_do_not_import_replay_engine_or_replay_package():
    for module in _SCANNED_SCANNER_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            assert name != "replay" and not name.startswith("replay."), (
                f"{module.__name__} must not import replay.* directly: {name}"
            )
            assert not name.startswith("options_manager.replay"), (
                f"{module.__name__} must not import options_manager.replay: {name}"
            )


# --- 16. scanner does not import forbidden modules / network / live context -----------------------------------


def test_scanner_modules_have_no_forbidden_imports():
    for module in _SCANNED_SCANNER_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in name, f"{module.__name__} must not import {name!r}"


def test_scanner_modules_have_no_cross_boundary_imports_at_all():
    for module in _SCANNED_SCANNER_MODULES:
        imported = _imported_modules(module)
        outside_options_manager = [
            name
            for name in imported
            if not name.startswith("options_manager")
            and name not in ("__future__", "dataclasses", "typing", "collections")
        ]
        assert not outside_options_manager, (
            f"{module.__name__} has an unexpected cross-boundary import: "
            f"{outside_options_manager}"
        )


def test_scanner_modules_do_not_import_live_context_loader():
    for module in _SCANNED_SCANNER_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            assert name != "context" and not name.startswith("context."), (
                f"{module.__name__} must not import the live context.* loader: {name}"
            )


# --- 17. scanner source contains no quote-fetch identifiers ---------------------------------------------------


def test_scanner_modules_have_no_quote_fetch_identifiers():
    for module in _SCANNED_SCANNER_MODULES:
        source = Path(module.__file__).read_text().lower()
        for forbidden in _FORBIDDEN_QUOTE_FETCH_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


# --- 18. scanner source contains no order/execution verbs -----------------------------------------------------


def test_scanner_modules_have_no_order_action_verbs():
    for module in _SCANNED_SCANNER_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in _FORBIDDEN_ORDER_ACTION_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


# --- 19. scanner source does not mutate LIVE_OPTIONS_TRADING_ENABLED ---------------------------------------------


def test_scanner_modules_do_not_mutate_live_options_flag():
    for module in _SCANNED_SCANNER_MODULES:
        source = Path(module.__file__).read_text()
        assert "LIVE_OPTIONS_TRADING_ENABLED" not in source


# --- 20. scanner source contains no file write calls ------------------------------------------------------------


def test_scanner_modules_do_not_write_files():
    for module in _SCANNED_SCANNER_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in ("open(", ".write(", ".write_text(", ".write_bytes("):
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


# --- 21. scanner source contains no alert/Discord/email send calls ------------------------------------------------


def test_scanner_modules_have_no_alert_send_identifiers():
    for module in _SCANNED_SCANNER_MODULES:
        source = Path(module.__file__).read_text().lower()
        for forbidden in _FORBIDDEN_ALERT_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_scanner_modules_do_not_modify_strat_212_source():
    source = Path(scanner_module.__file__).read_text()
    assert "def evaluate_strat_212" not in source
