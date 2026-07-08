"""
tests/test_options_scanner_fixtures.py

Increment 11 — options_manager/scanner/fixtures.py tests. Proves the
deterministic scanner proof dataset covers every required scan_status
(TRIGGERED/WATCH/NO_TRADE/INVALID), scans to stable aggregate/reason
counts, composes correctly with the Increment 10 reporting utilities,
and that fixture builders never share or mutate state between calls.
"""

from __future__ import annotations

import ast
from pathlib import Path

import options_manager.scanner.fixtures as fixtures_module
from options_manager.scanner.fixtures import (
    ScannerProofReview,
    build_scanner_proof_dataset,
    review_scanner_proof_dataset,
    run_scanner_proof_dataset,
)

_SCANNED_FIXTURE_MODULES = (fixtures_module,)

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

_EXPECTED_TOTAL_ROWS = 8
_EXPECTED_REASON_COUNTS = {
    "valid_212_continuation": 2,
    "setup_forming_not_triggered": 1,
    "sequence_not_212": 1,
    "missing_entry_trigger": 1,
    "market_context_event_risk_high": 1,
    "contract_constraints_spread_too_wide": 1,
    "target_finder_rr_below_threshold": 1,
}


# --- 1. fixture dataset length is stable ------------------------------------------------------


def test_fixture_dataset_length_is_stable():
    assert len(build_scanner_proof_dataset()) == _EXPECTED_TOTAL_ROWS
    assert len(build_scanner_proof_dataset()) == _EXPECTED_TOTAL_ROWS


# --- 2. all required scan_status types are represented -----------------------------------------


def test_all_required_scan_status_types_are_represented():
    report = run_scanner_proof_dataset()
    statuses = {r.scan_status for r in report.results}
    assert statuses == {"TRIGGERED", "WATCH", "NO_TRADE", "INVALID"}


# --- 3. scan over fixtures produces expected aggregate counts -----------------------------------


def test_scan_over_fixtures_produces_expected_aggregate_counts():
    report = run_scanner_proof_dataset()
    assert report.total_rows == _EXPECTED_TOTAL_ROWS
    assert report.triggered == 2
    assert report.watch == 1
    assert report.no_trade == 1
    assert report.invalid == 4
    assert report.counts_by_status == {
        "TRIGGERED": 2,
        "WATCH": 1,
        "INVALID": 4,
        "NO_TRADE": 1,
    }


# --- 4. reason counts are stable -----------------------------------------------------------------


def test_reason_counts_are_stable():
    report = run_scanner_proof_dataset()
    assert report.counts_by_reason == _EXPECTED_REASON_COUNTS
    report_again = run_scanner_proof_dataset(build_scanner_proof_dataset())
    assert report_again.counts_by_reason == _EXPECTED_REASON_COUNTS


# --- 5. per-row fields compose correctly ---------------------------------------------------------


def test_per_row_fields_compose_correctly():
    report = run_scanner_proof_dataset()
    by_ticker = {r.ticker: r for r in report.results}

    triggered_call = by_ticker["SX_CALL_TRIGGERED"]
    assert triggered_call.scan_status == "TRIGGERED"
    assert triggered_call.target_1 == 103.0
    assert triggered_call.target_2 == 106.0

    triggered_put = by_ticker["SX_PUT_TRIGGERED"]
    assert triggered_put.scan_status == "TRIGGERED"
    assert triggered_put.target_1 == 92.0
    assert triggered_put.target_2 == 89.0

    watch_row = by_ticker["SX_WATCH"]
    assert watch_row.scan_status == "WATCH"
    assert watch_row.reason_code == "setup_forming_not_triggered"

    no_trade_row = by_ticker["SX_NO_TRADE"]
    assert no_trade_row.scan_status == "NO_TRADE"
    assert no_trade_row.reason_code == "sequence_not_212"

    missing_entry_row = by_ticker["SX_MISSING_ENTRY"]
    assert missing_entry_row.scan_status == "INVALID"
    assert missing_entry_row.reason_code == "missing_entry_trigger"

    bad_context_row = by_ticker["SX_BAD_CONTEXT"]
    assert bad_context_row.scan_status == "INVALID"
    assert bad_context_row.reason_code == "market_context_event_risk_high"

    bad_contract_row = by_ticker["SX_BAD_CONTRACT"]
    assert bad_contract_row.scan_status == "INVALID"
    assert bad_contract_row.reason_code == "contract_constraints_spread_too_wide"

    poor_rr_row = by_ticker["SX_POOR_RR"]
    assert poor_rr_row.scan_status == "INVALID"
    assert poor_rr_row.reason_code == "target_finder_rr_below_threshold"


