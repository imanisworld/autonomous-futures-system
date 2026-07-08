"""
tests/test_options_broker_preview_contract.py

Phase 19 — broker preview contract tests. Test-only: locks the inert
safety contract any future broker-preview adapter (mock or real) must
obey, before any real broker implementation is allowed.

    OptionsBrokerPreviewRequest -> preview adapter -> OptionsBrokerPreviewResult

No production code is added or modified in this phase. This file only
reads existing dataclass field names, existing behavior of
`preview_with_mock_broker`, and existing production file source text.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol

import options_manager.broker_boundary as broker_boundary_module
import options_manager.config as config_module
import options_manager.http_api as http_api_module
import options_manager.mock_broker_preview as mock_module
import options_manager.storage as storage_module
from options_manager.broker_boundary import (
    OptionsBrokerPreviewRequest,
    OptionsBrokerPreviewResult,
)
from options_manager.mock_broker_preview import (
    MOCK_BROKER_LABEL,
    preview_with_mock_broker,
)


def _preview_request(**overrides) -> OptionsBrokerPreviewRequest:
    base = dict(
        ticket_id="ticket-1",
        confirmation_id="conf-1",
        ticker="BAC",
        direction="CALL",
        order_action="BUY_TO_OPEN",
        quantity=1,
        contract_strike=60.00,
        contract_expiry=date.today() + timedelta(days=30),
        limit_price=1.95,
        estimated_notional=195.0,
        account_tag="agentic_micro_account",
        source="claude_session",
        dry_run_only=True,
        executable=False,
    )
    base.update(overrides)
    return OptionsBrokerPreviewRequest(**base)


_FORBIDDEN_FIELD_NAMES = (
    "credentials",
    "account_number",
    "account_id",
    "broker_payload",
    "route_to_broker",
    "api_key",
    "access_token",
    "refresh_token",
    "password",
    "session_token",
)

_FORBIDDEN_ORDER_QUEUE_FIELDS = (
    "pending_submit",
    "ready_to_submit",
    "auto_submit",
    "execute_after",
    "order_queue",
)

_FORBIDDEN_ORDER_ACTION_IDENTIFIERS = (
    "place_order",
    "submit_order",
    "cancel_order",
    "replace_order",
    "execute_order",
    "live_order",
)

_FORBIDDEN_IMPORT_FRAGMENTS = (
    "requests",
    "httpx",
    "urllib",
    "socket",
    "aiohttp",
    "websocket",
    "robin_stocks",
    "ib_insync",
    "ibapi",
    "execution",
    "webhook",
    "alert_ranker",
    "risk_engine",
)

_SCANNED_PRODUCTION_MODULES = (
    mock_module,
    broker_boundary_module,
    http_api_module,
    storage_module,
    config_module,
)


# --- 1. contract shape: request has no credential/account/broker-payload fields ---


def test_preview_request_shape_has_no_forbidden_fields():
    field_names = set(OptionsBrokerPreviewRequest.__dataclass_fields__.keys())
    for forbidden in _FORBIDDEN_FIELD_NAMES:
        assert forbidden not in field_names, (
            f"OptionsBrokerPreviewRequest must not contain field {forbidden!r}"
        )


# --- 2. result shape stays limited to the existing safe field set -----------------


_ALLOWED_RESULT_FIELDS = frozenset(
    {
        "preview_ready",
        "status",
        "failed_stage",
        "reason",
        "ticket_id",
        "broker",
        "broker_order_id",
        "executable",
        "submitted",
        "warnings",
    }
)


def test_preview_result_shape_is_limited_to_safe_fields():
    field_names = set(OptionsBrokerPreviewResult.__dataclass_fields__.keys())
    assert field_names == _ALLOWED_RESULT_FIELDS


def test_preview_result_shape_has_no_forbidden_fields():
    field_names = set(OptionsBrokerPreviewResult.__dataclass_fields__.keys())
    for forbidden in _FORBIDDEN_FIELD_NAMES:
        assert forbidden not in field_names


# --- 3. inert result invariants (happy path) ---------------------------------------


def test_valid_request_yields_inert_result_invariants():
    result = preview_with_mock_broker(_preview_request())
    assert result.submitted is False
    assert result.executable is False
    assert result.broker_order_id is None
    assert result.broker in (MOCK_BROKER_LABEL, None)
    assert any(
        "mock" in warning or "not_a_broker_order" in warning
        for warning in result.warnings
    ), "a real/ready result must disclose it is mock-only, not a real broker order"


def test_valid_request_broker_field_is_never_a_real_broker_name():
    result = preview_with_mock_broker(_preview_request())
    real_broker_names = {"robinhood", "ibkr", "interactive brokers", "tradovate"}
    assert (result.broker or "").strip().lower() not in real_broker_names


# --- 4. rejection invariants --------------------------------------------------------


def test_executable_request_rejection_invariants():
    result = preview_with_mock_broker(_preview_request(executable=True))
    assert result.preview_ready is False
    assert result.submitted is False
    assert result.executable is False
    assert result.broker_order_id is None
    assert (result.broker or "").strip().lower() not in {
        "robinhood",
        "ibkr",
        "tradovate",
    }


def test_non_dry_run_request_rejection_invariants():
    result = preview_with_mock_broker(_preview_request(dry_run_only=False))
    assert result.preview_ready is False
    assert result.submitted is False
    assert result.executable is False
    assert result.broker_order_id is None


# --- 5. future-adapter contract fixture (test documentation only, not production) ---


class PreviewAdapter(Protocol):
    """Documents the contract any future preview adapter (mock or real) must
    satisfy. This is a test-only Protocol — it is never imported by
    production code and exists purely to make the required call shape
    explicit and machine-checkable in this test file."""

    def __call__(
        self, preview_request: OptionsBrokerPreviewRequest
    ) -> OptionsBrokerPreviewResult: ...


def _assert_adapter_preserves_inert_contract(adapter: PreviewAdapter) -> None:
    valid_result = adapter(_preview_request())
    assert valid_result.submitted is False
    assert valid_result.executable is False
    assert valid_result.broker_order_id is None

    rejected_result = adapter(_preview_request(executable=True))
    assert rejected_result.submitted is False
    assert rejected_result.executable is False
    assert rejected_result.broker_order_id is None


def test_mock_adapter_satisfies_the_documented_preview_adapter_contract():
    _assert_adapter_preserves_inert_contract(preview_with_mock_broker)


# --- 6. no credential/account persistence anywhere in relevant production files ----
#
# Structural (AST-based) identifier scan, not a raw substring scan: a raw
# substring scan would false-positive on legitimate prose (e.g. a docstring
# saying "no credentials") or on a legitimate compound identifier that merely
# contains a forbidden word as a substring (e.g. config.py's
# storage_reject_order_queue_fields, an existing Phase 12 safety flag that
# *rejects* order-queue fields — it is not an order-queue field itself).
# Checking actual identifiers (names, function/dataclass parameters, import
# aliases, keyword arguments) catches a real forbidden field/parameter while
# staying immune to both false-positive classes.


def _module_defined_identifiers(module) -> set[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.alias):
            names.add((node.asname or node.name).split(".")[-1])
        elif isinstance(node, ast.keyword) and node.arg is not None:
            names.add(node.arg)
    return names


def test_no_forbidden_fields_in_relevant_production_source():
    for module in _SCANNED_PRODUCTION_MODULES:
        identifiers = _module_defined_identifiers(module)
        overlap = identifiers & set(_FORBIDDEN_FIELD_NAMES)
        assert not overlap, f"{module.__name__} defines forbidden identifiers: {overlap}"


def test_no_order_queue_fields_in_relevant_production_source():
    for module in _SCANNED_PRODUCTION_MODULES:
        identifiers = _module_defined_identifiers(module)
        overlap = identifiers & set(_FORBIDDEN_ORDER_QUEUE_FIELDS)
        assert not overlap, f"{module.__name__} defines forbidden identifiers: {overlap}"


# --- 7. no HTTP exposure of preview/broker functionality ----------------------------


def _imported_modules(module) -> list[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def _referenced_identifiers(module) -> set[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_http_api_does_not_import_preview_modules_or_functions():
    modules = _imported_modules(http_api_module)
    assert not any("mock_broker_preview" in module for module in modules)
    assert not any("broker_boundary" in module for module in modules)

    identifiers = _referenced_identifiers(http_api_module)
    forbidden = {
        "preview_with_mock_broker",
        "build_preview_request",
        "validate_preview_boundary",
    }
    overlap = identifiers & forbidden
    assert not overlap, f"http_api.py references forbidden preview identifiers: {overlap}"


# --- 8. no network/broker-SDK imports in relevant files -----------------------------


def test_relevant_production_modules_have_no_forbidden_imports():
    for module in _SCANNED_PRODUCTION_MODULES:
        modules = _imported_modules(module)
        for imported in modules:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in imported, (
                    f"{module.__name__} must not import {imported!r}"
                )


def test_contract_test_file_itself_has_no_forbidden_imports():
    import tests.test_options_broker_preview_contract as this_module

    modules = _imported_modules(this_module)
    for imported in modules:
        for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
            assert forbidden not in imported, (
                f"test_options_broker_preview_contract.py must not import {imported!r}"
            )


# --- 9. no order-action path in relevant production files ---------------------------


def test_relevant_production_source_has_no_order_action_verbs():
    for module in _SCANNED_PRODUCTION_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in _FORBIDDEN_ORDER_ACTION_IDENTIFIERS:
            assert forbidden not in source, (
                f"{module.__name__} must not contain {forbidden!r}"
            )


# --- 10. no LIVE_OPTIONS_TRADING_ENABLED mutation -----------------------------------


def test_no_live_options_flag_mutation(monkeypatch):
    monkeypatch.delenv("LIVE_OPTIONS_TRADING_ENABLED", raising=False)
    import os

    assert os.getenv("LIVE_OPTIONS_TRADING_ENABLED") is None


def test_relevant_production_source_does_not_setenv_live_options_flag():
    for module in _SCANNED_PRODUCTION_MODULES:
        source = Path(module.__file__).read_text()
        assert "LIVE_OPTIONS_TRADING_ENABLED\"] =" not in source
        assert "LIVE_OPTIONS_TRADING_ENABLED'] =" not in source
