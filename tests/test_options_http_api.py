"""
tests/test_options_http_api.py

Phase 15 inert read-only HTTP status API tests. Covers auth, response
redaction, read-only behavior, and structural safety boundaries for
options_manager/http_api.py. No broker, no HTTP client calls out, no writes.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import options_manager.http_api as http_api_module
from options_manager.config import OptionsManagerConfig
from options_manager.http_api import STATUS_SECRET_HEADER, create_options_status_app
from options_manager.human_confirm import ConfirmationRecord
from options_manager.order_ticket import PreparedOrderTicket
from options_manager.storage import (
    append_confirmation_consumed_event,
    append_ticket_created_event,
    init_options_storage,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
SECRET = "phase15-status-secret"


def _config(**overrides) -> OptionsManagerConfig:
    base = dict(http_status_secret=SECRET)
    base.update(overrides)
    return replace(OptionsManagerConfig(), **base)


def _confirmation_record(**overrides) -> ConfirmationRecord:
    base = dict(
        confirmation_id="conf-1",
        intent_id="intent-abc",
        reviewer="alice",
        approved=True,
        approval_text="CONFIRM DRY RUN ORDER PREP",
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=300),
        used_at=None,
        nonce="nonce-1",
    )
    base.update(overrides)
    return ConfirmationRecord(**base)


def _ticket(**overrides) -> PreparedOrderTicket:
    from datetime import date

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
        broker=None,
        broker_order_id=None,
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=120),
        warnings=[],
    )
    base.update(overrides)
    return PreparedOrderTicket(**base)


@pytest.fixture()
def db_path(tmp_path) -> str:
    path = str(tmp_path / "options_status_test.sqlite")
    init_options_storage(path, _config())
    return path


@pytest.fixture()
def seeded_db_path(db_path) -> str:
    append_confirmation_consumed_event(
        db_path, _confirmation_record(), "ticket-1", _config(), consumed_at=NOW
    )
    append_ticket_created_event(db_path, _ticket(), _config())
    return db_path


def _client(config: OptionsManagerConfig, db_path: str) -> TestClient:
    app = create_options_status_app(config, db_path=db_path)
    return TestClient(app)


# --- confirmation status -----------------------------------------------------------


def test_confirmation_status_found_with_correct_secret(seeded_db_path):
    client = _client(_config(), seeded_db_path)
    response = client.get(
        "/options/status/confirmation/conf-1",
        headers={STATUS_SECRET_HEADER: SECRET},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "FOUND"
    assert body["found"] is True
    assert body["confirmation_id"] == "conf-1"
    assert body["ticket_id"] == "ticket-1"


def test_confirmation_status_not_found_with_correct_secret(seeded_db_path):
    client = _client(_config(), seeded_db_path)
    response = client.get(
        "/options/status/confirmation/conf-never-written",
        headers={STATUS_SECRET_HEADER: SECRET},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "NOT_FOUND"
    assert body["found"] is False
    assert body["ticket_id"] is None


# --- ticket status ------------------------------------------------------------------


def test_ticket_status_found_with_correct_secret(seeded_db_path):
    client = _client(_config(), seeded_db_path)
    response = client.get(
        "/options/status/ticket/conf-1",
        headers={STATUS_SECRET_HEADER: SECRET},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "FOUND"
    assert body["found"] is True
    assert body["ticket_id"] == "ticket-1"


def test_ticket_status_not_found_with_correct_secret(seeded_db_path):
    client = _client(_config(), seeded_db_path)
    response = client.get(
        "/options/status/ticket/conf-never-written",
        headers={STATUS_SECRET_HEADER: SECRET},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "NOT_FOUND"
    assert body["found"] is False


# --- auth ----------------------------------------------------------------------------


def test_missing_secret_header_returns_401(seeded_db_path):
    client = _client(_config(), seeded_db_path)
    response = client.get("/options/status/confirmation/conf-1")
    assert response.status_code == 401


def test_wrong_secret_header_returns_401(seeded_db_path):
    client = _client(_config(), seeded_db_path)
    response = client.get(
        "/options/status/confirmation/conf-1",
        headers={STATUS_SECRET_HEADER: "wrong-secret"},
    )
    assert response.status_code == 401


def test_unset_configured_secret_returns_503(seeded_db_path):
    client = _client(_config(http_status_secret=""), seeded_db_path)
    response = client.get(
        "/options/status/confirmation/conf-1",
        headers={STATUS_SECRET_HEADER: SECRET},
    )
    assert response.status_code == 503


def test_ticket_endpoint_unset_configured_secret_returns_503(seeded_db_path):
    client = _client(_config(http_status_secret=""), seeded_db_path)
    response = client.get(
        "/options/status/ticket/conf-1",
        headers={STATUS_SECRET_HEADER: SECRET},
    )
    assert response.status_code == 503


def test_ticket_endpoint_missing_secret_header_returns_401(seeded_db_path):
    client = _client(_config(), seeded_db_path)
    response = client.get("/options/status/ticket/conf-1")
    assert response.status_code == 401


def test_ticket_endpoint_wrong_secret_header_returns_401(seeded_db_path):
    client = _client(_config(), seeded_db_path)
    response = client.get(
        "/options/status/ticket/conf-1",
        headers={STATUS_SECRET_HEADER: "wrong-secret"},
    )
    assert response.status_code == 401


# --- response redaction ---------------------------------------------------------------


_ALLOWED_FIELDS = {
    "status",
    "found",
    "reason",
    "failed_stage",
    "confirmation_id",
    "ticket_id",
    "submitted",
    "executable",
    "broker",
    "broker_order_id",
    "warnings",
}

_FORBIDDEN_RESPONSE_KEYS = (
    "approval_text",
    "nonce",
    "reviewer",
    "credentials",
    "account_number",
    "live_account",
    "broker_payload",
    "route_to_broker",
    "pending_submit",
    "ready_to_submit",
    "auto_submit",
    "execute_after",
    "order_queue",
    "intent_id",
    "consumed_at",
    "created_at",
    "expires_at",
)


def test_response_body_uses_allowlisted_fields_only(seeded_db_path):
    client = _client(_config(), seeded_db_path)
    response = client.get(
        "/options/status/confirmation/conf-1",
        headers={STATUS_SECRET_HEADER: SECRET},
    )
    body = response.json()
    assert set(body.keys()) == _ALLOWED_FIELDS


def test_response_never_includes_approval_text_nonce_reviewer(seeded_db_path):
    client = _client(_config(), seeded_db_path)
    for path in ("/options/status/confirmation/conf-1", "/options/status/ticket/conf-1"):
        response = client.get(path, headers={STATUS_SECRET_HEADER: SECRET})
        body = response.json()
        for forbidden_key in _FORBIDDEN_RESPONSE_KEYS:
            assert forbidden_key not in body, f"{path} response leaked {forbidden_key!r}"
        assert "CONFIRM DRY RUN ORDER PREP" not in response.text
        assert "alice" not in response.text
        assert "nonce-1" not in response.text


def test_response_never_includes_order_queue_fields(seeded_db_path):
    client = _client(_config(), seeded_db_path)
    forbidden_fields = (
        "pending_submit",
        "ready_to_submit",
        "auto_submit",
        "execute_after",
        "order_queue",
        "route_to_broker",
        "broker_payload",
        "credentials",
        "account_number",
        "live_account",
    )
    for path in ("/options/status/confirmation/conf-1", "/options/status/ticket/conf-1"):
        response = client.get(path, headers={STATUS_SECRET_HEADER: SECRET})
        for forbidden in forbidden_fields:
            assert forbidden not in response.text


def test_response_always_forces_inert_execution_fields(seeded_db_path):
    client = _client(_config(), seeded_db_path)
    for path in ("/options/status/confirmation/conf-1", "/options/status/ticket/conf-1"):
        response = client.get(path, headers={STATUS_SECRET_HEADER: SECRET})
        body = response.json()
        assert body["submitted"] is False
        assert body["executable"] is False
        assert body["broker"] is None
        assert body["broker_order_id"] is None


# --- structural safety (AST-based) ---------------------------------------------------


def _http_api_ast():
    path = Path(http_api_module.__file__)
    return ast.parse(path.read_text())


def _http_api_imported_modules() -> list[str]:
    tree = _http_api_ast()
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def _http_api_referenced_identifiers() -> set[str]:
    tree = _http_api_ast()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_http_api_has_no_forbidden_imports():
    modules = _http_api_imported_modules()
    forbidden_module_fragments = (
        "execution",
        "webhook",
        "risk_engine",
        "alert_ranker",
        "httpx",
        "requests",
        "urllib",
        "socket",
        "live_lock",
    )
    for module in modules:
        for forbidden in forbidden_module_fragments:
            assert forbidden not in module, f"http_api.py must not import {module!r}"


def test_http_api_does_not_reference_write_or_broker_functions():
    identifiers = _http_api_referenced_identifiers()
    forbidden = {
        "append_confirmation_consumed_event",
        "append_ticket_created_event",
        "confirm_order_intent",
        "build_order_ticket",
        "build_preview_request",
        "validate_preview_boundary",
        "place_order",
        "submit_order",
        "cancel_order",
        "preview_order",
        "assert_live_options_trading_disabled",
    }
    overlap = identifiers & forbidden
    assert not overlap, f"http_api.py references forbidden identifiers: {overlap}"


def test_http_api_does_not_mutate_live_options_flag(monkeypatch):
    monkeypatch.delenv("LIVE_OPTIONS_TRADING_ENABLED", raising=False)
    import os

    assert os.getenv("LIVE_OPTIONS_TRADING_ENABLED") is None


def test_http_api_has_no_write_routes(seeded_db_path):
    app = create_options_status_app(_config(), db_path=seeded_db_path)
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if not methods:
            continue
        forbidden_methods = methods & {"POST", "PUT", "PATCH", "DELETE"}
        assert not forbidden_methods, (
            f"route {getattr(route, 'path', route)!r} exposes forbidden "
            f"methods {forbidden_methods}"
        )


def test_http_api_only_has_get_routes(seeded_db_path):
    app = create_options_status_app(_config(), db_path=seeded_db_path)
    all_methods: set[str] = set()
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if methods:
            all_methods |= methods
    assert all_methods <= {"GET", "HEAD"}
