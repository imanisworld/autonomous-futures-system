"""
tests/test_options_fixture_status.py

options_manager/validation/fixture_status.py tests. Proves the fixture-
status vocabulary and static candidate inventory represent real trade
candidates without conflating real-trade status, management quality, and
scanner-proof readiness -- and that nothing in the scanner, execution, or
broker paths depends on this labeling/reporting layer.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import options_manager.validation.fixture_status as fixture_status_module
from options_manager.validation import (
    FixtureCandidate,
    FixtureCandidateSummary,
    FixtureStatus,
    build_fixture_candidate_inventory,
    summarize_fixture_candidate_inventory,
)

_SCANNED_MODULES = (fixture_status_module,)

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

_FORBIDDEN_SCANNER_IDENTIFIERS = (
    "scan_watchlist_strat_212",
    "evaluate_strat_212",
    "WatchlistRow",
    "Strat212Bars",
)

_EXPECTED_TICKERS = {
    "HOOD",
    "EBAY",
    "AMD",
    "ORCL",
    "FITB",
    "BAC",
    "SPXW",
    "NVDA",
    "NOK",
    "ADP",
    "ARM",
    "QCOM",
}

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _module_source() -> str:
    return Path(fixture_status_module.__file__).read_text()


def _imported_modules(module) -> list[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


# --- 1. enum round-trips and inventory shape --------------------------------------------------------


def test_fixture_status_round_trips_from_string():
    for status in FixtureStatus:
        assert FixtureStatus(status.value) is status


def test_fixture_status_is_json_serializable_as_plain_string():
    for status in FixtureStatus:
        assert json.loads(json.dumps(status)) == status.value


def test_inventory_contains_expected_tickers():
    inventory = build_fixture_candidate_inventory()
    assert set(inventory.keys()) == _EXPECTED_TICKERS
    assert len(inventory) == 12


def test_inventory_helper_does_not_mutate_state_between_calls():
    inventory_1 = build_fixture_candidate_inventory()
    inventory_2 = build_fixture_candidate_inventory()
    assert inventory_1 == inventory_2
    assert inventory_1 is not inventory_2


def test_all_candidates_are_real_construct_objects():
    inventory = build_fixture_candidate_inventory()
    for ticker, candidate in inventory.items():
        assert isinstance(candidate, FixtureCandidate), ticker
        assert isinstance(candidate.status, FixtureStatus), ticker


# --- 2. every candidate carries real evidence, not a bare label --------------------------------------


def test_every_candidate_has_confirmed_or_missing_proof():
    inventory = build_fixture_candidate_inventory()
    for ticker, candidate in inventory.items():
        assert candidate.proof_confirmed or candidate.proof_missing, ticker


def test_every_non_clean_candidate_has_a_reason():
    inventory = build_fixture_candidate_inventory()
    for ticker, candidate in inventory.items():
        if candidate.status != FixtureStatus.CLEAN_COMPLETE_FIXTURE:
            assert candidate.reason_not_first_proof, ticker


def test_no_candidate_is_clean_complete_fixture_yet():
    """As of Increment 25B, nothing has cleared the bar to be a first
    scanner-identification proof fixture."""
    inventory = build_fixture_candidate_inventory()
    assert not any(
        candidate.status == FixtureStatus.CLEAN_COMPLETE_FIXTURE
        for candidate in inventory.values()
    )


# --- 3. specific status assignments match the current board -----------------------------------------


def test_hood_remains_pending_proof_not_promoted():
    candidate = build_fixture_candidate_inventory()["HOOD"]
    assert candidate.status == FixtureStatus.PENDING_PROOF_FIXTURE
    assert candidate.promotion_requirements


def test_ebay_is_special_case_and_cites_pr_221_correction():
    candidate = build_fixture_candidate_inventory()["EBAY"]
    assert candidate.status == FixtureStatus.SPECIAL_CASE_FIXTURE
    assert "PR #221" in candidate.best_future_use or any(
        "PR #221" in item for item in candidate.proof_confirmed
    )


def test_nok_is_management_case_and_cites_pr_221_correction():
    candidate = build_fixture_candidate_inventory()["NOK"]
    assert candidate.status == FixtureStatus.MANAGEMENT_CASE
    assert any("PR #221" in item for item in candidate.proof_confirmed)


def test_amd_is_special_case():
    candidate = build_fixture_candidate_inventory()["AMD"]
    assert candidate.status == FixtureStatus.SPECIAL_CASE_FIXTURE


def test_adp_arm_qcom_remain_reject():
    inventory = build_fixture_candidate_inventory()
    for ticker in ("ADP", "ARM", "QCOM"):
        assert inventory[ticker].status == FixtureStatus.REJECT, ticker


def test_orcl_bac_are_incomplete():
    inventory = build_fixture_candidate_inventory()
    for ticker in ("ORCL", "BAC"):
        assert inventory[ticker].status == FixtureStatus.INCOMPLETE, ticker


def test_fitb_is_special_case_not_clean():
    """As of Increment 25F, the FITB $50C trade is broker-verified and
    candle-reconstructed, but the same-day invalidation breach followed
    by a +30% MFE recovery disqualifies it from a clean loser fixture."""
    candidate = build_fixture_candidate_inventory()["FITB"]
    assert candidate.status == FixtureStatus.SPECIAL_CASE_FIXTURE
    assert candidate.status != FixtureStatus.CLEAN_COMPLETE_FIXTURE


def test_fitb_best_future_use_points_to_management_case():
    candidate = build_fixture_candidate_inventory()["FITB"]
    assert "management case" in candidate.best_future_use.lower()


def test_fitb_reason_cites_same_day_invalidation_and_mfe():
    candidate = build_fixture_candidate_inventory()["FITB"]
    reason = candidate.reason_not_first_proof
    assert "same-day" in reason.lower()
    assert "invalidation" in reason.lower()
    assert "30%" in reason


def test_bac_remains_not_clean_pending_candle_reconstruction():
    candidate = build_fixture_candidate_inventory()["BAC"]
    assert candidate.status != FixtureStatus.CLEAN_COMPLETE_FIXTURE
    assert "BROKER_VERIFIED_LOSER" in candidate.notes
    assert "PENDING_CANDLE_RECONSTRUCTION" in candidate.notes


def test_hood_70p_not_conflated_with_hood_100c():
    candidate = build_fixture_candidate_inventory()["HOOD"]
    notes = candidate.notes
    assert "$70P" in notes
    assert "$100C" in notes
    assert any(word in notes.lower() for word in ("separate", "unrelated", "distinct"))


def test_spxw_nvda_are_scalp_noise():
    inventory = build_fixture_candidate_inventory()
    for ticker in ("SPXW", "NVDA"):
        assert inventory[ticker].status == FixtureStatus.SCALP_NOISE, ticker


def test_orcl_documents_absence_of_signa_minervini_scoring():
    candidate = build_fixture_candidate_inventory()["ORCL"]
    joined = " ".join(candidate.proof_missing).lower()
    assert "minervini" in joined
    assert "signa" in joined


# --- 4. summary rollup is deterministic and matches the dataset --------------------------------------


def test_summary_counts_match_inventory():
    inventory = build_fixture_candidate_inventory()
    summary = summarize_fixture_candidate_inventory(inventory)
    assert isinstance(summary, FixtureCandidateSummary)
    assert summary.total_candidates == 12
    assert sum(summary.counts_by_status.values()) == 12
    for status_value, count in summary.counts_by_status.items():
        assert count == sum(
            1 for c in inventory.values() if c.status.value == status_value
        )


def test_summary_defaults_to_building_its_own_inventory():
    default_summary = summarize_fixture_candidate_inventory()
    explicit_summary = summarize_fixture_candidate_inventory(
        build_fixture_candidate_inventory()
    )
    assert default_summary == explicit_summary


# --- 5. status field has no default (always an explicit human call) ---------------------------------


def test_status_field_has_no_default_value():
    import dataclasses

    fields_by_name = {f.name: f for f in dataclasses.fields(FixtureCandidate)}
    status_field = fields_by_name["status"]
    assert status_field.default is dataclasses.MISSING
    assert status_field.default_factory is dataclasses.MISSING


def test_constructing_a_candidate_without_status_fails():
    import pytest

    with pytest.raises(TypeError):
        FixtureCandidate(
            ticker="TEST",
            window="unknown",
            best_future_use="n/a",
        )


# --- 6. no scanner import or scanner identifiers in this module --------------------------------------


def test_fixture_status_module_has_no_scanner_import():
    imported = _imported_modules(fixture_status_module)
    assert not any("options_manager.scanner" in name for name in imported)
    assert not any(name == "options_manager.strategies" for name in imported)


def test_fixture_status_module_has_no_scanner_identifiers():
    source = _module_source()
    for forbidden in _FORBIDDEN_SCANNER_IDENTIFIERS:
        assert forbidden not in source, f"module must not contain {forbidden!r}"


# --- 7. no broker/execution/config/network/MCP/credential/file-I/O identifiers -----------------------


def test_fixture_status_module_has_no_forbidden_imports():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in name, f"{module.__name__} must not import {name!r}"


def test_fixture_status_module_has_no_cross_boundary_imports_outside_options_manager():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        outside = [
            name
            for name in imported
            if not name.startswith("options_manager")
            and name not in ("__future__", "dataclasses", "enum", "typing")
        ]
        assert not outside, f"{module.__name__} has an unexpected import: {outside}"


def test_fixture_status_module_has_no_credential_identifiers():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text().lower()
        for forbidden in _FORBIDDEN_CREDENTIAL_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_fixture_status_module_has_no_order_action_verbs():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in _FORBIDDEN_ORDER_ACTION_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_fixture_status_module_does_not_mutate_live_options_flag():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        assert "LIVE_OPTIONS_TRADING_ENABLED" not in source


def test_fixture_status_module_does_not_read_or_write_files():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in ("open(", ".write(", ".write_text(", ".write_bytes(", ".read_text("):
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


# --- 8. nothing in scanner/execution/broker paths imports this module --------------------------------


def test_no_scanner_execution_or_broker_module_imports_fixture_status():
    scanned_dirs = [
        _REPO_ROOT / "options_manager" / "scanner",
        _REPO_ROOT / "execution",
        _REPO_ROOT / "webhook",
    ]
    offenders = []
    for directory in scanned_dirs:
        if not directory.is_dir():
            continue
        for path in directory.rglob("*.py"):
            text = path.read_text()
            if "fixture_status" in text:
                offenders.append(str(path))
    assert not offenders, f"fixture_status must not be referenced from: {offenders}"
