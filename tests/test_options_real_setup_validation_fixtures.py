"""
tests/test_options_real_setup_validation_fixtures.py

Increment 23 — options_manager/validation/{base,fixtures}.py tests.
Proves the real-setup validation fixtures run through the existing
advisory-only scanner path, keep each fixture's recorded outcome
separate from the scanner's own verdict, fail closed exactly like every
other row in this buildout when data is missing, and never claim a
synthetic placeholder fixture is a real trade example.
"""

from __future__ import annotations

import ast
from pathlib import Path

import options_manager.validation.base as validation_base_module
import options_manager.validation.fixtures as validation_fixtures_module
from options_manager.validation import (
    RealSetupValidationSummary,
    build_real_setup_validation_dataset,
    classify_real_setup_outcome,
    run_real_setup_validation_dataset,
    summarize_real_setup_validation_dataset,
)

_SCANNED_MODULES = (validation_base_module, validation_fixtures_module)

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

_EXPECTED_CASE_NAMES = {
    "complete_real_like_triggered_winner",
    "complete_real_like_triggered_loser",
    "rejected_missing_context",
    "rejected_missing_contract",
    "incomplete_data_fail_closed",
    "bac_partial_real_manual_pending",
}


# --- fixture dataset contains expected named cases ----------------------------------------------


def test_fixture_dataset_contains_expected_named_cases():
    cases = build_real_setup_validation_dataset()
    assert set(cases.keys()) == _EXPECTED_CASE_NAMES
    assert len(cases) == 6


def test_fixture_helpers_do_not_mutate_state_between_calls():
    dataset_1 = build_real_setup_validation_dataset()
    dataset_2 = build_real_setup_validation_dataset()
    assert dataset_1 == dataset_2
    assert dataset_1 is not dataset_2


# --- no fixture claims to be real if values are synthetic/placeholders --------------------------


def test_no_current_fixture_claims_complete_real_status():
    # No case supplied so far is a complete, user-supplied real trade --
    # each is either a synthetic placeholder or an honestly-partial real
    # seed. Neither may claim to be "user_supplied" (complete real).
    cases = build_real_setup_validation_dataset()
    for name, fixture in cases.items():
        assert fixture.provenance in ("placeholder", "partial_real"), (
            f"{name} must be marked provenance='placeholder' or "
            f"'partial_real' -- no complete real historical setup has "
            f"been supplied yet"
        )


def test_placeholder_fixtures_are_marked_placeholder_in_notes():
    cases = build_real_setup_validation_dataset()
    for name, fixture in cases.items():
        if fixture.provenance == "placeholder":
            assert "PLACEHOLDER" in fixture.notes, f"{name} notes must say PLACEHOLDER"


def test_partial_real_fixture_is_marked_partial_not_complete():
    cases = build_real_setup_validation_dataset()
    bac = cases["bac_partial_real_manual_pending"]
    assert bac.provenance == "partial_real"
    assert "PARTIAL_REAL" in bac.notes
    assert "MANUAL_PENDING" in bac.actual_outcome_notes


def test_no_fixture_id_or_ticker_implies_real_data():
    cases = build_real_setup_validation_dataset()
    recognized_markers = ("real_like", "rejected", "incomplete", "partial_real", "manual_pending")
    for fixture in cases.values():
        assert any(marker in fixture.id for marker in recognized_markers)
        assert fixture.id != "real" and not fixture.id.startswith("actual_")


# --- complete cases produce stable scanner outputs -----------------------------------------------


def test_complete_cases_produce_stable_scanner_outputs():
    entries_1 = run_real_setup_validation_dataset()
    entries_2 = run_real_setup_validation_dataset()
    assert entries_1 == entries_2

    winner = entries_1["complete_real_like_triggered_winner"]
    assert winner.scan_status == "TRIGGERED"
    assert winner.classification == "valid_triggered_winner"

    loser = entries_1["complete_real_like_triggered_loser"]
    assert loser.scan_status == "TRIGGERED"
    assert loser.classification == "valid_triggered_loser"


# --- missing context / contract / entry all fail closed -------------------------------------------


