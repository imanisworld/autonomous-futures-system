"""
tests/test_options_replay_fixtures.py

Increment 7 — options_manager/replay/fixtures.py tests. Proves the
deterministic replay proof dataset covers every required 2-1-2 replay
outcome type, replays to stable aggregate/rejection counts, composes
correctly with the Increment 6 reporting utilities, and that fixture
builders never share or mutate state between calls.
"""

from __future__ import annotations

import ast
from pathlib import Path

import options_manager.replay.fixtures as fixtures_module
from options_manager.replay.fixtures import (
    ReplayProofReview,
    build_replay_proof_dataset,
    review_replay_proof_dataset,
    run_replay_proof_dataset,
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

_FORBIDDEN_SCANNER_FRAGMENTS = (
    "scanner",
    "watchlist",
    "option_chain",
    "chain_fetch",
    "contract_select",
)

_EXPECTED_TOTAL_ROWS = 12
_EXPECTED_REJECTIONS = {
    "sequence_not_212": 1,
    "market_context_event_risk_high": 1,
    "contract_constraints_spread_too_wide": 1,
    "target_finder_rr_below_threshold": 1,
}


# --- 1. fixture dataset length is stable ------------------------------------------------------


def test_fixture_dataset_length_is_stable():
    assert len(build_replay_proof_dataset()) == _EXPECTED_TOTAL_ROWS
    assert len(build_replay_proof_dataset()) == _EXPECTED_TOTAL_ROWS


# --- 2. all required outcome types are represented --------------------------------------------


def test_all_required_outcome_types_are_represented():
    report = run_replay_proof_dataset()
    statuses = {r.status for r in report.results}
    outcomes = {r.replay_outcome for r in report.results}
    assert statuses == {"VALID", "WATCH", "INVALID"}
    assert outcomes == {
        "TARGET_1_HIT",
        "TARGET_2_HIT",
        "STOP_HIT",
        "NOT_TRIGGERED",
        "INVALID",
        "NO_OUTCOME_DATA",
    }


# --- 3. replay over fixtures produces expected aggregate counts -------------------------------


def test_replay_over_fixtures_produces_expected_aggregate_counts():
    report = run_replay_proof_dataset()
    assert report.total_rows == _EXPECTED_TOTAL_ROWS
    assert report.valid_setups == 7
    assert report.invalid_setups == 4
    assert report.watch_setups == 1
    assert report.target_1_hits == 2
    assert report.target_2_hits == 2
    assert report.stop_hits == 2
    assert report.no_outcome_data == 1
    assert report.win_rate_target_1 == 4 / 6


# --- 4. rejection counts by reason are stable --------------------------------------------------


def test_rejection_counts_by_reason_are_stable():
    report = run_replay_proof_dataset()
    assert report.rejection_counts_by_reason == _EXPECTED_REJECTIONS
    # Re-running from a fresh dataset build produces the same counts.
    report_again = run_replay_proof_dataset(build_replay_proof_dataset())
    assert report_again.rejection_counts_by_reason == _EXPECTED_REJECTIONS


# --- 5. target/context/contract fields compose correctly ---------------------------------------


def test_target_context_contract_fields_compose_correctly():
    report = run_replay_proof_dataset()
    by_ticker = {r.ticker: r for r in report.results}

    target_hit_row = by_ticker["FX_CALL_T1"]
    assert target_hit_row.status == "VALID"
    assert target_hit_row.target_1 == 103.0
    assert target_hit_row.target_2 == 106.0
    assert target_hit_row.replay_outcome == "TARGET_1_HIT"

    bad_context_row = by_ticker["FX_BAD_CONTEXT"]
    assert bad_context_row.status == "INVALID"
    assert bad_context_row.reason_code == "market_context_event_risk_high"
    assert bad_context_row.context_status is None

    bad_contract_row = by_ticker["FX_BAD_CONTRACT"]
    assert bad_contract_row.status == "INVALID"
    assert bad_contract_row.reason_code == "contract_constraints_spread_too_wide"
    assert bad_contract_row.contract_status is None

    poor_rr_row = by_ticker["FX_POOR_RR"]
    assert poor_rr_row.status == "INVALID"
    assert poor_rr_row.reason_code == "target_finder_rr_below_threshold"

    bad_sequence_row = by_ticker["FX_BAD_SEQ"]
    assert bad_sequence_row.status == "INVALID"
    assert bad_sequence_row.reason_code == "sequence_not_212"

    watch_row = by_ticker["FX_WATCH"]
    assert watch_row.status == "WATCH"
    assert watch_row.replay_outcome == "NOT_TRIGGERED"

    no_outcome_row = by_ticker["FX_NO_OUTCOME"]
    assert no_outcome_row.status == "VALID"
    assert no_outcome_row.replay_outcome == "NO_OUTCOME_DATA"


# --- 6. replay report summary over fixtures is deterministic -----------------------------------


def test_replay_report_summary_over_fixtures_is_deterministic():
    review_1 = review_replay_proof_dataset()
    review_2 = review_replay_proof_dataset()
    assert isinstance(review_1, ReplayProofReview)
    assert review_1.summary == review_2.summary
    assert review_1.rejections == review_2.rejections
    assert review_1.outcomes == review_2.outcomes
    assert review_1.warnings == review_2.warnings


# --- 7. human-readable report summary is deterministic ------------------------------------------


def test_human_readable_summary_over_fixtures_is_deterministic():
    review_1 = review_replay_proof_dataset()
    review_2 = review_replay_proof_dataset()
    assert review_1.summary_text == review_2.summary_text
    assert "Replay Summary" in review_1.summary_text
    assert f"Total rows: {_EXPECTED_TOTAL_ROWS}" in review_1.summary_text


# --- 8. fixture helpers do not mutate rows between calls ----------------------------------------


def test_fixture_helpers_do_not_mutate_rows_between_calls():
    dataset_1 = build_replay_proof_dataset()
    dataset_2 = build_replay_proof_dataset()
    assert dataset_1 == dataset_2
    assert dataset_1 is not dataset_2
    for row_1, row_2 in zip(dataset_1, dataset_2):
        assert row_1 == row_2
        assert row_1 is not row_2
    # Frozen rows cannot be mutated in place; confirm that guarantee holds.
    with __import__("pytest").raises((AttributeError, TypeError)):
        dataset_1[0].ticker = "MUTATED"


# --- structural safety (matches this buildout's established pattern) ---------------------------


def _imported_modules(module) -> list[str]:
    """Absolute module names only; relative imports (level > 0) resolve
    within the same package and are excluded rather than misreported as a
    cross-boundary import (see the Increment 1-6 fix for the same
    issue)."""
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def test_fixture_modules_do_not_import_replay_engine_or_candle_loader():
    for module in _SCANNED_FIXTURE_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            assert name != "replay" and not name.startswith("replay."), (
                f"{module.__name__} must not import replay.* directly "
                f"(replay_engine.py has execution/broker/journal imports; "
                f"candle_loader.py is not needed here): {name}"
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


def test_fixture_modules_have_no_order_action_verbs():
    for module in _SCANNED_FIXTURE_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in _FORBIDDEN_ORDER_ACTION_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_fixture_modules_do_not_mutate_live_options_flag():
    for module in _SCANNED_FIXTURE_MODULES:
        source = Path(module.__file__).read_text()
        assert "LIVE_OPTIONS_TRADING_ENABLED" not in source


def test_fixture_modules_have_no_scanner_or_chain_fetch_or_contract_selection_imports():
    for module in _SCANNED_FIXTURE_MODULES:
        source = Path(module.__file__).read_text().lower()
        for forbidden in _FORBIDDEN_SCANNER_FRAGMENTS:
            assert forbidden not in source, f"{module.__name__} must not reference {forbidden!r}"


def test_fixture_modules_do_not_read_or_write_files():
    for module in _SCANNED_FIXTURE_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in ("open(", ".write(", ".write_text(", ".write_bytes(", ".read_text("):
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_fixture_modules_do_not_modify_strat_212_source():
    source = Path(fixtures_module.__file__).read_text()
    assert "def evaluate_strat_212" not in source
