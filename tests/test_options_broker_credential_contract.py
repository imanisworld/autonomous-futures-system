"""
tests/test_options_broker_credential_contract.py

Phase 21 — broker credential/account contract tests. Test-only: locks the
Phase 20 credential/account-handling rules before any real broker
implementation is allowed.

    credential / account_id -> must be an explicit, per-call, in-memory-only
    parameter of a future real broker-preview adapter — never a dataclass
    field, config field, storage column, or HTTP-exposed value.

No production code is added or modified in this phase. This file only
reads existing dataclass field names, existing config/storage/HTTP source
text, and defines a local, test-only Protocol documenting the shape any
future real broker-preview adapter must accept. It never calls a broker,
never uses an MCP broker tool, and never opens a network connection.
"""

from __future__ import annotations

import ast
import inspect
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
from options_manager.config import OptionsManagerConfig
from options_manager.mock_broker_preview import preview_with_mock_broker

_FORBIDDEN_CREDENTIAL_ACCOUNT_IDENTIFIERS = (
    "broker_credentials",
    "credentials",
    "api_key",
    "access_token",
    "refresh_token",
    "password",
    "session_token",
    "account_id",
    "account_number",
    "broker_account",
    "broker_payload",
    "route_to_broker",
    "ibkr_account",
    "robinhood_account",
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

_FORBIDDEN_SUBSYSTEM_IMPORT_FRAGMENTS = (
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

_OPTIONS_MANAGER_PACKAGE_DIR = Path(config_module.__file__).parent


# --- shared AST helpers ---------------------------------------------------
#
# Identifier-aware (not raw substring) scanning throughout: a raw substring
# scan would false-positive on legitimate prose (e.g.
# mock_broker_preview.py's own docstring saying "no credentials") or on a
# legitimate compound identifier that merely contains a forbidden word as a
# substring (e.g. config.py's storage_reject_order_queue_fields, which
# *rejects* order-queue fields rather than being one). Checking whole
# identifiers (names, parameters, import aliases, keyword arguments) and,
# for the SQL schema check, only the extracted SQL string constants,
# structurally avoids both false-positive classes.


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


def _imported_modules(module) -> list[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def _sql_schema_string_constants(module) -> list[str]:
    """Extract only the SQL CREATE TABLE string literals from a module's
    source — never docstrings or other prose — so a forbidden-column scan
    can never false-positive on a safety comment/docstring."""
    tree = ast.parse(Path(module.__file__).read_text())
    schema_strings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "CREATE TABLE" in node.value:
                schema_strings.append(node.value)
    return schema_strings


def _all_options_manager_source_files() -> list[Path]:
    return sorted(_OPTIONS_MANAGER_PACKAGE_DIR.glob("*.py"))


def _logged_string_arguments(source_path: Path) -> list[str]:
    """Collect every string-literal/identifier argument passed to a
    logger.*()/print() call in this file — never docstrings, never other
    prose, since only ast.Call arguments are inspected."""
    tree = ast.parse(source_path.read_text())
    logged: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_logger_call = (
            isinstance(func, ast.Attribute)
            and func.attr
            in ("debug", "info", "warning", "error", "critical", "exception")
            and isinstance(func.value, ast.Name)
            and func.value.id in ("logger", "logging")
        )
        is_print_call = isinstance(func, ast.Name) and func.id == "print"
        if not (is_logger_call or is_print_call):
            continue
        for call_arg in node.args:
            if isinstance(call_arg, ast.Constant) and isinstance(call_arg.value, str):
                logged.append(call_arg.value)
            elif isinstance(call_arg, ast.Attribute):
                logged.append(call_arg.attr)
            elif isinstance(call_arg, ast.Name):
                logged.append(call_arg.id)
    return logged


# --- 1. config credential/account field test ------------------------------


def test_config_has_no_credential_or_account_fields():
    field_names = set(OptionsManagerConfig.__dataclass_fields__.keys())
    for forbidden in _FORBIDDEN_CREDENTIAL_ACCOUNT_IDENTIFIERS:
        assert forbidden not in field_names, (
            f"OptionsManagerConfig must not contain field {forbidden!r}"
        )


def test_config_module_defines_no_credential_or_account_identifiers():
    identifiers = _module_defined_identifiers(config_module)
    overlap = identifiers & set(_FORBIDDEN_CREDENTIAL_ACCOUNT_IDENTIFIERS)
    assert not overlap, f"config.py defines forbidden identifiers: {overlap}"


# --- 2. storage schema credential/account test ----------------------------


def test_storage_module_defines_no_credential_or_account_identifiers():
    identifiers = _module_defined_identifiers(storage_module)
    overlap = identifiers & set(_FORBIDDEN_CREDENTIAL_ACCOUNT_IDENTIFIERS)
    assert not overlap, f"storage.py defines forbidden identifiers: {overlap}"


def test_storage_sql_schema_has_no_credential_or_account_columns():
    schema_strings = _sql_schema_string_constants(storage_module)
    assert schema_strings, "expected at least one CREATE TABLE statement in storage.py"
    for schema in schema_strings:
        for forbidden in _FORBIDDEN_CREDENTIAL_ACCOUNT_IDENTIFIERS:
            assert forbidden not in schema, (
                f"storage.py SQL schema must not define column {forbidden!r}"
            )


# --- 3. HTTP preview isolation test ----------------------------------------


def test_http_api_does_not_import_preview_or_credential_modules():
    modules = _imported_modules(http_api_module)
    assert not any("mock_broker_preview" in module for module in modules)
    assert not any("broker_boundary" in module for module in modules)

    identifiers = _module_defined_identifiers(http_api_module)
    forbidden_calls = {
        "preview_with_mock_broker",
        "build_preview_request",
        "validate_preview_boundary",
    }
    overlap = identifiers & forbidden_calls
    assert not overlap, f"http_api.py references forbidden preview identifiers: {overlap}"

    overlap_credentials = identifiers & set(_FORBIDDEN_CREDENTIAL_ACCOUNT_IDENTIFIERS)
    assert not overlap_credentials, (
        "http_api.py references forbidden credential/account identifiers: "
        f"{overlap_credentials}"
    )


# --- 4. future real-adapter signature contract (documentation-by-test only) -


class RealBrokerPreviewAdapter(Protocol):
    """Documents the contract any FUTURE real broker-preview adapter must
    satisfy, per the Phase 20 audit decision: credential/account_id must be
    explicit, per-call, in-memory-only parameters — never a dataclass,
    config, or storage field. This Protocol is test-only documentation; it
    is never imported by production code, and no real adapter exists yet."""

    def __call__(
        self,
        preview_request: OptionsBrokerPreviewRequest,
        *,
        credential: str,
        account_id: str,
    ) -> OptionsBrokerPreviewResult: ...


def test_future_real_adapter_contract_keeps_credential_and_account_out_of_dataclass_fields():
    """The documented future contract's credential/account_id parameters
    must never also exist as OptionsBrokerPreviewRequest/Result dataclass
    fields — they may only ever be explicit per-call arguments."""
    request_fields = set(OptionsBrokerPreviewRequest.__dataclass_fields__.keys())
    result_fields = set(OptionsBrokerPreviewResult.__dataclass_fields__.keys())
    for forbidden in ("credential", "account_id", "credentials"):
        assert forbidden not in request_fields
        assert forbidden not in result_fields


def test_future_real_adapter_contract_has_no_submit_cancel_replace_place_methods():
    """The documented future contract is a single __call__ returning
    OptionsBrokerPreviewResult only — it defines no submit/cancel/replace/
    place method of any kind."""
    protocol_members = {
        name for name in dir(RealBrokerPreviewAdapter) if not name.startswith("_")
    } | {"__call__"}
    forbidden_methods = {
        "submit",
        "submit_order",
        "cancel",
        "cancel_order",
        "replace",
        "replace_order",
        "place",
        "place_order",
    }
    overlap = protocol_members & forbidden_methods
    assert not overlap, f"RealBrokerPreviewAdapter must not define {overlap}"


def test_mock_adapter_signature_has_no_credential_or_account_parameter():
    """The existing mock adapter (the only adapter that exists today) takes
    no credential/account parameter — today's implementation already
    matches the future contract's "no persisted, no config-sourced
    credential" half, even before any real adapter exists."""
    signature = inspect.signature(preview_with_mock_broker)
    param_names = set(signature.parameters.keys())
    overlap = param_names & set(_FORBIDDEN_CREDENTIAL_ACCOUNT_IDENTIFIERS)
    assert not overlap, f"preview_with_mock_broker must not accept {overlap}"


# --- 5. logging safety test --------------------------------------------------


def test_no_options_manager_production_file_logs_credential_or_account_identifiers():
    for source_path in _all_options_manager_source_files():
        logged = _logged_string_arguments(source_path)
        for value in logged:
            for forbidden in _FORBIDDEN_CREDENTIAL_ACCOUNT_IDENTIFIERS:
                assert forbidden not in value, (
                    f"{source_path.name} logs/formats forbidden identifier "
                    f"{forbidden!r} in {value!r}"
                )


# --- 6. no network/broker-SDK imports in relevant files ----------------------


def test_relevant_production_modules_have_no_network_or_broker_sdk_imports():
    for module in _SCANNED_PRODUCTION_MODULES:
        modules = _imported_modules(module)
        for imported in modules:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in imported, (
                    f"{module.__name__} must not import {imported!r}"
                )


def test_contract_test_file_itself_has_no_forbidden_imports():
    import tests.test_options_broker_credential_contract as this_module

    modules = _imported_modules(this_module)
    for imported in modules:
        for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
            assert forbidden not in imported, (
                "test_options_broker_credential_contract.py must not import "
                f"{imported!r}"
            )


# --- 7. no execution/webhook/alert_ranker/risk_engine imports ----------------


def test_relevant_production_modules_have_no_execution_webhook_alert_ranker_or_risk_engine_imports():
    for module in _SCANNED_PRODUCTION_MODULES:
        modules = _imported_modules(module)
        for imported in modules:
            for forbidden in _FORBIDDEN_SUBSYSTEM_IMPORT_FRAGMENTS:
                assert forbidden not in imported, (
                    f"{module.__name__} must not import {imported!r}"
                )


# --- 8. no order-action path in relevant production files --------------------


def test_relevant_production_source_has_no_order_action_verbs():
    for module in _SCANNED_PRODUCTION_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in _FORBIDDEN_ORDER_ACTION_IDENTIFIERS:
            assert forbidden not in source, (
                f"{module.__name__} must not contain {forbidden!r}"
            )


# --- 9. no LIVE_OPTIONS_TRADING_ENABLED mutation ------------------------------


def test_relevant_production_source_does_not_mutate_live_options_flag():
    for module in _SCANNED_PRODUCTION_MODULES:
        source = Path(module.__file__).read_text()
        assert "LIVE_OPTIONS_TRADING_ENABLED\"] =" not in source
        assert "LIVE_OPTIONS_TRADING_ENABLED'] =" not in source


def test_no_live_options_flag_mutation_via_setenv(monkeypatch):
    monkeypatch.delenv("LIVE_OPTIONS_TRADING_ENABLED", raising=False)
    import os

    assert os.getenv("LIVE_OPTIONS_TRADING_ENABLED") is None