def test_missing_context_fails_closed():
    entries = run_real_setup_validation_dataset()
    entry = entries["rejected_missing_context"]
    assert entry.scan_status == "INVALID"
    assert "market_context" in entry.reason_code or entry.reason_code == "missing_market_context"


def test_missing_contract_fails_closed():
    entries = run_real_setup_validation_dataset()
    entry = entries["rejected_missing_contract"]
    assert entry.scan_status == "INVALID"
    assert (
        "contract_constraints" in entry.reason_code
        or entry.reason_code == "missing_contract_constraints"
    )


def test_incomplete_data_fails_closed():
    entries = run_real_setup_validation_dataset()
    entry = entries["incomplete_data_fail_closed"]
    assert entry.scan_status == "INVALID"
    assert entry.reason_code == "missing_entry_trigger"


# --- BAC partial-real fixture: missing bars, NOT_RUN, no fabrication -------------------------------


def test_bac_partial_real_fixture_can_be_represented_with_missing_bar_fields():
    cases = build_real_setup_validation_dataset()
    bac = cases["bac_partial_real_manual_pending"]
    assert bac.ticker == "BAC"
    assert bac.two_bars_back_type is None
    assert bac.two_bars_back_high is None
    assert bac.two_bars_back_low is None
    assert bac.previous_high is None
    assert bac.previous_low is None
    assert bac.current_high is None
    assert bac.current_low is None


def test_bac_partial_real_fixture_preserves_real_stored_signa_gex_fragments():
    cases = build_real_setup_validation_dataset()
    bac = cases["bac_partial_real_manual_pending"]
    assert bac.market_context_inputs is not None
    assert bac.market_context_inputs.signa_grade == "B"
    assert bac.market_context_inputs.signa_score == 78.0
    assert bac.market_context_inputs.signa_direction == "bullish"
    # LOW_PINNING is not a valid GexRegime value in this system -- it must
    # never be force-mapped onto gex_regime; it is quoted as raw reference
    # text in the notes only.
    assert bac.market_context_inputs.gex_regime is None
    assert "LOW_PINNING" in bac.actual_outcome_notes


def test_bac_partial_real_fixture_returns_not_run_due_to_missing_candle_data():
    entries = run_real_setup_validation_dataset()
    entry = entries["bac_partial_real_manual_pending"]
    assert entry.scan_status == "NOT_RUN"
    assert entry.reason_code == "missing_candle_data"


def test_not_run_is_separate_from_scanner_verdict_vocabulary():
    entries = run_real_setup_validation_dataset()
    entry = entries["bac_partial_real_manual_pending"]
    assert entry.scan_status not in ("TRIGGERED", "WATCH", "INVALID", "NO_TRADE")


def test_bac_partial_real_fixture_actual_outcome_is_unknown_manual_pending():
    cases = build_real_setup_validation_dataset()
    bac = cases["bac_partial_real_manual_pending"]
    assert bac.actual_outcome == "unknown"
    assert "MANUAL_PENDING" in bac.actual_outcome_notes


def test_complete_fixtures_still_run_normally_when_a_not_run_fixture_is_present():
    entries = run_real_setup_validation_dataset()
    winner = entries["complete_real_like_triggered_winner"]
    loser = entries["complete_real_like_triggered_loser"]
    assert winner.scan_status == "TRIGGERED"
    assert loser.scan_status == "TRIGGERED"
    # The NOT_RUN fixture must not have been silently included in the
    # rows handed to the scanner, and must not affect other fixtures'
    # own results.
    assert entries["bac_partial_real_manual_pending"].scan_status == "NOT_RUN"


def test_not_run_fixture_is_excluded_from_rows_built_for_the_scanner():
    cases = build_real_setup_validation_dataset()
    runnable = [
        name for name, fx in cases.items() if validation_fixtures_module._bars_complete(fx)
    ]
    assert "bac_partial_real_manual_pending" not in runnable
    for name in ("complete_real_like_triggered_winner", "complete_real_like_triggered_loser"):
        assert name in runnable


# --- actual outcome labels are stored separately from scanner verdict -----------------------------


