"""
tests/test_options_replay_report.py

Increment 6 — options_manager/review/replay_report.py tests. Proves the
advisory-only replay reporting/rejection-review/outcome-review/warning-
aggregation utilities are pure functions of a caller-supplied
Strat212ReplayReport: they run no replay, fetch no data, write no files,
send no alerts, and compute deterministic, correctly-ranked, correctly-
percented output.
"""

from __future__ import annotations

import ast
from pathlib import Path

import options_manager.review.base as review_base_module
import options_manager.review.replay_report as review_report_module
from options_manager.context import MarketContextInputs
from options_manager.contracts import ContractConstraintsInputs
from options_manager.levels import LevelFinderInputs
from options_manager.replay import Strat212ReplayRow, replay_strat_212
from options_manager.review import (
    aggregate_warnings,
    outcome_review,
    rejection_review,
    render_summary_text,
    summarize_replay,
)
from options_manager.strategies import (
    Strat212Bars,
    StrategyContractConstraints,
    StrategyMarketContext,
)

_SCANNED_REVIEW_MODULES = (review_base_module, review_report_module)

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

_FORBIDDEN_SCANNER_FRAGMENTS = (
    "scanner",
    "watchlist",
    "option_chain",
    "chain_fetch",
    "contract_select",
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


def _watch_row(**overrides) -> Strat212ReplayRow:
    fields = dict(
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
    fields.update(overrides)
    return Strat212ReplayRow(**fields)


# --- 1. summary preserves core replay metrics -----------------------------------------------


def test_summary_preserves_core_replay_metrics():
    rows = [
        _valid_call_row(future_high=104.0, future_low=98.0, ticker="A"),  # TARGET_1_HIT
        _valid_call_row(future_high=107.0, future_low=98.0, ticker="B"),  # TARGET_2_HIT
        _valid_call_row(future_high=100.0, future_low=94.0, ticker="C"),  # STOP_HIT
        _valid_call_row(underlying_invalidation=None, ticker="D"),  # INVALID
        _watch_row(ticker="E"),  # WATCH
    ]
    report = replay_strat_212(rows)
    summary = summarize_replay(report)
    assert summary.total_rows == report.total_rows == 5
    assert summary.valid_setups == report.valid_setups == 3
    assert summary.invalid_setups == report.invalid_setups == 1
    assert summary.watch_setups == report.watch_setups == 1
    assert summary.target_1_hits == report.target_1_hits == 1
    assert summary.target_2_hits == report.target_2_hits == 1
    assert summary.stop_hits == report.stop_hits == 1
    assert summary.no_outcome_data == report.no_outcome_data == 0
    assert summary.win_rate_target_1 == report.win_rate_target_1 == 2 / 3
    assert summary.rejection_counts_by_reason == report.rejection_counts_by_reason


# --- 2. rejection counts are ranked correctly -----------------------------------------------


def test_rejection_counts_ranked_correctly():
    rows = [
        _valid_call_row(underlying_invalidation=None, ticker="A"),  # missing_invalidation
        _valid_call_row(underlying_invalidation=None, ticker="B"),  # missing_invalidation
        _valid_call_row(underlying_invalidation=None, ticker="C"),  # missing_invalidation
        _valid_call_row(target_1=None, target_2=None, ticker="D"),  # missing_target_1
    ]
    report = replay_strat_212(rows)
    summary = summarize_replay(report)
    assert summary.top_rejection_reasons[0] == ("missing_invalidation", 3)
    assert summary.top_rejection_reasons[1] == ("missing_target_1", 1)


def test_rejection_counts_ranked_alphabetically_on_tie():
    rows = [
        _valid_call_row(underlying_invalidation=None, ticker="A"),  # missing_invalidation
        _valid_call_row(target_1=None, target_2=None, ticker="B"),  # missing_target_1
    ]
    report = replay_strat_212(rows)
    summary = summarize_replay(report)
    assert [reason for reason, _ in summary.top_rejection_reasons] == [
        "missing_invalidation",
        "missing_target_1",
    ]


# --- 3. rejection review includes sample tickers/timestamps ---------------------------------


def test_rejection_review_includes_sample_tickers_and_timestamps():
    rows = [
        _valid_call_row(underlying_invalidation=None, ticker="AAA", timestamp="t1"),
        _valid_call_row(underlying_invalidation=None, ticker="BBB", timestamp="t2"),
    ]
    report = replay_strat_212(rows)
    entries = rejection_review(report)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.reason_code == "missing_invalidation"
    assert entry.count == 2
    assert entry.percent_of_total == 100.0
    assert entry.sample_tickers == ["AAA", "BBB"]
    assert entry.sample_timestamps == ["t1", "t2"]


def test_rejection_review_sample_is_capped():
    rows = [
        _valid_call_row(underlying_invalidation=None, ticker=f"T{i}") for i in range(10)
    ]
    report = replay_strat_212(rows)
    entries = rejection_review(report, sample_limit=3)
    assert entries[0].count == 10
    assert len(entries[0].sample_tickers) == 3
    assert len(entries[0].sample_timestamps) == 3


# --- 4. outcome review counts target/stop/open/no-data outcomes correctly -------------------


def test_outcome_review_counts_outcomes_correctly():
    rows = [
        _valid_call_row(future_high=104.0, future_low=98.0, ticker="A"),  # TARGET_1_HIT
        _valid_call_row(future_high=107.0, future_low=98.0, ticker="B"),  # TARGET_2_HIT
        _valid_call_row(future_high=100.0, future_low=94.0, ticker="C"),  # STOP_HIT
        _valid_call_row(future_high=100.0, future_low=96.0, ticker="D"),  # OPEN
        _valid_call_row(ticker="E"),  # NO_OUTCOME_DATA
    ]
    report = replay_strat_212(rows)
    entries = {entry.outcome: entry for entry in outcome_review(report)}
    assert entries["TARGET_1_HIT"].count == 1
    assert entries["TARGET_2_HIT"].count == 1
    assert entries["STOP_HIT"].count == 1
    assert entries["OPEN"].count == 1
    assert entries["NO_OUTCOME_DATA"].count == 1


def test_outcome_review_excludes_invalid_and_watch_rows():
    rows = [
        _valid_call_row(underlying_invalidation=None, ticker="A"),  # INVALID
        _watch_row(ticker="B"),  # WATCH
        _valid_call_row(future_high=104.0, future_low=98.0, ticker="C"),  # TARGET_1_HIT
    ]
    report = replay_strat_212(rows)
    outcomes = {entry.outcome for entry in outcome_review(report)}
    assert outcomes == {"TARGET_1_HIT"}


# --- 5. percent calculations are correct -----------------------------------------------------


def test_percent_of_total_is_correct():
    rows = [
        _valid_call_row(underlying_invalidation=None, ticker="A"),
        _valid_call_row(future_high=104.0, future_low=98.0, ticker="B"),
        _valid_call_row(future_high=104.0, future_low=98.0, ticker="C"),
        _valid_call_row(future_high=104.0, future_low=98.0, ticker="D"),
    ]
    report = replay_strat_212(rows)
    entries = rejection_review(report)
    assert entries[0].percent_of_total == 25.0


def test_percent_of_valid_setups_is_correct():
    rows = [
        _valid_call_row(future_high=104.0, future_low=98.0, ticker="A"),  # TARGET_1_HIT
        _valid_call_row(future_high=100.0, future_low=96.0, ticker="B"),  # OPEN
        _valid_call_row(future_high=100.0, future_low=96.0, ticker="C"),  # OPEN
        _valid_call_row(future_high=100.0, future_low=96.0, ticker="D"),  # OPEN
    ]
    report = replay_strat_212(rows)
    entries = {entry.outcome: entry for entry in outcome_review(report)}
    assert entries["OPEN"].percent_of_valid_setups == 75.0
    assert entries["TARGET_1_HIT"].percent_of_valid_setups == 25.0


# --- 6. average rr_1 and rr_2 are calculated correctly ---------------------------------------


def _valid_call_row_with_derived_targets(*, ticker, entry, invalidation, resistance_levels):
    return Strat212ReplayRow(
        ticker=ticker,
        timestamp="2026-01-02T10:00:00Z",
        direction="CALL",
        bars=_bullish_bars(),
        entry_trigger=entry,
        underlying_invalidation=invalidation,
        level_inputs=LevelFinderInputs(
            direction="CALL",
            entry=entry,
            underlying_invalidation=invalidation,
            resistance_levels=resistance_levels,
        ),
        market_context=StrategyMarketContext(confirmed=True),
        contract_constraints=StrategyContractConstraints(constraints_met=True),
    )


def test_average_rr_calculated_correctly():
    # rr_1/rr_2 are only populated when targets are derived via level_inputs
    # (options_manager/strategies/strat_212.py leaves them None for
    # explicitly-supplied target_1/target_2) -- so both rows here derive
    # their targets to get a real rr_1/rr_2 to average.
    rows = [
        _valid_call_row_with_derived_targets(
            ticker="A", entry=100.0, invalidation=97.0, resistance_levels=(103.0, 108.0)
        ),
        _valid_call_row_with_derived_targets(
            ticker="B", entry=100.0, invalidation=95.0, resistance_levels=(110.0, 120.0)
        ),
    ]
    report = replay_strat_212(rows)
    summary = summarize_replay(report)
    rr_1_values = [r.rr_1 for r in report.results if r.rr_1 is not None]
    rr_2_values = [r.rr_2 for r in report.results if r.rr_2 is not None]
    assert len(rr_1_values) == 2 and len(rr_2_values) == 2
    assert summary.average_rr_1 == sum(rr_1_values) / len(rr_1_values)
    assert summary.average_rr_2 == sum(rr_2_values) / len(rr_2_values)


def test_average_rr_is_none_when_no_valid_rows_have_it():
    report = replay_strat_212([_watch_row()])
    summary = summarize_replay(report)
    assert summary.average_rr_1 is None
    assert summary.average_rr_2 is None


# --- 7. context status counts work -----------------------------------------------------------


def test_context_status_counts_work():
    rows = [
        # Explicit market_context.confirmed skips derivation entirely, so
        # context_status stays None/"NONE" on the replay result.
        _valid_call_row(ticker="A", market_context=StrategyMarketContext(confirmed=True)),
        # Unresolved market_context + market_context_inputs derives a real
        # VALID context_status via evaluate_market_context().
        _valid_call_row(
            ticker="B",
            market_context=StrategyMarketContext(),
            market_context_inputs=_aligned_market_context_inputs(),
        ),
        # A mixed (not fully aligned) context derives CAUTION, not VALID.
        _valid_call_row(
            ticker="C",
            market_context=StrategyMarketContext(),
            market_context_inputs=_aligned_market_context_inputs(qqq_trend="bearish"),
        ),
    ]
    report = replay_strat_212(rows)
    summary = summarize_replay(report)
    assert summary.context_status_counts == {"CAUTION": 1, "NONE": 1, "VALID": 1}


# --- 8. contract status counts work -----------------------------------------------------------


def test_contract_status_counts_work():
    rows = [
        # Explicit contract_constraints.constraints_met skips derivation
        # entirely, so contract_status stays None/"NONE" on the result.
        _valid_call_row(
            ticker="A", contract_constraints=StrategyContractConstraints(constraints_met=True)
        ),
        # Unresolved contract_constraints + contract_constraints_inputs
        # derives a real VALID contract_status via
        # evaluate_contract_constraints().
        _valid_call_row(
            ticker="B",
            contract_constraints=StrategyContractConstraints(),
            contract_constraints_inputs=_valid_contract_constraints_inputs(),
        ),
        _valid_call_row(
            ticker="C",
            contract_constraints=StrategyContractConstraints(),
            contract_constraints_inputs=_valid_contract_constraints_inputs(),
        ),
    ]
    report = replay_strat_212(rows)
    summary = summarize_replay(report)
    assert summary.contract_status_counts == {"NONE": 1, "VALID": 2}


# --- 9. warnings aggregate correctly ----------------------------------------------------------


def test_warnings_aggregate_correctly():
    mixed_context = _aligned_market_context_inputs(qqq_trend="bearish")
    rows = [
        _valid_call_row(
            ticker="A",
            market_context=StrategyMarketContext(),
            market_context_inputs=mixed_context,
        ),
        _valid_call_row(
            ticker="B",
            market_context=StrategyMarketContext(),
            market_context_inputs=mixed_context,
        ),
    ]
    report = replay_strat_212(rows)
    assert sum(len(r.warnings) for r in report.results) == 2
    aggregation = aggregate_warnings(report)
    assert aggregation.total_warnings == 2
    assert aggregation.warning_counts == {
        "SPY/QQQ trend not both aligned with CALL "
        "(spy_trend='bullish', qqq_trend='bearish')": 2
    }
    assert aggregation.warning_counts == dict(sorted(aggregation.warning_counts.items()))


def test_warnings_aggregate_empty_when_no_warnings_present():
    report = replay_strat_212([_valid_call_row()])
    aggregation = aggregate_warnings(report)
    assert aggregation.total_warnings == 0
    assert aggregation.warning_counts == {}


# --- 10. empty report does not crash ----------------------------------------------------------


def test_empty_report_does_not_crash():
    report = replay_strat_212([])
    summary = summarize_replay(report)
    assert summary.total_rows == 0
    assert summary.win_rate_target_1 is None
    assert summary.average_rr_1 is None
    assert summary.average_rr_2 is None
    assert summary.top_rejection_reasons == []
    assert rejection_review(report) == []
    assert outcome_review(report) == []
    aggregation = aggregate_warnings(report)
    assert aggregation.total_warnings == 0
    text = render_summary_text(report)
    assert "Total rows: 0" in text


# --- 11. human-readable summary is deterministic -----------------------------------------------


def test_human_readable_summary_is_deterministic():
    rows = [
        _valid_call_row(future_high=104.0, future_low=98.0, ticker="A"),
        _valid_call_row(underlying_invalidation=None, ticker="B"),
    ]
    report = replay_strat_212(rows)
    text_1 = render_summary_text(report)
    text_2 = render_summary_text(report)
    assert text_1 == text_2
    assert "Replay Summary" in text_1
    assert "Top rejection reasons:" in text_1


def test_human_readable_summary_is_plain_text_not_markdown():
    report = replay_strat_212([_valid_call_row()])
    text = render_summary_text(report)
    assert "|" not in text
    assert "```" not in text
    assert "# " not in text


# --- structural safety (matches this buildout's established pattern) ------------------------


def _imported_modules(module) -> list[str]:
    """Absolute module names only; relative imports (level > 0) resolve
    within the same package and are excluded rather than misreported as a
    cross-boundary import (see the Increment 1-5 fix for the same
    issue)."""
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def test_review_modules_do_not_import_replay_engine_or_candle_loader():
    for module in _SCANNED_REVIEW_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            assert name != "replay" and not name.startswith("replay."), (
                f"{module.__name__} must not import replay.* directly "
                f"(replay_engine.py has execution/broker/journal imports; "
                f"candle_loader.py is not needed here): {name}"
            )
            assert name not in ("options_manager.replay.replay_engine",), (
                f"{module.__name__} must not import options_manager.replay.replay_engine: {name}"
            )


def test_review_modules_have_no_forbidden_imports():
    for module in _SCANNED_REVIEW_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in name, f"{module.__name__} must not import {name!r}"


def test_review_modules_have_no_cross_boundary_imports_at_all():
    for module in _SCANNED_REVIEW_MODULES:
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


def test_review_modules_do_not_import_live_context_loader():
    for module in _SCANNED_REVIEW_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            assert name != "context" and not name.startswith("context."), (
                f"{module.__name__} must not import the live context.* loader: {name}"
            )


def test_review_modules_have_no_order_action_verbs():
    for module in _SCANNED_REVIEW_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in _FORBIDDEN_ORDER_ACTION_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_review_modules_do_not_mutate_live_options_flag():
    for module in _SCANNED_REVIEW_MODULES:
        source = Path(module.__file__).read_text()
        assert "LIVE_OPTIONS_TRADING_ENABLED" not in source


def test_review_modules_have_no_scanner_or_chain_fetch_or_contract_selection_imports():
    for module in _SCANNED_REVIEW_MODULES:
        source = Path(module.__file__).read_text().lower()
        for forbidden in _FORBIDDEN_SCANNER_FRAGMENTS:
            assert forbidden not in source, f"{module.__name__} must not reference {forbidden!r}"


def test_review_modules_do_not_write_files():
    for module in _SCANNED_REVIEW_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in ("open(", "Path(", ".write(", ".write_text(", ".write_bytes("):
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_review_modules_do_not_run_replay():
    for module in _SCANNED_REVIEW_MODULES:
        source = Path(module.__file__).read_text()
        assert "replay_strat_212(" not in source, (
            f"{module.__name__} must consume a caller-supplied report, not run its own replay"
        )