# --- 6. scanner proof review is deterministic ------------------------------------------------------


def test_scanner_proof_review_is_deterministic():
    review_1 = review_scanner_proof_dataset()
    review_2 = review_scanner_proof_dataset()
    assert isinstance(review_1, ScannerProofReview)
    assert review_1.summary == review_2.summary
    assert review_1.rejections == review_2.rejections
    assert review_1.no_trades == review_2.no_trades
    assert review_1.warnings == review_2.warnings


# --- 7. human-readable summary over fixtures is deterministic --------------------------------------


def test_human_readable_summary_over_fixtures_is_deterministic():
    review_1 = review_scanner_proof_dataset()
    review_2 = review_scanner_proof_dataset()
    assert review_1.summary_text == review_2.summary_text
    assert "Scan Summary" in review_1.summary_text
    assert f"Total rows: {_EXPECTED_TOTAL_ROWS}" in review_1.summary_text


# --- 8. fixture helpers do not mutate rows between calls ---------------------------------------------


def test_fixture_helpers_do_not_mutate_rows_between_calls():
    dataset_1 = build_scanner_proof_dataset()
    dataset_2 = build_scanner_proof_dataset()
    assert dataset_1 == dataset_2
    assert dataset_1 is not dataset_2
    for row_1, row_2 in zip(dataset_1, dataset_2):
        assert row_1 == row_2
        assert row_1 is not row_2
    with __import__("pytest").raises((AttributeError, TypeError)):
        dataset_1[0].ticker = "MUTATED"


# --- structural safety (matches this buildout's established pattern) ------------------------------------


def _imported_modules(module) -> list[str]:
    """Absolute module names only; relative imports (level > 0) resolve
    within the same package and are excluded rather than misreported as a
    cross-boundary import (see the Increment 1-10 fix for the same
    issue)."""
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def test_fixture_modules_do_not_import_replay_engine_or_replay_package():
    for module in _SCANNED_FIXTURE_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            assert name != "replay" and not name.startswith("replay."), (
                f"{module.__name__} must not import replay.* directly: {name}"
            )
            assert not name.startswith("options_manager.replay"), (
                f"{module.__name__} must not import options_manager.replay: {name}"
            )


def test_fixture_modules_have_no_forbidden_imports():
    for module in _SCANNED_FIXTURE_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in name, f"{module.__name__} must not import {name!r}"


def test_fixture_modules_have_no_cross_boundary_imports_at_all():
    for module in _SCANNED_FIXTURE_MODULES:
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


def test_fixture_modules_do_not_import_live_context_loader():
    for module in _SCANNED_FIXTURE_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            assert name != "context" and not name.startswith("context."), (
                f"{module.__name__} must not import the live context.* loader: {name}"
            )


def test_fixture_modules_have_no_quote_fetch_identifiers():
    for module in _SCANNED_FIXTURE_MODULES:
        source = Path(module.__file__).read_text().lower()
        for forbidden in _FORBIDDEN_QUOTE_FETCH_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_fixture_modules_have_no_order_action_verbs():
    for module in _SCANNED_FIXTURE_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in _FORBIDDEN_ORDER_ACTION_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_fixture_modules_do_not_mutate_live_options_flag():
    for module in _SCANNED_FIXTURE_MODULES:
        source = Path(module.__file__).read_text()
        assert "LIVE_OPTIONS_TRADING_ENABLED" not in source


def test_fixture_modules_have_no_alert_send_identifiers():
    for module in _SCANNED_FIXTURE_MODULES:
        source = Path(module.__file__).read_text().lower()
        for forbidden in _FORBIDDEN_ALERT_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_fixture_modules_do_not_read_or_write_files():
    for module in _SCANNED_FIXTURE_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in ("open(", ".write(", ".write_text(", ".write_bytes(", ".read_text("):
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_fixture_modules_do_not_reimplement_strategy_or_scanner_logic():
    source = Path(fixtures_module.__file__).read_text()
    assert "def evaluate_strat_212" not in source
    assert "def scan_watchlist_strat_212" not in source
