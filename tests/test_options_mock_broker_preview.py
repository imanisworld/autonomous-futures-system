"""
tests/test_options_mock_broker_preview.py

Phase 17 mock broker preview adapter tests. Proves the mock adapter is
fully inert: no real broker, no credentials, no account data, no HTTP/
network, no storage writes, no submit/cancel/replace path — a local,
deterministic, canned preview result only.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import options_manager.mock_broker_preview as mock_module
from options_manager.broker_boundary import OptionsBrokerPreviewRequest
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


# --- happy path ----------------------------------------------------------------------


def test_mock_adapter_accepts_valid_preview_request():
    result = preview_with_mock_broker(_preview_request())
    assert result.preview_ready is True
    assert result.status == "PREVIEW_READY"
    assert result.ticket_id == "ticket-1"


def test_mock_adapter_returns_submitted_false():
    result = preview_with_mock_broker(_preview_request())
    assert result.submitted is False


def test_mock_adapter_returns_executable_false():
    result = preview_with_mock_broker(_preview_request())
    assert result.executable is False


def test_mock_adapter_returns_broker_order_id_none():
    result = preview_with_mock_broker(_preview_request())
    assert result.broker_order_id is None


def test_mock_adapter_does_not_return_a_real_broker_name():
    result = preview_with_mock_broker(_preview_request())
    assert result.broker == MOCK_BROKER_LABEL
    real_broker_names = {"robinhood", "ibkr", "interactive brokers", "tradovate"}
    assert result.broker.strip().lower() not in real_broker_names


def test_mock_adapter_includes_mock_only_warning():
    result = preview_with_mock_broker(_preview_request())
    assert "mock_preview_only" in result.warnings
    assert "not_a_broker_order" in result.warnings


# --- rejection paths -------------------------------------------------------------------


def test_mock_adapter_rejects_executable_request():
    result = preview_with_mock_broker(_preview_request(executable=True))
    assert result.status == "REJECTED"
    assert result.failed_stage == "executable"
    assert result.preview_ready is False


def test_mock_adapter_rejects_non_dry_run_request():
    result = preview_with_mock_broker(_preview_request(dry_run_only=False))
    assert result.status == "REJECTED"
    assert result.failed_stage == "dry_run_only"


def test_mock_adapter_rejects_missing_ticket_id():
    result = preview_with_mock_broker(_preview_request(ticket_id=""))
    assert result.status == "DATA_BLOCKED"
    assert result.failed_stage == "ticket_id"


def test_mock_adapter_rejects_missing_confirmation_id():
    result = preview_with_mock_broker(_preview_request(confirmation_id=""))
    assert result.status == "DATA_BLOCKED"
    assert result.failed_stage == "confirmation_id"


def test_preview_request_has_no_submitted_or_broker_order_id_fields():
    """OptionsBrokerPreviewRequest (Phase 9) has no submitted/broker_order_id
    fields at all — there is nothing for a caller to set that would need
    rejecting here; this test makes that structural fact explicit."""
    field_names = set(OptionsBrokerPreviewRequest.__dataclass_fields__.keys())
    assert "submitted" not in field_names
    assert "broker_order_id" not in field_names


# --- redaction / forbidden fields --------------------------------------------------------


def test_mock_result_never_contains_forbidden_fields():
    from options_manager.broker_boundary import OptionsBrokerPreviewResult

    field_names = set(OptionsBrokerPreviewResult.__dataclass_fields__.keys())
    for forbidden in ("account_number", "credentials", "broker_payload", "route_to_broker"):
        assert forbidden not in field_names


def test_mock_result_does_not_include_broker_confirmation_id_field():
    result = preview_with_mock_broker(_preview_request())
    assert not hasattr(result, "broker_confirmation_id")
    assert not hasattr(result, "account_number")
    assert not hasattr(result, "credentials")


# --- structural safety (AST-based) --------------------------------------------------------


def _mock_module_ast():
    path = Path(mock_module.__file__)
    return ast.parse(path.read_text())


def _mock_module_imported_modules() -> list[str]:
    tree = _mock_module_ast()
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def _mock_module_referenced_identifiers() -> set[str]:
    tree = _mock_module_ast()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_mock_adapter_has_no_forbidden_imports():
    modules = _mock_module_imported_modules()
    forbidden_module_fragments = (
        "execution",
        "webhook",
        "alert_ranker",
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
        "live_lock",
        "storage",
        "http_api",
    )
    for module in modules:
        for forbidden in forbidden_module_fragments:
            assert forbidden not in module, (
                f"mock_broker_preview.py must not import {module!r}"
            )


def test_mock_adapter_has_no_forbidden_identifiers():
    identifiers = {name.lower() for name in _mock_module_referenced_identifiers()}
    forbidden = {
        "place_order",
        "submit_order",
        "cancel_order",
        "replace_order",
        "execute_order",
        "live_order",
        "broker_payload",
        "route_to_broker",
        "account_number",
        "credentials",
        "api_key",
        "access_token",
        "refresh_token",
        "password",
        "session_token",
        "robinhood",
        "ibkr",
        "tradovate",
        "assert_live_options_trading_disabled",
        "append_confirmation_consumed_event",
        "append_ticket_created_event",
    }
    overlap = identifiers & forbidden
    assert not overlap, f"mock_broker_preview.py references forbidden identifiers: {overlap}"


def test_mock_adapter_source_has_no_forbidden_substrings():
    source = Path(mock_module.__file__).read_text()
    for forbidden in (
        "robin_stocks",
        "ib_insync",
        "ibapi",
        "api_key",
        "access_token",
        "refresh_token",
        "password",
        "session_token",
        "account_number",
    ):
        assert forbidden not in source, (
            f"mock_broker_preview.py must not contain {forbidden!r}"
        )


def test_mock_adapter_does_not_mutate_live_options_flag(monkeypatch):
    monkeypatch.delenv("LIVE_OPTIONS_TRADING_ENABLED", raising=False)
    import os

    assert os.getenv("LIVE_OPTIONS_TRADING_ENABLED") is None


def test_mock_adapter_does_not_reference_storage_write_functions():
    identifiers = _mock_module_referenced_identifiers()
    forbidden = {
        "append_confirmation_consumed_event",
        "append_ticket_created_event",
        "init_options_storage",
    }
    overlap = identifiers & forbidden
    assert not overlap, f"mock_broker_preview.py references storage writes: {overlap}"


def test_mock_adapter_does_not_reference_http_api_module():
    modules = _mock_module_imported_modules()
    assert not any("http_api" in module for module in modules)
    identifiers = _mock_module_referenced_identifiers()
    assert "create_options_status_app" not in identifiers
