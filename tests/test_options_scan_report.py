"""
tests/test_options_scan_report.py

Increment 10 — options_manager/scanner/report.py tests. Proves the
advisory-only scan reporting/rejection-review/no-trade-review/warning-
aggregation utilities are pure functions of a caller-supplied ScanReport:
they scan nothing, fetch nothing, write no files, send no alerts, and
compute deterministic, correctly-ranked, correctly-percented output that
preserves the NO_TRADE-vs-INVALID distinction from Increment 9.
"""

from __future__ import annotations

import ast
from pathlib import Path

import options_manager.scanner.report as scan_report_module
from options_manager.scanner import ScanReport, WatchlistRow, scan_watchlist_strat_212
from options_manager.scanner.report import (
    aggregate_scan_warnings,
    no_trade_review,
    rejection_review,
    render_scan_summary_text,
    summarize_scan,
)
from options_manager.strategies import (
    Strat212Bars,
    StrategyContractConstraints,
    StrategyMarketContext,
)

_SCANNED_REPORT_MODULES = (scan_report_module,)

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


def _watch_row(**overrides) -> WatchlistRow:
    fields = dict(
        ticker="SPY",
        timestamp="2026-01-02T10:00:00Z",
        direction="CALL",
        bars=_forming_bars(),
        market_context=StrategyMarketContext(),
        contract_constraints=StrategyContractConstraints(),
    )
    fields.update(overrides)
    return WatchlistRow(**fields)


def _no_trade_row(**overrides) -> WatchlistRow:
    return _valid_call_row(bars=_bad_sequence_bars(), **overrides)


def _invalid_row(**overrides) -> WatchlistRow:
    return _valid_call_row(entry_trigger=None, **overrides)


# --- 1. summary preserves core scan metrics ---------------------------------------------------


def test_summary_preserves_core_scan_metrics():
    rows = [
        _valid_call_row(ticker="A"),
        _watch_row(ticker="B"),
        _no_trade_row(ticker="C"),
        _invalid_row(ticker="D"),
    ]
    report = scan_watchlist_strat_212(rows)
    summary = summarize_scan(report)
    assert summary.total_rows == report.total_rows == 4
    assert summary.triggered == report.triggered == 1
    assert summary.watch == report.watch == 1
    assert summary.no_trade == report.no_trade == 1
    assert summary.invalid == report.invalid == 1
    assert summary.counts_by_status == report.counts_by_status
    assert summary.counts_by_reason == report.counts_by_reason


# --- 2. rejection counts ranked correctly (INVALID only) --------------------------------------


def test_rejection_counts_ranked_correctly():
    rows = [
        _invalid_row(ticker="A"),
        _invalid_row(ticker="B"),
        _valid_call_row(ticker="C", underlying_invalidation=None),  # missing_invalidation
    ]
    report = scan_watchlist_strat_212(rows)
    summary = summarize_scan(report)
    assert summary.top_invalid_reasons[0] == ("missing_entry_trigger", 2)
    assert summary.top_invalid_reasons[1] == ("missing_invalidation", 1)


def test_rejection_counts_ranked_alphabetically_on_tie():
    rows = [
        _invalid_row(ticker="A"),  # missing_entry_trigger
        _valid_call_row(ticker="B", underlying_invalidation=None),  # missing_invalidation
    ]
    report = scan_watchlist_strat_212(rows)
    summary = summarize_scan(report)
    assert [reason for reason, _ in summary.top_invalid_reasons] == [
        "missing_entry_trigger",
        "missing_invalidation",
    ]


# --- 3. no-trade counts ranked correctly and kept separate from INVALID -----------------------


def test_no_trade_counts_ranked_correctly_and_separate_from_invalid():
    rows = [
        _no_trade_row(ticker="A"),  # sequence_not_212
        _no_trade_row(ticker="B"),  # sequence_not_212
        _invalid_row(ticker="C"),  # missing_entry_trigger -- must not appear in no-trade bucket
    ]
    report = scan_watchlist_strat_212(rows)
    summary = summarize_scan(report)
    assert summary.top_no_trade_reasons == [("sequence_not_212", 2)]
    assert summary.top_invalid_reasons == [("missing_entry_trigger", 1)]


# --- 4. rejection review includes sample tickers/timestamps (INVALID only) ---------------------


def test_rejection_review_includes_sample_tickers_and_timestamps():
    rows = [
        _invalid_row(ticker="AAA", timestamp="t1"),
        _invalid_row(ticker="BBB", timestamp="t2"),
        _no_trade_row(ticker="CCC", timestamp="t3"),
    ]
    report = scan_watchlist_strat_212(rows)
    entries = rejection_review(report)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.reason_code == "missing_entry_trigger"
    assert entry.count == 2
    assert entry.percent_of_total == (2 / 3) * 100.0
    assert entry.sample_tickers == ["AAA", "BBB"]
    assert entry.sample_timestamps == ["t1", "t2"]


def test_rejection_review_sample_is_capped():
    rows = [_invalid_row(ticker=f"T{i}") for i in range(10)]
    report = scan_watchlist_strat_212(rows)
    entries = rejection_review(report, sample_limit=3)
    assert entries[0].count == 10
    assert len(entries[0].sample_tickers) == 3
    assert len(entries[0].sample_timestamps) == 3


# --- 5. no-trade review includes sample tickers/timestamps --------------------------------------


