"""
tests/test_options_management_cases.py

options_manager/validation/management_cases.py tests. Proves the new
trade-management validation layer represents real, human-labeled
position-management decisions (hold/trim/exit, sizing, thesis status)
without ever touching the setup-scanner path, without fabricating any
missing field, and without silently merging a blended multi-trade
history into one case.
"""

from __future__ import annotations

import ast
from pathlib import Path

import options_manager.validation.management_cases as management_cases_module
from options_manager.validation import (
    ManagementCase,
    ManagementCaseSummary,
    build_management_case_dataset,
    summarize_management_case_dataset,
)

_SCANNED_MODULES = (management_cases_module,)

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

_EXPECTED_CASE_NAMES = {
    "nok_management_case",
    "ebay_management_case",
    "adp_management_case",
    "arm_management_case",
}


def _module_source() -> str:
    return Path(management_cases_module.__file__).read_text()


def _imported_modules(module) -> list[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


# --- 1. all four cases construct with real known fields only --------------------------------------


def test_dataset_contains_expected_named_cases():
    cases = build_management_case_dataset()
    assert set(cases.keys()) == _EXPECTED_CASE_NAMES
    assert len(cases) == 4


def test_all_cases_are_real_construct_objects():
    cases = build_management_case_dataset()
    for name, case in cases.items():
        assert isinstance(case, ManagementCase), name


def test_nok_case_reflects_real_known_fields():
    case = build_management_case_dataset()["nok_management_case"]
    assert case.ticker == "NOK"
    assert case.contract_strike == 14.0
    assert case.entry_premium == 1.27
    assert case.exit_premium == 1.02
    assert case.position_size_contracts == 3
    assert case.decision_type == "full_exit"
    assert case.decision_basis == "emotional"
    assert case.thesis_status_at_decision == "intact"
    assert case.classification == "premature_exit_thesis_intact"


def test_ebay_case_reflects_real_known_fields():
    case = build_management_case_dataset()["ebay_management_case"]
    assert case.ticker == "EBAY"
    assert case.contract_strike == 105.0
    assert case.entry_premium == 1.06
    assert case.position_size_contracts == 4
    assert case.decision_type == "full_exit"
    assert case.decision_basis == "rule_based"
    assert case.thesis_status_at_decision == "intact"
    assert case.position_sizing == "defined_risk"
    assert case.realized_pnl_dollars == 538.0 + 470.0 + 455.0 + 421.0
    assert case.classification == "correct_rule_based_hold_or_scale"


def test_adp_case_reflects_real_known_fields():
    case = build_management_case_dataset()["adp_management_case"]
    assert case.ticker == "ADP"
    assert case.position_size_contracts == 8
    assert case.decision_type == "hold"
    assert case.decision_basis == "no_rule_defined"
    assert case.position_sizing == "oversized"
    assert case.realized_pnl_percent == -93.0
    assert case.classification == "oversized_no_exit_rule"


def test_arm_case_reflects_real_known_fields():
    case = build_management_case_dataset()["arm_management_case"]
    assert case.ticker == "ARM"
    assert case.contract_strike == 215.0
    assert case.decision_type == "full_exit"
    assert case.decision_basis == "external_recommendation"
    assert case.thesis_status_at_decision == "intact"
    assert case.realized_pnl_dollars == 624.0
    assert case.classification == "premature_exit_thesis_intact"


def test_dataset_helper_does_not_mutate_state_between_calls():
    dataset_1 = build_management_case_dataset()
    dataset_2 = build_management_case_dataset()
    assert dataset_1 == dataset_2
    assert dataset_1 is not dataset_2


# --- 2. missing optional fields remain None --------------------------------------------------------


def test_adp_case_leaves_unknown_fields_none_not_fabricated():
    case = build_management_case_dataset()["adp_management_case"]
    assert case.entry_premium is None
    assert case.exit_premium is None
    assert case.realized_pnl_dollars is None
    assert case.thesis_status_at_decision == "unknown"


def test_arm_case_leaves_unknown_fields_none_not_fabricated():
    case = build_management_case_dataset()["arm_management_case"]
    assert case.contract_expiration is None
    assert case.entry_premium is None
    assert case.exit_premium is None
    assert case.position_size_contracts is None
    assert case.realized_pnl_percent is None


def test_nok_case_has_no_percent_pnl_only_dollar_pnl_known():
    case = build_management_case_dataset()["nok_management_case"]
    assert case.realized_pnl_percent is None


# --- 3. AMZN-shaped blended case is rejected / not included ----------------------------------------


def test_amzn_blended_case_is_not_included():
    cases = build_management_case_dataset()
    assert not any("amzn" in name.lower() for name in cases)
    assert not any(case.ticker == "AMZN" for case in cases.values())


def test_module_documents_why_amzn_is_excluded():
    source = _module_source().lower()
    assert "amzn" in source
    assert "blend" in source or "decompos" in source


# --- 4. classification is required (no default exists on the model) -------------------------------


def test_classification_field_has_no_default_value():
    import dataclasses

    fields_by_name = {f.name: f for f in dataclasses.fields(ManagementCase)}
    classification_field = fields_by_name["classification"]
    assert classification_field.default is dataclasses.MISSING
    assert classification_field.default_factory is dataclasses.MISSING


def test_constructing_a_case_without_classification_fails():
    import pytest

    with pytest.raises(TypeError):
        ManagementCase(
            id="incomplete",
            ticker="TEST",
            direction="CALL",
            provenance="placeholder",
            decision_type="hold",
            decision_basis="rule_based",
            thesis_status_at_decision="intact",
            position_sizing="defined_risk",
        )


# --- 5. no scanner imports or scanner call path exists ---------------------------------------------


def test_management_cases_module_has_no_scanner_import():
    imported = _imported_modules(management_cases_module)
    assert not any("options_manager.scanner" in name for name in imported)
    assert not any(name == "options_manager.strategies" for name in imported)


def test_management_cases_module_has_no_scanner_identifiers():
    source = _module_source()
    for forbidden in _FORBIDDEN_SCANNER_IDENTIFIERS:
        assert forbidden not in source, f"module must not contain {forbidden!r}"


# --- 6. no broker/execution/config/network/MCP/credential/file-I/O identifiers ---------------------


def test_management_cases_module_has_no_forbidden_imports():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in name, f"{module.__name__} must not import {name!r}"


def test_management_cases_module_has_no_cross_boundary_imports_outside_options_manager():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        outside = [
            name
            for name in imported
            if not name.startswith("options_manager")
            and name not in ("__future__", "dataclasses", "typing")
        ]
        assert not outside, f"{module.__name__} has an unexpected import: {outside}"


def test_management_cases_module_has_no_credential_identifiers():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text().lower()
        for forbidden in _FORBIDDEN_CREDENTIAL_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_management_cases_module_has_no_order_action_verbs():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in _FORBIDDEN_ORDER_ACTION_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_management_cases_module_does_not_mutate_live_options_flag():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        assert "LIVE_OPTIONS_TRADING_ENABLED" not in source


def test_management_cases_module_does_not_read_or_write_files():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in ("open(", ".write(", ".write_text(", ".write_bytes(", ".read_text("):
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_management_cases_module_has_no_network_call_text():
    source = _module_source().lower()
    for forbidden in ("httpx.", "requests.", "socket."):
        assert forbidden not in source, f"module must not contain {forbidden!r}"


# --- 7. summary counts by classification and provenance are correct --------------------------------


def test_summary_counts_are_stable_and_correct():
    summary_1 = summarize_management_case_dataset()
    summary_2 = summarize_management_case_dataset()
    assert isinstance(summary_1, ManagementCaseSummary)
    assert summary_1 == summary_2
    assert summary_1.total_cases == 4
    assert summary_1.placeholder_cases == 0
    assert summary_1.partial_real_cases == 2  # ADP, ARM
    assert summary_1.user_supplied_cases == 2  # NOK, EBAY
    assert sum(summary_1.counts_by_classification.values()) == 4
    assert sum(summary_1.counts_by_decision_type.values()) == 4
    assert summary_1.counts_by_classification["premature_exit_thesis_intact"] == 2
    assert summary_1.counts_by_classification["correct_rule_based_hold_or_scale"] == 1
    assert summary_1.counts_by_classification["oversized_no_exit_rule"] == 1
