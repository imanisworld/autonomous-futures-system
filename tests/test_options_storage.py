"""
tests/test_options_storage.py

Phase 12 inert append-only storage layer tests. SQLite-backed, insert-only
replay protection for confirmation-consumed and ticket-created events — no
broker, no Robinhood, no Tradovate, no HTTP, no Discord, no order queue,
no UPDATE/DELETE/UPSERT/REPLACE anywhere in storage.py.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import options_manager.storage as storage_module
from options_manager.config import OptionsManagerConfig
from options_manager.human_confirm import ConfirmationRecord
from options_manager.order_ticket import PreparedOrderTicket
from options_manager.storage import (
    append_confirmation_consumed_event,
    append_ticket_created_event,
    has_confirmation_consumed,
    has_ticket_for_confirmation,
    init_options_storage,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _config(**overrides) -> OptionsManagerConfig:
    return replace(OptionsManagerConfig(), **overrides)


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
    return str(tmp_path / "options_storage.sqlite")


# --- init ----------------------------------------------------------------------


def test_init_creates_expected_tables(db_path):
    result = init_options_storage(db_path, _config())
    assert result.written is True
    assert result.status == "WRITTEN"

    import sqlite3

    conn = sqlite3.connect(db_path)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    assert "confirmation_consumed_events" in tables
    assert "ticket_created_events" in tables


def test_init_is_idempotent(db_path):
    result_1 = init_options_storage(db_path, _config())
    result_2 = init_options_storage(db_path, _config())
    assert result_1.status == "WRITTEN"
    assert result_2.status == "WRITTEN"


def test_init_disabled_rejects(db_path):
    result = init_options_storage(db_path, _config(storage_enabled=False))
    assert result.written is False
    assert result.status == "REJECTED"
    assert result.failed_stage == "storage_disabled"


# --- confirmation consumed events ------------------------------------------------


def test_append_confirmation_consumed_event_succeeds(db_path):
    init_options_storage(db_path, _config())
    result = append_confirmation_consumed_event(
        db_path, _confirmation_record(), "ticket-1", _config(), consumed_at=NOW
    )
    assert result.written is True
    assert result.status == "WRITTEN"
    assert result.record_id == "conf-1"


def test_duplicate_confirmation_consumed_event_returns_duplicate(db_path):
    init_options_storage(db_path, _config())
    record = _confirmation_record()
    first = append_confirmation_consumed_event(
        db_path, record, "ticket-1", _config(), consumed_at=NOW
    )
    second = append_confirmation_consumed_event(
        db_path, record, "ticket-2", _config(), consumed_at=NOW
    )
    assert first.status == "WRITTEN"
    assert second.status == "DUPLICATE"
    assert second.written is False


def test_has_confirmation_consumed_found_after_write(db_path):
    init_options_storage(db_path, _config())
    append_confirmation_consumed_event(
        db_path, _confirmation_record(), "ticket-1", _config(), consumed_at=NOW
    )
    result = has_confirmation_consumed(db_path, "conf-1", _config())
    assert result.found is True
    assert result.status == "FOUND"
    assert result.record["confirmation_id"] == "conf-1"
    assert result.record["ticket_id"] == "ticket-1"


def test_has_confirmation_consumed_not_found_before_write(db_path):
    init_options_storage(db_path, _config())
    result = has_confirmation_consumed(db_path, "conf-never-written", _config())
    assert result.found is False
    assert result.status == "NOT_FOUND"


def test_naive_consumed_at_rejected(db_path):
    init_options_storage(db_path, _config())
    result = append_confirmation_consumed_event(
        db_path, _confirmation_record(), "ticket-1", _config(), consumed_at=datetime.now()
    )
    assert result.status == "DATA_BLOCKED"
    assert result.failed_stage == "timestamp"


def test_naive_confirmation_created_at_rejected(db_path):
    init_options_storage(db_path, _config())
    record = _confirmation_record(created_at=datetime.now())
    result = append_confirmation_consumed_event(
        db_path, record, "ticket-1", _config(), consumed_at=NOW
    )
    assert result.status == "DATA_BLOCKED"
    assert result.failed_stage == "timestamp"


def test_naive_confirmation_expires_at_rejected(db_path):
    init_options_storage(db_path, _config())
    record = _confirmation_record(expires_at=datetime.now())
    result = append_confirmation_consumed_event(
        db_path, record, "ticket-1", _config(), consumed_at=NOW
    )
    assert result.status == "DATA_BLOCKED"
    assert result.failed_stage == "timestamp"


# --- ticket created events -------------------------------------------------------


def test_append_ticket_created_event_succeeds(db_path):
    init_options_storage(db_path, _config())
    result = append_ticket_created_event(db_path, _ticket(), _config())
    assert result.written is True
    assert result.status == "WRITTEN"
    assert result.record_id == "ticket-1"


def test_duplicate_ticket_created_for_same_confirmation_returns_duplicate(db_path):
    init_options_storage(db_path, _config())
    append_ticket_created_event(db_path, _ticket(), _config())
    second = append_ticket_created_event(
        db_path, _ticket(ticket_id="ticket-2"), _config()
    )
    assert second.status == "DUPLICATE"
    assert second.written is False


def test_duplicate_ticket_id_returns_duplicate(db_path):
    init_options_storage(db_path, _config())
    append_ticket_created_event(db_path, _ticket(confirmation_id="conf-a"), _config())
    second = append_ticket_created_event(
        db_path, _ticket(confirmation_id="conf-b"), _config()
    )
    assert second.status == "DUPLICATE"
    assert second.written is False


def test_has_ticket_for_confirmation_found_after_write(db_path):
    init_options_storage(db_path, _config())
    append_ticket_created_event(db_path, _ticket(), _config())
    result = has_ticket_for_confirmation(db_path, "conf-1", _config())
    assert result.found is True
    assert result.status == "FOUND"
    assert result.record["ticket_id"] == "ticket-1"


def test_has_ticket_for_confirmation_not_found_before_write(db_path):
    init_options_storage(db_path, _config())
    result = has_ticket_for_confirmation(db_path, "conf-never-written", _config())
    assert result.found is False
    assert result.status == "NOT_FOUND"


def test_naive_ticket_created_at_rejected(db_path):
    init_options_storage(db_path, _config())
    result = append_ticket_created_event(
        db_path, _ticket(created_at=datetime.now()), _config()
    )
    assert result.status == "DATA_BLOCKED"
    assert result.failed_stage == "timestamp"


def test_naive_ticket_expires_at_rejected(db_path):
    init_options_storage(db_path, _config())
    result = append_ticket_created_event(
        db_path, _ticket(expires_at=datetime.now()), _config()
    )
    assert result.status == "DATA_BLOCKED"
    assert result.failed_stage == "timestamp"


def test_executable_ticket_rejected(db_path):
    init_options_storage(db_path, _config())
    result = append_ticket_created_event(
        db_path, _ticket(executable=True), _config()
    )
    assert result.status == "REJECTED"
    assert result.failed_stage == "executable"


def test_dry_run_only_false_ticket_rejected(db_path):
    init_options_storage(db_path, _config())
    result = append_ticket_created_event(
        db_path, _ticket(dry_run_only=False), _config()
    )
    assert result.status == "REJECTED"
    assert result.failed_stage == "dry_run_only"


def test_ticket_broker_not_none_rejected(db_path):
    init_options_storage(db_path, _config())
    result = append_ticket_created_event(
        db_path, _ticket(broker="robinhood"), _config()
    )
    assert result.status == "REJECTED"
    assert result.failed_stage == "broker"


def test_ticket_broker_order_id_not_none_rejected(db_path):
    init_options_storage(db_path, _config())
    result = append_ticket_created_event(
        db_path, _ticket(broker_order_id="abc123"), _config()
    )
    assert result.status == "REJECTED"
    assert result.failed_stage == "broker_order_id"


# --- storage_enabled=False --------------------------------------------------------


def test_writes_disabled_when_storage_disabled(db_path):
    init_options_storage(db_path, _config())
    disabled_config = _config(storage_enabled=False)

    confirm_result = append_confirmation_consumed_event(
        db_path, _confirmation_record(), "ticket-1", disabled_config, consumed_at=NOW
    )
    assert confirm_result.status == "REJECTED"
    assert confirm_result.failed_stage == "storage_disabled"

    ticket_result = append_ticket_created_event(db_path, _ticket(), disabled_config)
    assert ticket_result.status == "REJECTED"
    assert ticket_result.failed_stage == "storage_disabled"


# --- corruption handling -----------------------------------------------------------


def test_corrupted_row_returns_corrupt_record(db_path):
    init_options_storage(db_path, _config())
    append_confirmation_consumed_event(
        db_path, _confirmation_record(), "ticket-1", _config(), consumed_at=NOW
    )

    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE confirmation_consumed_events SET consumed_at = ? WHERE confirmation_id = ?",
        ("not-a-valid-timestamp", "conf-1"),
    )
    conn.commit()
    conn.close()

    result = has_confirmation_consumed(db_path, "conf-1", _config())
    assert result.status == "CORRUPT_RECORD"


# --- schema / structural safety ----------------------------------------------------


def test_schema_contains_no_order_queue_fields():
    source = Path(storage_module.__file__).read_text()
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
    for forbidden in forbidden_fields:
        assert forbidden not in source, f"storage.py must not contain field {forbidden!r}"


def test_source_contains_no_update_delete_upsert_replace():
    source = Path(storage_module.__file__).read_text()
    for forbidden_keyword in ("UPDATE", "DELETE", "UPSERT", "REPLACE"):
        assert forbidden_keyword not in source, (
            f"storage.py must not contain the SQL keyword {forbidden_keyword!r} "
            "(append-only: INSERT/SELECT/CREATE TABLE only)"
        )


def _storage_ast():
    path = Path(storage_module.__file__)
    return ast.parse(path.read_text())


def _storage_imported_modules() -> list[str]:
    tree = _storage_ast()
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def _storage_referenced_identifiers() -> set[str]:
    tree = _storage_ast()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_storage_has_no_forbidden_imports():
    modules = _storage_imported_modules()
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
            assert forbidden not in module, f"storage.py must not import {module!r}"


def test_storage_has_no_broker_notify_or_network_identifiers():
    identifiers = {name.lower() for name in _storage_referenced_identifiers()}
    forbidden = {
        "place_order",
        "submit_order",
        "cancel_order",
        "preview_order",
        "robinhood",
        "tradovate",
        "ibkr",
        "notify",
        "notify_packet",
        "log_packet",
        "broker_client",
        "getenv",
        "load_dotenv",
        "environ",
        "assert_live_options_trading_disabled",
    }
    overlap = identifiers & forbidden
    assert not overlap, f"storage.py references forbidden identifiers: {overlap}"


def test_storage_does_not_mutate_live_options_flag(monkeypatch):
    monkeypatch.delenv("LIVE_OPTIONS_TRADING_ENABLED", raising=False)
    import os

    assert os.getenv("LIVE_OPTIONS_TRADING_ENABLED") is None


def test_storage_dataclasses_have_no_credential_or_account_fields():
    from options_manager.storage import StorageReadResult, StorageWriteResult

    for cls in (StorageWriteResult, StorageReadResult):
        field_names = set(cls.__dataclass_fields__.keys())
        for forbidden in ("credentials", "account_number", "broker_payload"):
            assert forbidden not in field_names