def test_no_trade_review_includes_sample_tickers_and_timestamps():
    rows = [
        _no_trade_row(ticker="XXX", timestamp="tx1"),
        _no_trade_row(ticker="YYY", timestamp="tx2"),
    ]
    report = scan_watchlist_strat_212(rows)
    entries = no_trade_review(report)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.reason_code == "sequence_not_212"
    assert entry.count == 2
    assert entry.sample_tickers == ["XXX", "YYY"]
    assert entry.sample_timestamps == ["tx1", "tx2"]


# --- 6. context/contract status counts work -----------------------------------------------------


def test_context_and_contract_status_counts_work():
    rows = [
        _valid_call_row(ticker="A"),  # explicit market_context/contract_constraints -> None/NONE
        _valid_call_row(ticker="B"),
    ]
    report = scan_watchlist_strat_212(rows)
    summary = summarize_scan(report)
    assert summary.context_status_counts == {"NONE": 2}
    assert summary.contract_status_counts == {"NONE": 2}


# --- 7. warnings aggregate correctly --------------------------------------------------------------


def test_warnings_aggregate_correctly():
    report = scan_watchlist_strat_212([_valid_call_row(ticker="A"), _valid_call_row(ticker="B")])
    aggregation = aggregate_scan_warnings(report)
    assert aggregation.total_warnings == sum(len(r.warnings) for r in report.results)
    assert aggregation.warning_counts == dict(sorted(aggregation.warning_counts.items()))


def test_warnings_aggregate_empty_when_no_warnings_present():
    report = scan_watchlist_strat_212([_valid_call_row()])
    aggregation = aggregate_scan_warnings(report)
    assert aggregation.total_warnings == 0
    assert aggregation.warning_counts == {}


# --- 8. empty scan report does not crash -----------------------------------------------------------


def test_empty_scan_report_does_not_crash():
    report = scan_watchlist_strat_212([])
    summary = summarize_scan(report)
    assert summary.total_rows == 0
    assert summary.top_invalid_reasons == []
    assert summary.top_no_trade_reasons == []
    assert rejection_review(report) == []
    assert no_trade_review(report) == []
    aggregation = aggregate_scan_warnings(report)
    assert aggregation.total_warnings == 0
    text = render_scan_summary_text(report)
    assert "Total rows: 0" in text


# --- 9. human-readable summary is deterministic ------------------------------------------------------


def test_human_readable_summary_is_deterministic():
    rows = [
        _valid_call_row(ticker="A"),
        _invalid_row(ticker="B"),
        _no_trade_row(ticker="C"),
    ]
    report = scan_watchlist_strat_212(rows)
    text_1 = render_scan_summary_text(report)
    text_2 = render_scan_summary_text(report)
    assert text_1 == text_2
    assert "Scan Summary" in text_1
    assert "Top invalid reasons:" in text_1
    assert "Top no-trade reasons:" in text_1


def test_human_readable_summary_is_plain_text_not_markdown():
    report = scan_watchlist_strat_212([_valid_call_row()])
    text = render_scan_summary_text(report)
    assert "|" not in text
    assert "```" not in text
    assert "# " not in text


# --- structural safety (matches this buildout's established pattern) --------------------------------


def _imported_modules(module) -> list[str]:
    """Absolute module names only; relative imports (level > 0) resolve
    within the same package and are excluded rather than misreported as a
    cross-boundary import (see the Increment 1-9 fix for the same
    issue)."""
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def test_report_module_does_not_scan():
    for module in _SCANNED_REPORT_MODULES:
        source = Path(module.__file__).read_text()
        assert "scan_watchlist_strat_212(" not in source, (
            f"{module.__name__} must consume a caller-supplied ScanReport, not run its own scan"
        )


def test_report_module_does_not_import_replay_engine_or_replay_package():
    for module in _SCANNED_REPORT_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            assert name != "replay" and not name.startswith("replay."), (
                f"{module.__name__} must not import replay.* directly: {name}"
            )
            assert not name.startswith("options_manager.replay"), (
                f"{module.__name__} must not import options_manager.replay: {name}"
            )


def test_report_module_has_no_forbidden_imports():
    for module in _SCANNED_REPORT_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in name, f"{module.__name__} must not import {name!r}"


def test_report_module_has_no_cross_boundary_imports_at_all():
    for module in _SCANNED_REPORT_MODULES:
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


def test_report_module_does_not_import_live_context_loader():
    for module in _SCANNED_REPORT_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            assert name != "context" and not name.startswith("context."), (
                f"{module.__name__} must not import the live context.* loader: {name}"
            )


def test_report_module_has_no_quote_fetch_identifiers():
    for module in _SCANNED_REPORT_MODULES:
        source = Path(module.__file__).read_text().lower()
        for forbidden in _FORBIDDEN_QUOTE_FETCH_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_report_module_has_no_order_action_verbs():
    for module in _SCANNED_REPORT_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in _FORBIDDEN_ORDER_ACTION_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_report_module_does_not_mutate_live_options_flag():
    for module in _SCANNED_REPORT_MODULES:
        source = Path(module.__file__).read_text()
        assert "LIVE_OPTIONS_TRADING_ENABLED" not in source


def test_report_module_does_not_write_files():
    for module in _SCANNED_REPORT_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in ("open(", ".write(", ".write_text(", ".write_bytes("):
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_report_module_has_no_alert_send_identifiers():
    for module in _SCANNED_REPORT_MODULES:
        source = Path(module.__file__).read_text().lower()
        for forbidden in _FORBIDDEN_ALERT_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"
