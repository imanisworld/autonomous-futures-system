"""
tests/test_options_robinhood_readonly.py

options_manager/adapters/robinhood_readonly.py tests. Proves the new
read-only Robinhood adapter maps already-obtained mock data into the
existing AdapterOptionQuote/AdapterUnderlyingSnapshot shapes (plus two
local, account-number-free shapes), leaves any missing field as None
rather than fabricating it, never imports alert_ranker, never mutates
the live-options flag, and exposes no method whose name touches any
broker-instruction verb.
"""

from __future__ import annotations

import ast
from pathlib import Path

import options_manager.adapters.robinhood_readonly as rh_readonly_module
from options_manager.adapters.robinhood_readonly import (
    RobinhoodAccountSummary,
    RobinhoodPosition,
    get_accounts_summary,
    get_option_chain,
    get_option_quote,
    get_portfolio_positions,
    get_underlying_quote,
)
from options_manager.adapters.base import AdapterOptionQuote, AdapterUnderlyingSnapshot
from options_manager.contracts import ContractConstraintsInputs, evaluate_contract_constraints

_SCANNED_MODULES = (rh_readonly_module,)

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

# Every one of these must be absent anywhere in the module's source text,
# not just as identifiers -- matching the strict, no-exceptions boundary
# requested for this adapter (even a review-shaped identifier is blocked).
_FORBIDDEN_EXECUTION_SUBSTRINGS = (
    "place",
    "submit",
    "order",
    "cancel",
    "replace",
    "execute",
    "trade",
    "buy",
    "sell",
)

_FORBIDDEN_CREDENTIAL_IDENTIFIERS = (
    "api_key",
    "apikey",
    "credential",
    "secret",
    "password",
    "token",
)

_ALLOWED_PUBLIC_FUNCTION_NAMES = {
    "get_accounts_summary",
    "get_portfolio_positions",
    "get_option_chain",
    "get_option_quote",
    "get_underlying_quote",
}


def _module_source() -> str:
    return Path(rh_readonly_module.__file__).read_text()