def test_actual_outcome_is_stored_separately_from_scan_status():
    entries = run_real_setup_validation_dataset()
    for entry in entries.values():
        # scan_status/reason_code come only from the scanner; actual_outcome
        # comes only from the fixture's own recorded result -- neither field
        # is derived from the other.
        assert entry.scan_status in ("TRIGGERED", "WATCH", "INVALID", "NO_TRADE", "NOT_RUN")
        assert entry.actual_outcome in (
            "hit_target_1",
            "hit_target_2",
            "hit_stop",
            "no_resolution",
            "unknown",
        )


def test_scanner_never_receives_actual_outcome_fields():
    # The row builder in fixtures.py must translate only the setup-packet
    # fields -- confirm the translation function's source never references
    # the fixture's own outcome attributes.
    source = Path(validation_fixtures_module.__file__).read_text()
    build_row_start = source.index("def _build_watchlist_row(")
    build_row_end = source.index("\ndef ", build_row_start + 1)
    build_row_source = source[build_row_start:build_row_end]
    assert "actual_outcome" not in build_row_source
    assert "human_classification_override" not in build_row_source


# --- classification helper is a pure function of its explicit inputs -----------------------------


def test_classification_helper_is_deterministic_and_explicit_wins():
    assert (
        classify_real_setup_outcome(scan_status="TRIGGERED", actual_outcome="hit_target_1")
        == "valid_triggered_winner"
    )
    assert (
        classify_real_setup_outcome(scan_status="TRIGGERED", actual_outcome="hit_stop")
        == "valid_triggered_loser"
    )
    assert (
        classify_real_setup_outcome(scan_status="TRIGGERED", actual_outcome="no_resolution")
        == "valid_no_follow_through"
    )
    assert (
        classify_real_setup_outcome(scan_status="INVALID", actual_outcome="hit_stop")
        == "rejected_correctly"
    )
    assert (
        classify_real_setup_outcome(scan_status="INVALID", actual_outcome="hit_target_1")
        == "rejected_incorrectly"
    )
    assert (
        classify_real_setup_outcome(scan_status="NO_TRADE", actual_outcome="hit_target_2")
        == "rejected_incorrectly"
    )
    assert (
        classify_real_setup_outcome(scan_status="WATCH", actual_outcome="hit_target_1")
        == "unclassified"
    )
    # Explicit override always wins, regardless of status/outcome.
    assert (
        classify_real_setup_outcome(
            scan_status="INVALID",
            actual_outcome="hit_stop",
            human_classification_override="false_negative",
        )
        == "false_negative"
    )


# --- summary rollup ---------------------------------------------------------------------------------


def test_summary_counts_are_stable():
    summary_1 = summarize_real_setup_validation_dataset()
    summary_2 = summarize_real_setup_validation_dataset()
    assert isinstance(summary_1, RealSetupValidationSummary)
    assert summary_1 == summary_2
    assert summary_1.total_cases == 6
    assert summary_1.placeholder_cases == 5
    assert summary_1.partial_real_cases == 1
    assert summary_1.user_supplied_cases == 0
    assert sum(summary_1.counts_by_classification.values()) == 6
    assert sum(summary_1.counts_by_scan_status.values()) == 6


# --- structural safety (matches this buildout's established pattern) -----------------------------


def _imported_modules(module) -> list[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def test_validation_modules_have_no_forbidden_imports():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in name, f"{module.__name__} must not import {name!r}"


def test_validation_modules_have_no_cross_boundary_imports_outside_options_manager():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        outside = [
            name
            for name in imported
            if not name.startswith("options_manager")
            and name not in ("__future__", "dataclasses", "typing")
        ]
        assert not outside, f"{module.__name__} has an unexpected import: {outside}"


def test_validation_modules_have_no_credential_identifiers():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text().lower()
        for forbidden in _FORBIDDEN_CREDENTIAL_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_validation_modules_have_no_order_action_verbs():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in _FORBIDDEN_ORDER_ACTION_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_validation_modules_do_not_mutate_live_options_flag():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        assert "LIVE_OPTIONS_TRADING_ENABLED" not in source


def test_validation_modules_do_not_read_or_write_files():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in ("open(", ".write(", ".write_text(", ".write_bytes(", ".read_text("):
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"
