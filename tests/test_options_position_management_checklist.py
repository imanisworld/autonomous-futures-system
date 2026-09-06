"""
tests/test_options_position_management_checklist.py

Tests the advisory position-management checklist and its safety boundaries.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import options_manager.validation.position_management_checklist as position_management_checklist_module
from options_manager.validation.position_management_checklist import (
    ChecklistItemStatus,
    PositionAction,
    PositionManagementChecklistResult,
    PositionManagementInput,
    check_position_management_checklist_intake,
    evaluate_position_management_checklist,
)

_SCANNED_MODULES = (position_management_checklist_module,)

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

_FORBIDDEN_CREDENTIAL_IDENTIFIERS = (
    "api_key",
    "apikey",
    "credential",
    "secret",
    "password",
    "token",
)

_FORBIDDEN_ORDER_ACTION_IDENTIFIERS = (
    "place_order",
    "submit_order",
    "cancel_order",
    "replace_order",
    "execute_order",
    "live_order",
)


def _module_source() -> str:
    return Path(position_management_checklist_module.__file__).read_text()


def _imported_modules(module) -> list[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def _imported_modules_at_path(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
            modules.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def _clean_payload(**overrides) -> dict:
    payload = dict(
        ticker="ORCL",
        direction="CALL",
        strike=200.0,
        expiration="2026-08-21",
        dte=30,
        entry_premium=2.00,
        current_premium=2.50,
        contracts_held=2,
        underlying_spot=195.0,
        underlying_invalidation=185.0,
        target_1=210.0,
        target_2=220.0,
        thesis_status="intact",
        position_sizing="defined_risk",
        max_dollar_risk=300.0,
        current_dollar_risk=200.0,
        iv_event_risk="none",
        earnings_before_expiration=False,
        notes="",
    )
    payload.update(overrides)
    return payload


def test_clean_position_returns_continue_hold():
    result = check_position_management_checklist_intake(_clean_payload())
    assert isinstance(result, PositionManagementChecklistResult)
    assert result.action == PositionAction.CONTINUE_HOLD
    assert result.blocking_reasons == ()
    assert len(result.checklist_items) == 7


def test_thesis_broken_returns_exit_required():
    result = check_position_management_checklist_intake(_clean_payload(thesis_status="broken"))
    assert result.action == PositionAction.EXIT_REQUIRED


def test_call_invalidation_breach_returns_exit_required():
    result = check_position_management_checklist_intake(
        _clean_payload(underlying_spot=180.0, underlying_invalidation=185.0)
    )
    assert result.action == PositionAction.EXIT_REQUIRED


def test_put_invalidation_breach_is_direction_aware():
    result = check_position_management_checklist_intake(
        _clean_payload(
            direction="PUT",
            underlying_spot=210.0,
            underlying_invalidation=205.0,
            target_1=190.0,
            target_2=180.0,
        )
    )
    assert result.action == PositionAction.EXIT_REQUIRED


def test_thesis_broken_outranks_invalidation_intact():
    result = check_position_management_checklist_intake(
        _clean_payload(thesis_status="broken", underlying_spot=195.0, underlying_invalidation=185.0)
    )
    assert result.action == PositionAction.EXIT_REQUIRED
    thesis_item = next(i for i in result.checklist_items if i.name == "thesis_status")
    assert thesis_item.status == ChecklistItemStatus.FAIL


def test_oversized_position_returns_consider_trim():
    result = check_position_management_checklist_intake(_clean_payload(position_sizing="oversized"))
    assert result.action == PositionAction.CONSIDER_TRIM


def test_risk_over_cap_returns_consider_trim():
    result = check_position_management_checklist_intake(
        _clean_payload(current_dollar_risk=400.0, max_dollar_risk=300.0)
    )
    assert result.action == PositionAction.CONSIDER_TRIM


def test_sizing_failure_outranks_target_reached():
    result = check_position_management_checklist_intake(
        _clean_payload(position_sizing="oversized", underlying_spot=225.0)
    )
    assert result.action == PositionAction.CONSIDER_TRIM


def test_target_2_reached_returns_consider_exit():
    result = check_position_management_checklist_intake(_clean_payload(underlying_spot=225.0))
    assert result.action == PositionAction.CONSIDER_EXIT


def test_target_1_reached_without_target_2_returns_consider_trim():
    result = check_position_management_checklist_intake(_clean_payload(underlying_spot=212.0))
    assert result.action == PositionAction.CONSIDER_TRIM


def test_target_1_reached_with_no_target_2_supplied_returns_consider_trim():
    result = check_position_management_checklist_intake(
        _clean_payload(underlying_spot=212.0, target_2=None)
    )
    assert result.action == PositionAction.CONSIDER_TRIM


def test_earnings_with_high_event_risk_returns_consider_trim():
    result = check_position_management_checklist_intake(
        _clean_payload(earnings_before_expiration=True, iv_event_risk="high")
    )
    assert result.action == PositionAction.CONSIDER_TRIM


def test_earnings_flag_without_high_risk_does_not_trigger():
    result = check_position_management_checklist_intake(
        _clean_payload(earnings_before_expiration=True, iv_event_risk="low")
    )
    assert result.action == PositionAction.CONTINUE_HOLD


def test_low_dte_returns_consider_trim():
    result = check_position_management_checklist_intake(_clean_payload(dte=3))
    assert result.action == PositionAction.CONSIDER_TRIM


def test_dte_above_threshold_does_not_trigger():
    result = check_position_management_checklist_intake(_clean_payload(dte=6))
    assert result.action == PositionAction.CONTINUE_HOLD


def test_premium_decayed_past_threshold_returns_consider_trim():
    result = check_position_management_checklist_intake(
        _clean_payload(entry_premium=2.00, current_premium=1.49)
    )
    assert result.action == PositionAction.CONSIDER_TRIM
    assert "25%" in result.next_required_action


def test_premium_decay_under_threshold_does_not_trigger():
    result = check_position_management_checklist_intake(
        _clean_payload(entry_premium=2.00, current_premium=1.60)
    )
    assert result.action == PositionAction.CONTINUE_HOLD


def test_non_dict_payload_returns_structured_failure_not_exception():
    for bad_payload in (None, "not a dict", 42, ["a", "list"]):
        result = check_position_management_checklist_intake(bad_payload)
        assert isinstance(result, PositionManagementChecklistResult)
        assert result.action == PositionAction.EXIT_REQUIRED
        assert "malformed payload" in result.blocking_reasons[0]
        assert result.position is None


def test_missing_required_field_returns_exit_required_not_exception():
    payload = _clean_payload()
    del payload["underlying_invalidation"]
    result = check_position_management_checklist_intake(payload)
    assert result.action == PositionAction.EXIT_REQUIRED
    assert any("missing underlying_invalidation" in reason for reason in result.blocking_reasons)
    assert result.position is None


def test_invalid_direction_returns_exit_required_not_exception():
    result = check_position_management_checklist_intake(_clean_payload(direction="SIDEWAYS"))
    assert result.action == PositionAction.EXIT_REQUIRED
    assert result.position is None


def test_invalid_thesis_status_returns_exit_required_not_exception():
    result = check_position_management_checklist_intake(_clean_payload(thesis_status="who knows"))
    assert result.action == PositionAction.EXIT_REQUIRED
    assert result.position is None


def test_invalid_numeric_field_returns_exit_required_not_exception():
    result = check_position_management_checklist_intake(_clean_payload(strike="not-a-number"))
    assert result.action == PositionAction.EXIT_REQUIRED
    assert result.position is None


def test_invalid_target_2_returns_exit_required_not_exception():
    result = check_position_management_checklist_intake(_clean_payload(target_2="not-a-number"))
    assert result.action == PositionAction.EXIT_REQUIRED
    assert result.position is None


def test_evaluate_directly_on_typed_input():
    position = PositionManagementInput(**_clean_payload())
    result = evaluate_position_management_checklist(position)
    assert result.action == PositionAction.CONTINUE_HOLD
    assert result.position is position


def test_result_has_no_order_action_fields():
    field_names = {f.name for f in dataclasses.fields(PositionManagementChecklistResult)}
    for forbidden in ("order", "order_id", "ticket", "submit", "place", "execute", "broker_order"):
        assert forbidden not in field_names


def test_module_has_no_order_action_verbs():
    source = _module_source()
    for forbidden in _FORBIDDEN_ORDER_ACTION_IDENTIFIERS:
        assert forbidden not in source, f"module must not contain {forbidden!r}"


def test_module_has_no_scanner_import():
    imported = _imported_modules(position_management_checklist_module)
    assert not any("options_manager.scanner" in name for name in imported)
    assert not any(name == "options_manager.strategies" for name in imported)


def test_module_has_no_forbidden_imports():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in name, f"{module.__name__} must not import {name!r}"


def test_module_has_no_cross_boundary_imports_outside_options_manager():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        outside = [
            name
            for name in imported
            if not name.startswith("options_manager")
            and name not in ("__future__", "dataclasses", "enum", "typing")
        ]
        assert not outside, f"{module.__name__} has an unexpected import: {outside}"


def test_module_has_no_credential_identifiers():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text().lower()
        for forbidden in _FORBIDDEN_CREDENTIAL_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_module_does_not_read_or_write_files():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in ("open(", ".write(", ".write_text(", ".write_bytes(", ".read_text("):
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_module_has_no_network_call_text():
    source = _module_source().lower()
    for forbidden in ("httpx.", "requests.", "socket."):
        assert forbidden not in source, f"module must not contain {forbidden!r}"


def test_module_has_no_clock_access():
    source = _module_source()
    for forbidden in ("datetime.now(", "time.time(", "date.today("):
        assert forbidden not in source, f"module must not contain {forbidden!r}"


def test_no_scanner_execution_or_broker_module_imports_position_management_checklist():
    repo_root = Path(__file__).resolve().parent.parent
    scanned_dirs = [
        repo_root / "options_manager" / "scanner",
        repo_root / "execution",
        repo_root / "webhook",
    ]
    offenders = []
    for directory in scanned_dirs:
        if not directory.is_dir():
            continue
        for path in directory.rglob("*.py"):
            imported = _imported_modules_at_path(path)
            if any("position_management_checklist" in name for name in imported):
                offenders.append(str(path))
    assert not offenders, f"position_management_checklist must not be imported from: {offenders}"