def _imported_modules(module) -> list[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def _top_level_function_names(module) -> list[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    return [
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    ]


# --- 1. AST test: no import of alert_ranker -------------------------------------------------------


def test_adapter_does_not_import_alert_ranker():
    imported = _imported_modules(rh_readonly_module)
    assert not any("alert_ranker" in name for name in imported)


def test_adapter_has_no_forbidden_import_fragments():
    imported = _imported_modules(rh_readonly_module)
    for name in imported:
        for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
            assert forbidden not in name, f"must not import {name!r}"


def test_adapter_has_no_cross_boundary_imports_outside_options_manager():
    imported = _imported_modules(rh_readonly_module)
    outside = [
        name
        for name in imported
        if not name.startswith("options_manager")
        and name not in ("__future__", "dataclasses", "typing")
    ]
    assert not outside, f"unexpected import: {outside}"


# --- 2. AST/string safety test: no forbidden execution identifiers anywhere ------------------------


def test_adapter_source_contains_no_forbidden_execution_substrings():
    source = _module_source().lower()
    for forbidden in _FORBIDDEN_EXECUTION_SUBSTRINGS:
        assert forbidden not in source, f"module source must not contain {forbidden!r}"


def test_adapter_has_no_credential_identifiers():
    source = _module_source().lower()
    for forbidden in _FORBIDDEN_CREDENTIAL_IDENTIFIERS:
        assert forbidden not in source, f"module must not contain {forbidden!r}"


# --- 3. mocked Robinhood quote data maps into AdapterOptionQuote -----------------------------------


def test_mocked_quote_maps_into_adapter_option_quote():
    raw_quote = {
        "expiration_date": "2026-08-21",
        "dte": 30,
        "strike_price": "60.0",
        "mark_price": "2.15",
        "bid_price": "2.10",
        "ask_price": "2.20",
        "volume": "500",
        "open_interest": "1000",
        "delta": "0.35",
        "theta": "-0.05",
        "implied_volatility": "0.28",
    }
    quote = get_option_quote(raw_quote)
    assert isinstance(quote, AdapterOptionQuote)
    assert quote.expiration == "2026-08-21"
    assert quote.dte == 30
    assert quote.strike == 60.0
    assert quote.premium == 2.15
    assert quote.bid == 2.10
    assert quote.ask == 2.20
    assert quote.volume == 500
    assert quote.open_interest == 1000
    assert quote.delta == 0.35
    assert quote.theta == -0.05
    assert quote.iv == 0.28
    assert quote.spread_percent is not None


def test_mocked_chain_maps_each_row_into_adapter_option_quote():
    raw_chain = [
        {"strike_price": "60.0", "bid_price": "2.0", "ask_price": "2.1"},
        {"strike_price": "65.0", "bid_price": "1.0", "ask_price": "1.1"},
    ]
    quotes = get_option_chain(raw_chain)
    assert len(quotes) == 2
    assert all(isinstance(q, AdapterOptionQuote) for q in quotes)
    assert quotes[0].strike == 60.0
    assert quotes[1].strike == 65.0


def test_mocked_underlying_quote_maps_into_adapter_underlying_snapshot():
    snapshot = get_underlying_quote({"mark_price": "60.11"})
    assert isinstance(snapshot, AdapterUnderlyingSnapshot)
    assert snapshot.spot_price == 60.11
    assert snapshot.resistance_levels == ()
    assert snapshot.support_levels == ()


def test_mocked_account_and_positions_map_into_local_models():
    summary = get_accounts_summary(
        {"cash_available": "1000.0", "cash": "500.0", "portfolio_value": "5000.0", "equity": "5000.0"}
    )
    assert isinstance(summary, RobinhoodAccountSummary)
    assert summary.cash_available == 1000.0

    positions = get_portfolio_positions(
        [{"symbol": "BAC", "quantity": "10", "average_cost_basis": "59.5", "current_price": "60.1"}]
    )
    assert len(positions) == 1
    assert isinstance(positions[0], RobinhoodPosition)
    assert positions[0].ticker == "BAC"


# --- 4. missing bid/ask/volume/OI maps to None -----------------------------------------------------


def test_missing_quote_fields_map_to_none_not_fabricated():
    quote = get_option_quote({"strike_price": "60.0"})
    assert quote.bid is None
    assert quote.ask is None
    assert quote.volume is None
    assert quote.open_interest is None
    assert quote.delta is None
    assert quote.theta is None
    assert quote.iv is None
    assert quote.spread_percent is None
    assert quote.earnings_risk is None
    assert quote.event_risk is None


def test_missing_underlying_price_maps_to_none():
    snapshot = get_underlying_quote({})
    assert snapshot.spot_price is None


def test_missing_account_fields_map_to_none():
    summary = get_accounts_summary({})
    assert summary.cash_available is None
    assert summary.cash is None
    assert summary.portfolio_value is None
    assert summary.equity is None


def test_malformed_chain_row_is_skipped_not_fabricated():
    raw_chain = [
        {"strike_price": "not_a_number", "bid_price": "not_a_number"},
    ]
    # strike/bid become None (can't be parsed as float) rather than raising --
    # the row is still mapped, just with those fields left None.
    quotes = get_option_chain(raw_chain)
    assert len(quotes) == 1
    assert quotes[0].strike is None
    assert quotes[0].bid is None


# --- 5. missing required contract fields cause contract validation INVALID ------------------------


def test_quote_with_missing_fields_fails_closed_in_contract_validator():
    raw_quote = {
        "expiration_date": "2026-08-21",
        "dte": 30,
        "strike_price": "60.0",
        "mark_price": "2.15",
        "bid_price": "2.10",
        "ask_price": "2.20",
        # volume/open_interest/delta/theta/iv intentionally omitted
    }
    quote = get_option_quote(raw_quote)
    inputs = ContractConstraintsInputs(
        direction="CALL",
        ticker="BAC",
        expiration=quote.expiration,
        dte=quote.dte,
        strike=quote.strike,
        premium=quote.premium,
        bid=quote.bid,
        ask=quote.ask,
        spread_percent=quote.spread_percent,
        volume=quote.volume,
        open_interest=quote.open_interest,
        delta=quote.delta,
        theta=quote.theta,
        iv=quote.iv,
        max_premium=5.0,
        max_spread_percent=0.10,
        min_volume=100,
        min_open_interest=200,
        min_dte=7,
    )
    result = evaluate_contract_constraints(inputs)
    assert result.status == "INVALID"
    assert result.reason_code == "missing_volume"


# --- 6. adapter exposes only the 5 allowed public functions ----------------------------------------


def test_adapter_public_surface_is_exactly_the_five_allowed_functions():
    names = set(_top_level_function_names(rh_readonly_module))
    assert names == _ALLOWED_PUBLIC_FUNCTION_NAMES


def test_no_function_name_contains_a_forbidden_execution_verb():
    names = _top_level_function_names(rh_readonly_module)
    for name in names:
        for forbidden in _FORBIDDEN_EXECUTION_SUBSTRINGS:
            assert forbidden not in name, f"function name {name!r} must not contain {forbidden!r}"


# --- 7. no environment variable or live-trading flag is changed ------------------------------------


def test_adapter_never_reads_or_mutates_live_options_flag():
    source = _module_source()
    assert "LIVE_OPTIONS_TRADING_ENABLED" not in source


def test_adapter_never_touches_environment_variables():
    source = _module_source()
    assert "os.environ" not in source
    assert "os.getenv" not in source
    assert "import os" not in source


def test_adapter_performs_no_io_or_network_calls():
    source = _module_source()
    for forbidden in ("open(", ".write(", ".write_text(", "httpx.", "requests.", "socket."):
        assert forbidden not in source, f"module must not contain {forbidden!r}"
